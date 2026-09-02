# Admission-webhook drift: CREATE-only rules never re-run on existing objects

**Class of bug, not a single incident.** Any object whose current spec was written by a
*mutating admission webhook* keeps that mutation only if the webhook's rule still matches
future requests against it. A webhook rule scoped to `operations: [CREATE]` only ever fires
once, at admission time. If the object is never deleted and recreated after that, it silently
keeps whatever the webhook produced (or didn't produce) on day one — permanently, and with no
error, no alert, and no drifted-looking status anywhere.

This is a variant of the "fixed at admission time" hazard already documented for
`postBuild.substitute` collisions and `fsGroup`/SSA field-ownership drift elsewhere in this
repo (see the root `CLAUDE.md` NOTES section) — but it is specifically about admission
*webhooks*, which is a different mechanism from both of those and needed its own sweep.

## The concrete case: k8tz and CronJob timezones

`system-controller/k8tz` (`MutatingWebhookConfiguration/k8tz`, created
`2025-09-11T23:58:22Z`) stamps every `pods` and `cronjobs` CREATE request in every
non-excluded namespace with `TZ=America/New_York` (pods) or `spec.timeZone:
America/New_York` (CronJobs, gated by the chart's `cronJobTimeZone: true`). Both webhook
rules are `operations: [CREATE]` only — confirmed by reading the live object, not inferred:

```
kubectl get mutatingwebhookconfiguration k8tz -o json | jq '.webhooks[].rules'
[
  {"apiGroups":[""],     "resources":["pods"],     "operations":["CREATE"]},
  {"apiGroups":["batch"],"resources":["cronjobs"], "operations":["CREATE"]}
]
```

Two CronJobs were found running on the wrong timezone as a result:

| CronJob | Created | Webhook existed since | Gap |
|---|---|---|---|
| `system/fstrim` | 2025-09-11T23:57:52Z | 2025-09-11T23:58:22Z | created **30 seconds before** the webhook |
| `downloads/reading-glasses-cache-prune` | 2026-04-26T18:48:43Z | (pre-existing) | never recreated since — see "A second, independent way to lose it" below |

Both fired at `00:00 UTC` (20:00 EDT Sunday / 19:00 EST Sunday) instead of local midnight, and
`03:00 UTC` instead of local 03:00, for their entire lives, with `flux get ks`, `flux get hr`,
and every probe reporting healthy the whole time.

### Detection technique

Compare `metadata.creationTimestamp` on the candidate object against the webhook's own
`metadata.creationTimestamp`, then check whether the webhook's expected mutation is actually
present on the object:

```bash
kubectl get mutatingwebhookconfiguration k8tz -o jsonpath='{.metadata.creationTimestamp}'
kubectl get cronjob -A -o custom-columns=\
  'NAMESPACE:.metadata.namespace,NAME:.metadata.name,TZ:.spec.timeZone,CREATED:.metadata.creationTimestamp'
```

A `<none>` in the `TZ` column is the signal — it means either the object predates the
webhook, or (see below) something stripped the field afterward. `metadata.creationTimestamp`
before the webhook's own `creationTimestamp` proves the first case directly. Neither
`flux get ks/hr`, `flate`, nor any probe or health check surfaces this — the object is
`Ready`/`Applied` by every GitOps signal because Git never declared the field in the first
place; there is nothing for Flux to detect as missing.

### The fix, and why "recreate it" needed a durability check

Per the standing rule for this class of drift: **never hand-patch `spec.timeZone`** — that
edits around the symptom without fixing why the webhook didn't fire, and manually-set fields
are exactly the kind of drift a later reconcile may or may not clear (see the `fsGroup`/SSA
entry in the root `CLAUDE.md`). The correct fix is to delete the object and let Flux recreate
it, so the CREATE request goes through the webhook fresh.

In practice, deletion alone was not enough:

- `system/fstrim` is HelmRelease-managed. `flux reconcile hr fstrim` alone reported
  "release in-sync with desired state" and did **not** recreate the missing CronJob —
  helm-controller diffs its own last-applied release manifest, not live cluster state, so a
  resource deleted out from under it is invisible to a plain reconcile.
  `flux reconcile hr fstrim --force` (forces a one-off Helm upgrade regardless of diff) was
  required to actually recreate it.
- `downloads/reading-glasses-cache-prune` is a plain Kustomization-managed resource;
  `flux reconcile ks reading-glasses` recreated it directly, no `--force` needed.

Both were verified live afterward to carry `spec.timeZone: America/New_York`, **and then
re-verified after a second forced reconcile of each**, specifically because of the second
finding below — a field landing on the object once is not proof it stays.

### A second, independent way to lose the field — found while fixing the first

Before deletion, `downloads/reading-glasses-cache-prune` was in a stranger state than "never
touched by the webhook": it already carried the annotations `k8tz.io/injected: "true"` and
`k8tz.io/timezone: "America/New_York"` (not present in git — live-only, so webhook-authored),
proving the webhook **had** fired on it at some point since its 2026-04-26 creation — yet
`spec.timeZone` was `null`.

Reading `metadata.managedFields` (`--show-managed-fields`) on both this object and a
correctly-stamped sibling (`downloads/downloads-janitor`) showed neither `kustomize-controller`
nor any other field manager ever claims ownership of `spec.timeZone` (or of those annotations)
— a webhook-injected field that was never part of the applier's own intent lands unowned. An
unowned field is exactly the kind of state that should be immune to SSA's "prune what I stopped
declaring" behavior, which is presumably why the other 9 CronJobs have kept their stamped
timezone across months of routine Flux reconciles with no `spec.timeZone` in git at all.

*Why this one specifically lost it anyway is not fully explained* — the object's `generation`
was `5`, meaning its spec changed multiple times after creation (the source manifest at
`kubernetes/apps/base/downloads/reading-glasses/app/cronjob.yaml` was edited several times);
some one of those five applies evidently cleared the field while a bare annotation with the
same "unowned" shape survived. This was not root-caused further — it would require replaying
history that no longer exists — but it is exactly why the fix above re-verified after a *second*
forced reconcile rather than trusting the field's presence immediately post-recreation. Both
CronJobs held their `spec.timeZone` across that second reconcile.

## Sweep: what else was checked

**Every CronJob in the cluster (11 total)** — confirmed all carry `spec.timeZone:
America/New_York` after the fix:

```
actions-runner-system/runner-maintenance    ai/repo-wiki-repo-wiki-gen
downloads/downloads-janitor                 downloads/reading-glasses-cache-prune  (fixed)
downloads/recyclarr                         monitoring/grafana-sa-provisioner
renovate/renovate                           rook-ceph/rook-ceph-backup
system/fstrim  (fixed)                      system/pvc-mover-readable-check
system/pvc-writable-check
```

**Every other object kind k8tz's webhook targets** — the live rules are `pods` and
`cronjobs` only (quoted above); there is no third kind to sweep. For `pods`: no running pod
in any non-excluded namespace predates the webhook's `2025-09-11T23:58:22Z` creation, and no
running pod in a non-excluded namespace is missing the `k8tz` initContainer while also having
no `ownerReferences` (i.e. no orphaned bare pod slipped through). Both checked directly against
the live cluster, not inferred from coverage percentages. Pods are structurally lower-risk than
CronJobs for this class regardless: anything backed by a Deployment/StatefulSet/DaemonSet/Job
gets recreated on every rollout, image bump, or node event, which continuously re-admits it
through the webhook — a CronJob's own object, by contrast, is written once and then only ever
*patched* by Flux, which a CREATE-only rule never sees again.

**Every mutating and validating webhook in the cluster**, enumerated live
(`kubectl get {mutating,validating}webhookconfiguration -o json`) and checked for
`operations: [CREATE]`-only rules on a resource kind that isn't inherently short-lived:

| Webhook | Resource(s) | Ops | Exposed to this class? |
|---|---|---|---|
| `k8tz` | `pods`, `cronjobs` | CREATE | **Yes — the case above.** |
| `cert-manager-webhook` (mutating) | `certificaterequests` | CREATE | No live exposure: no CertificateRequest predates the webhook's `2025-09-11T23:56:41Z` creation (checked live) — and the kind is inherently short-lived (cert-manager creates a fresh one every renewal, then garbage-collects old ones), so it self-heals by churn even where a webhook change occurs. |
| `inteldeviceplugins-mutating-webhook-configuration` — `fpga.mutator.webhooks.intel.com`, `sgx.mutator.webhooks.intel.com` | `pods` | CREATE | Structurally inert here: no pod in the cluster requests an `fpga`/`sgx` resource (checked live) — this fleet has no FPGA/SGX hardware, only the Arc B70 and iGPUs covered by `devic.es/b70` and `gpu.intel.com/xe`. |
| `envoy-gateway-topology-injector.network` | `pods/binding` | CREATE | Not applicable — `pods/binding` is a one-shot subresource created fresh at every scheduling event, never a persistent object that can "predate" anything. |
| `inteldeviceplugins-*` — the `*deviceplugin.kb.io` device-plugin CRD rules (gpu/dlb/dsa/fpga/iaa/npu/qat/sgx) | own CRDs | **CREATE *and* UPDATE** | No — an UPDATE rule re-fires on every Flux reconcile (SSA `Apply` is an UPDATE once the object exists), so these self-heal continuously. This is the general reason CREATE+UPDATE rules are not part of this class. |
| `cnpg-mutating-webhook-configuration` | CNPG CRDs (clusters, backups, databases, scheduledbackups) | CREATE+UPDATE | No, same reasoning. |
| `emqx-mutating-webhook-configuration` | EMQX CRDs | CREATE+UPDATE | No, same reasoning. |
| `kopiur-mutating` | kopiur CRDs | CREATE+UPDATE | No, same reasoning. |
| `kube-prometheus-stack-admission` (mutating) | `prometheusrules` | CREATE+UPDATE | No, same reasoning. |
| All 12 `ValidatingWebhookConfiguration`s | various | mostly CREATE+UPDATE, one CREATE+UPDATE+DELETE | Validating webhooks are a different failure shape — they reject, they don't write silent stale state into an object's spec — and none found here is CREATE-only on a long-lived kind. Not pursued further. |

**Conclusion: k8tz is the only admission webhook in this cluster exposed to this class today.**
No other CREATE-only mutating rule has a matching long-lived object that predates it. The two
found CronJobs are believed to be the complete list of live drift from this specific cause.

## What would make this recur

- A future object created in a k8tz-covered namespace *before* some future k8tz config change
  (e.g. a timezone value change, or extending coverage to a currently-excluded namespace) and
  never recreated afterward, would silently repeat exactly this.
- Any *new* CREATE-only mutating webhook added to this cluster inherits the same structural
  risk against whatever long-lived kind it targets. When adding one, check whether its rule
  includes UPDATE — if it only needs to run once by design (e.g. it sets a value that must
  never change again for correctness), that is a deliberate choice worth a one-line comment
  next to the rule; if it's meant to always reflect current config (like k8tz's timezone),
  scoping to CREATE only is very likely wrong on any object outside a tight recreate/rollout
  cycle, in the same shape as this bug.

## Method

All of the above was measured directly against the live cluster
(`KUBECONFIG=/Users/coder/firstmate/projects/home-ops/kubeconfig`), read-only except for the
two documented CronJob deletions. Commands are inline above; nothing here was inferred from
chart source or documentation without a matching live check.
