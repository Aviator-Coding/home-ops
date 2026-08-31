# pvc-writable-check

A sweep that catches an app that cannot write to its own mounted volume. A
2026-08-30 audit of underused claims found this bug independently in both
`downloads/autobrr` and `selfhosted/rsshub-playwright`: each pod's
`securityContext`/`fsGroup` didn't match its volume's ownership, so the
container's UID fell through to the volume's "other" permission bits and
could never write. Both apps ran `Ready`, passed every probe, stayed
Gatus-green, and had VolSync back up the resulting empty volume on schedule
for months. The only symptom was an empty volume; the only direct evidence
was one `permission denied` log line that one of the two apps happened to
print at startup and the other did not. Nothing in the existing stack -
manifest validation, HelmRelease status, pod readiness, Gatus - checks that a
container can actually write to what it mounts.

## What it does

Every 6 hours, a `CronJob` in `system`:

1. Lists every pod in the cluster and, for every container `volumeMount` that
   backs onto a `PersistentVolumeClaim`, decides whether to test it.
2. Skips namespaces deliberately excluded from `pods/exec` RBAC
   (`rook-ceph`, `database`, `security`) before any exec attempt.
3. For each remaining target, execs `test -w <mountPath>` directly in that
   container - a single non-mutating `access()`/`stat()`-style check, not a
   write attempt.
4. Exits non-zero if any mount comes back genuinely not writable, printing
   exactly which namespace/pod/claim/path failed.

kube-state-metrics already exposes `kube_job_status_failed`/`_succeeded` for
every Job it sees, including CronJob-created ones, with no extra config - so
the Job's own exit code becomes the alert signal via `prometheusrule.yaml`,
with no custom exporter, textfile collector, or Pushgateway needed. The alert
expression requires a non-empty `reason` label (`reason!=""`) so bare-gauge
or empty-reason failed-pod-count series from kube-state-metrics v2.20.0 do
not fire (see the comment on the rule).

## Why this shape

- **Cron + PrometheusRule**, not a webhook or admission check, because the
  bug is about runtime filesystem permissions (actual UID/GID vs. actual
  volume ownership), which only exists once the pod is running - a manifest
  or admission-time check can't see it (this is exactly why `flate` and
  HelmRelease status both missed the real bugs: `selfhostedPodOptions` is
  legal YAML, and Helm silently drops the unknown key).
- **A single sweep over live pod specs, not a per-app CronJob**, because a
  per-app/per-namespace check has to be added by hand for every new app -
  exactly the "hand-maintained list that rots" this task was asked to avoid.
  Discovery is driven off live pod specs via the Kubernetes API, so a new app
  in a covered namespace is covered automatically the next time the sweep
  runs (a brand-new *namespace* still needs a RoleBinding added once - see
  below).
- **kube-state-metrics' existing Job metrics, not a new metric or exporter**,
  because it already tracks exactly the signal needed (did this Job succeed)
  for every Job in the cluster with zero additional code.

## Why pods/exec, and what is explicitly excluded

Testing write access from the actual container's identity is the entire
point - a check that ran as root on the host (e.g. reading kubelet's volume
directories directly) would always see "writable" regardless of the app's
real UID/GID/fsGroup, because root bypasses the exact permission check this
is trying to catch. That leaves `exec`-ing into the container as the only way
to ask the real question.

### Review history

The first cut used a single cluster-wide `ClusterRole` + `ClusterRoleBinding`
granting both `pods` get/list and `pods/exec` create. Review flagged that as
a real blast-radius increase: compromise of this one ServiceAccount becomes
arbitrary command execution inside Ceph, the databases, and the secret
plumbing. Captain decision narrowed it rather than accepting the grant.

### Split-role, per-namespace design

- **`pvc-writable-check-read`** - `pods` get/list only, bound cluster-wide via
  `ClusterRoleBinding`. Low-risk discovery; same shape as
  `ai/toolhive/mcp-servers/kubectl-mcp`.
- **`pvc-writable-check-exec`** - `pods/exec` create only. This ClusterRole is
  **never** bound via a `ClusterRoleBinding`. Instead one namespaced
  `RoleBinding` named `pvc-writable-check-exec` (roleRef → that ClusterRole,
  subject `ServiceAccount pvc-writable-check` in `system`) exists in every
  namespace **except** `rook-ceph`, `database`, and `security`.

Exclusion is enforced by the API server (a binding that genuinely does not
exist there), not by the script choosing not to look:

```text
kubectl auth can-i create pods --subresource=exec \
  --as=system:serviceaccount:system:pvc-writable-check -n rook-ceph
# no

# live kubectl exec against a real database pod:
# Error from server (Forbidden): pods/exec ... is forbidden: User
# "system:serviceaccount:system:pvc-writable-check" cannot create resource
# "pods/exec" in API group "" in the namespace "database"
```

The CronJob still short-circuits those namespaces up front
(`EXCLUDED_NAMESPACES`, matching the RoleBinding exclusion list) and logs
`SKIP (namespace excluded by design)` with its own counter, so a deliberate
permanent policy exclusion is not mislabeled as a flaky transient 403.

Why those three: the bug class this check exists to catch lives in ordinary
app workloads on the app-template/fsGroup pattern. Storage in `rook-ceph` /
`database` is owned by Rook/CloudNativePG; secrets plumbing in `security` is
owned by 1Password Connect / External Secrets. The excluded coverage is close
to worthless while the excluded risk is the worst of it.

Other mitigations still in place:

- The roles grant nothing else - no `secrets`, no other resources/verbs.
- No other workload references this `ServiceAccount` or mounts its token.
- The command run is always `test -w <mountPath>` (or the `sh -c` fallback);
  the path comes from the cluster's own pod specs and is always passed as a
  separate `argv` element, never spliced into a shell string.
- The container is hardened: non-root, read-only root filesystem, all
  capabilities dropped, no privilege escalation.
- The Job runs for a few minutes every 6 hours, not continuously.

### Flux `targetNamespace` gotcha (do not reintroduce)

The overlay Kustomization CR at
`kubernetes/apps/main/system/pvc-writable-check.yaml` deliberately has **no**
`targetNamespace`. Flux's `targetNamespace` unconditionally overwrites
`metadata.namespace` on every namespaced resource in the kustomize build
output, even ones that already declare their own - proven with
`flux build kustomization ... --dry-run`, which failed with a
namespace-transformation ID conflict once RoleBindings had explicit
per-namespace fields (they all collapsed onto `system` and collided by name).
`ServiceAccount`, `CronJob`, and `PrometheusRule` therefore declare
`namespace: system` explicitly; each RoleBinding keeps its own target
namespace.

When a new namespace is added to the cluster, add a matching
`pvc-writable-check-exec` RoleBinding in `rbac.yaml` (unless it is one of the
three excluded namespaces).

## Declaring "expected read-only"

Two ways, in order of preference:

1. **`volumeMounts[].readOnly: true`** on the container - the standard
   Kubernetes way to say "this mount is read-only," already skipped
   automatically and enforced by kubelet. **This is the only mechanism
   actually in use today** (`flux-system/headlamp`: the `headlamp-plugins`
   initContainer is the only writer; the main container mounts
   `/build/plugins` read-only).
2. **`pvc-writable-check.home-operations.com/skip: "true"` pod annotation** -
   an escape hatch for the rarer case where a claim can't be expressed as
   read-only at the `volumeMount` level. The script still honors it, but it
   deliberately ships with **zero current users** (captain decision) so it
   does not become the path of least resistance for a future real bug the way
   it briefly did for headlamp before the `readOnly: true` fix. Add it only
   when a real app truly cannot use option 1, with a comment explaining why.

## Awkward cases, and how each is actually handled

| Case | Handling |
|---|---|
| Namespaces excluded by design (`rook-ceph`, `database`, `security`) | No RoleBinding → API server denies `pods/exec`. Script also short-circuits first with `SKIP (namespace excluded by design)`. |
| Containers without a shell (distroless/scratch) | Try the standalone `test` binary first; if missing, retry via `sh -c` with the path as a positional parameter; if `sh` is also missing, log `SKIP (no shell or test binary)` and move on. |
| Declared read-only mounts | Skipped via `volumeMounts[].readOnly` before any exec is attempted. |
| Scaled-to-zero apps | Discovery is pod-driven: a claim with no running pod produces zero rows. |
| CronJob-owned claims that only exist briefly | Same mechanism / container-running filter; mid-check exits land in the transient-error branch. |
| Claims with no pod at all (orphaned) | No row, no mention - this check is about workloads, not claims. |
| A pod being deleted/rescheduled mid-check, timeout killing kubectl (RC 124/137/143), or any other kubectl/API-level exec error | Classified via shared `classify_exec_failure`: matches case-insensitive `error:` / `Error from server (...)` and known connection phrases, plus timeout/signal RCs, as `SKIP (exec unavailable, transient)`. Never a failure. |
| Namespace missing its `pvc-writable-check-exec` RoleBinding (reconcile lag after a new NS) | Same classifier; API-server `Forbidden` / `pods/exec` denial is `SKIP (pods/exec forbidden, RBAC)` with its own counter so coverage drift is visible in logs without paging as a volume bug. |
| Genuine unwritable mount | Only a clean `test -w` exit (typically RC=1 with empty/non-matching output) counts as `NOT WRITABLE`. |
| Multiple containers/mounts on the same claim | Tested independently per (pod, container, mountPath) tuple. |

## Measured against the live cluster (2026-08-30/31, re-measured after RBAC narrow)

Not a simulation: manifests were applied directly to the live cluster
pre-merge, run via `kubectl create job --from=cronjob/pvc-writable-check`,
and deleted again afterward.

**First real run found three bugs, not two:**

- `selfhosted/rsshub-playwright` - flagged `NOT WRITABLE` while its
  `selfhostedPodOptions` typo was still live.
- `downloads/autobrr` - already `WRITABLE` by the time of the in-cluster run
  (concurrent PR #1502 had landed); earlier in the same investigation
  `kubectl exec ... -- test -w /config` independently returned exit code 1.
- `flux-system/headlamp` - a previously undocumented third instance of the
  same shape (no `fsGroup`, main container falls through to "other").
  Correctly designed: a `runAsUser: 0` initContainer does the only write.
  Fixed by setting `readOnly: true` on the main container's
  `/build/plugins` mount (not the initContainer's), verified live (init still
  writes, main is genuinely read-only under kubelet, `/plugins` still serves
  the flux plugin).

Shell-less containers found live and skipped cleanly include
`flux-system/konflate`, `database/surrealdb`, `monitoring/loki`,
`monitoring/prometheus`, `monitoring/tempo`, and `downloads/readarr`'s
`exporter` sidecar.

The check's own `readOnlyRootFilesystem: true` initially broke bash
here-strings on multi-MB pod JSON (temp file under RO rootfs). Fixed by
switching the large payload path to a pipe / process substitution.

**Re-measured coverage after the narrowed RBAC design** (fixed design
deployed via `kubectl create job --from=cronjob/pvc-writable-check`):

- **76** total PVC-mounting container rows in one sweep.
- **16** reported `SKIP (namespace excluded by design)`:
  - 10 in `database` across 6 pods: emqx-core x2, nats x3, pgadmin,
    postgres-17 x3, surrealdb
  - 6 in `rook-ceph` across 3 mon pods
  - 0 in `security` (no PVC-mounting pod exists there today, so excluding it
    costs nothing yet)
- Remaining covered rows checked or skipped via the normal running /
  shell-less / transient paths; false-positive rate on covered targets after
  the headlamp `readOnly: true` fix: zero across the earlier full runs (only
  genuine `NOT WRITABLE` was still-broken `rsshub-playwright`, plus headlamp
  before its fix).

## What's not proven

- The Prometheus alert firing end-to-end in Alertmanager was not exercised
  pre-merge - that requires a real failed Job to persist for the full
  `for: 10m` window.
- The 6-hour schedule and 10-minute `for:` window are reasoned choices, not
  empirically tuned.
- This sweep covers PVC-mounting **containers**; it does not check
  `initContainers`. Every PVC-mounting initContainer found live in this
  cluster runs as `runAsUser: 0` specifically to do that write, making the
  check moot for every current case - worth adding if a future app's
  initContainer needs write access without running as root.
- A brand-new namespace still needs a one-time RoleBinding addition in
  `rbac.yaml` (the residual hand-maintained surface after dropping
  cluster-wide exec).
