# Consolidating `system-controller` and `system-upgrade` into `system`

**Date:** 2026-08-31
**Verdict: do not do it.** Both halves are blocked by mechanisms outside this repo's
control - the k8tz chart's own webhook template, and the Talos machine config - and the
entire benefit is a namespace list two entries shorter.

This document records the measured evidence so the idea does not get re-proposed
without it. Everything below was verified against the live cluster, not inferred.

## 1. What is actually in each namespace

The proposal assumed both namespaces are stateless. Neither is, though neither holds a PVC.

### `system-controller` - k8tz only

| Object | Note |
|---|---|
| `Deployment/k8tz` (2 replicas), `Service`, `ServiceAccount` | the webhook backend |
| `Secret/k8tz-tls`, `Secret/k8tz-webhook-ca` | cert-manager-issued, recreatable |
| `Issuer/k8tz-webhook-selfsign`, `Issuer/k8tz-webhook-ca`, 2 `Certificate`s | `app/pki.yaml` |
| 5 × `sh.helm.release.v1.k8tz.*` | Helm release history - **namespace-bound, not portable** |
| `Lease/snapshot-controller-leader` | **stale orphan**, last renewed 2025-10-20; the live snapshot-controller runs in `kube-system` with `--leader-election-namespace=$(NAMESPACE)`. Safe to delete; unrelated to k8tz. |
| `RoleBinding/pvc-writable-check-exec` | one of 20 written by `system/pvc-writable-check` |
| `cluster-secrets`, `github-status-token*`, `Alert`/`Provider`, `OCIRepository/app-template` | from `components/common` + `components/alerts` |

No PVCs.

### `system-upgrade` - tuppr only

| Object | Note |
|---|---|
| `Deployment/tuppr` (2 replicas), metrics + webhook `Service`s | |
| `ValidatingWebhookConfiguration/tuppr-validating-webhook` | `failurePolicy: Fail`, but scoped to `tuppr.home-operations.com` CRs **only** - not pods. No admission deadlock. |
| `Secret/tuppr-webhook-server-cert` | |
| **`ServiceAccount.talos.dev/tuppr-talosconfig` + `Secret/tuppr-talosconfig`** | **role `os:admin`** - the Talos API credential. See §3. |
| `TalosUpgrade/cluster` (`rebootMode: powercycle`), `KubernetesUpgrade/kubernetes` | both `Completed`; nodes at v1.13.9 / v1.36.3, matching the CRs |
| `Lease/bea89bcd.home-operations.com`, leader-election `Role`/`RoleBinding` | |
| `PrometheusRule`, `ServiceMonitor`, `ConfigMap/tuppr-dashboard` | |
| `RoleBinding/pvc-writable-check-exec` | as above |

No PVCs.

## 2. The k8tz blocker: the exclusion is not optional, and that is the problem

The brief's stated hazard was *moving k8tz without carrying its namespace exclusion across*.
That failure mode **cannot happen** - the chart prevents it. `templates/_helpers.tpl`:

```gotemplate
{{- define "k8tz.webhook.ignoredNamespaces" -}}
- {{ include "k8tz.namespace" . }}          {{/* ALWAYS the controller's own namespace */}}
{{- if .Values.webhook.ignoredNamespaces }}
{{ toYaml .Values.webhook.ignoredNamespaces }}
{{- end }}
{{- end }}
```

The controller's own namespace is unconditionally prepended. `webhook.ignoredNamespaces`
can only *add*. So `namespace: system` produces `ignoredNamespaces: [system, kube-system]`
with no way to opt out.

**The real problem is the inverse of the one anticipated: the exclusion moves
*mandatorily*, and `system` losing TZ injection is what breaks the cluster.**

### Measured: VolSync's schedules depend on the TZ k8tz injects into `system`

All 22 pods in `system` today carry `TZ=America/New_York` and a `k8tz` initContainer,
including both `volsync` managers. VolSync's Go cron scheduler honours the process `TZ`:

```
ai/hermes-r2   schedule "10 2 * * *"   nextSyncTime 2026-09-01T06:10:00Z   (= 02:10 EDT)
```

06:10Z for a `2 *` cron is only possible if the scheduler is in `America/New_York`.

Excluding `system` therefore shifts **all 90 `ReplicationSource`s** by 4h (EDT) / 5h (EST)
and collides them with kopiur's **60 `SnapshotSchedule`s**, every one of which pins
`spec.schedule.timezone: America/New_York` and so does *not* move. That destroys the
deliberate engine stagger established in PR #1509.

Secondary: 2 of the 3 CronJobs in `system` carry a k8tz-injected
`spec.timeZone: America/New_York`. The webhook fires on CREATE only, so they keep it until
something recreates them - a namespace migration being exactly that.

### There is no supported compensation

The volsync chart (`ghcr.io/home-operations/charts-mirror/volsync` 0.15.0) exposes no
`env`, `extraEnv`, or `envFrom` value. Pinning `TZ` on the manager would require a
kustomize `postRenderer` patch on the Deployment - replacing a clean cluster-wide
declarative mechanism with a bespoke per-app patch, permanently, for **every current and
future workload in `system`**. `system` is this cluster's home for infrastructure apps; it
will keep growing.

### The label selector is not a finer-grained alternative

`k8tz.io/controller-namespace: "true"` excludes the **whole namespace**, exactly like the
by-name entry - same blast radius, different key. The chart defines no `objectSelector`,
so there is no way to exclude only k8tz's own pods while keeping the rest of `system`
covered. (The label is also inert here: only the chart's `templates/namespace.yaml` applies
it, and this repo's `postRenderers` deletes that Namespace object. Confirmed live -
**no namespace in the cluster carries the label**, so the by-name clause is the sole
active exclusion.)

### The migration itself cannot be made non-disruptive

`MutatingWebhookConfiguration/k8tz` is cluster-scoped and its name does not change with the
namespace. A Helm release cannot change namespace, so the move is uninstall-old +
install-new, and the old Flux `Kustomization` (in `system-controller`) and the new one (in
`system`) are separate objects with **no ordering guarantee**:

- **Prune first:** the `failurePolicy: Fail` webhook briefly survives its backing Deployment
  → every pod CREATE in all **21 covered namespaces** fails, loudly, including VolSync and
  kopiur mover pods.
- **Install first:** the new release creates the webhook, then the old prune deletes the
  same-named object → TZ injection stops **silently**, fail-open by absence, until the next
  HelmRelease reconcile (up to 30m).

The task constraint is "do not disrupt backups". Neither ordering can guarantee that, and
which one occurs is not under our control.

## 3. The tuppr blocker: a Talos machine-config dependency, outside Flux's reach

`talos/machineconfig.yaml.j2`:

```yaml
features:
  kubernetesTalosAPIAccess:
    enabled: true
    allowedRoles: [os:admin, os:operator]
    allowedKubernetesNamespaces:
      - actions-runner-system
      - system-upgrade          # <-- tuppr
```

This allowlist gates which namespaces may hold a `ServiceAccount.talos.dev` and receive a
Talos API client certificate. tuppr's is `os:admin`. Moving tuppr to `system` requires
editing this template and running `just talos apply-node` on **all 3 nodes before** the
Flux change lands - an out-of-band, manually-sequenced step GitOps cannot express, on the
file `AGENTS.md` flags as able to downgrade a running node if stale. Getting the order
wrong leaves tuppr with no Talos API access.

Second risk: the move recreates `TalosUpgrade/cluster`, which carries
`policy.rebootMode: powercycle`. A fresh CR makes tuppr re-evaluate all 3 nodes. Both CRs
are `Completed` and the nodes already match target, so it would *probably* no-op - but
`AGENTS.md` warns that a talos-3 powercycle risks losing the Arc Pro B70 through its
OCuLink dock, and "probably" is thin cover for a change whose only benefit is cosmetic.

## 4. Every by-name reference found

| Reference | Kind |
|---|---|
| `talos/machineconfig.yaml.j2` `allowedKubernetesNamespaces` | **Talos node config - not Flux-managed** |
| k8tz `helmrelease.yaml` `values.namespace: system-controller` | drives the webhook exclusion (§2) |
| `system/pvc-writable-check/app/rbac.yaml` × 2 RoleBindings | 2 of 20; would become dead |
| `apps/main/{system-controller,system-upgrade}/` overlays + `apps/main/kustomization.yaml` | Flux wiring |
| `.github/workflows/validate.yaml` path filters (×4) | CI would silently stop covering the tree |
| `scripts/ci/talos-renovate-pin-test.py` (lines 48, 691) | hardcoded paths - test fails on a missing file |
| `scripts/ci/version-consistency.sh` (lines 26-27) | hardcoded paths |
| `docs/branch-protection.md`, `.vscode/settings.json`, `.renovate/autoMerge.json5` | docs/config |
| `components/{kopiur,volsync}/Readme.md`, `components/kopiur/ceph/snapshotschedule.yaml` | prose referencing `system-controller/k8tz` |
| `docs/grafana-operator-removal.md` | historical |
| *(fixed in this change)* `helmrepository.yaml` `namespace: system-controllers` | typo, see §6 |

The CI-script references matter: per `AGENTS.md`, `scripts/ci/*-test.py` gates that
hardcode manifest paths fail on a missing file, and `flate` does not warn.

## 5. Recommendation

**Keep `system-controller` and `system-upgrade`.**

- k8tz's separate namespace is not clutter - it is the mechanism that keeps k8tz's
  `failurePolicy: Fail` webhook off its own pods while leaving TZ injection covering
  everything else, `system` included. Merging it trades a working invariant for a shorter
  list.
- tuppr's separate namespace is pinned by a Talos node-level allowlist. Moving it converts a
  GitOps change into a 3-node `apply-node` operation plus a `powercycle`-capable CR
  recreation.

If a future k8tz release adds an `objectSelector` (or the `imageVolume` injection strategy
already noted in the HelmRelease comment removes the pod webhook from the picture), the
k8tz half becomes worth revisiting. Until then it is a regression.

## 6. What was changed instead

One real defect found inside the blast radius, fixed in the same commit:

`kubernetes/apps/base/system-controller/k8tz/app/helmrepository.yaml` declared
`metadata.namespace: system-controller**s**` - a namespace that does not exist. It is
currently masked because the Flux `Kustomization` sets `targetNamespace: system-controller`,
which overrides it (verified live: the object is in `system-controller`). The field is
removed rather than corrected, matching every other colocated source manifest in the repo
(e.g. tuppr's own `ocirepository.yaml`, `flux-operator/app/ocirepository.yaml`).

Noted, not changed - live-cluster cleanup, no GitOps representation:
`Lease/snapshot-controller-leader` in `system-controller` is a 10-month-dead orphan from
when snapshot-controller lived there.
