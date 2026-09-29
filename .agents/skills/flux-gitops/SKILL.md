---
name: flux-gitops
description: "Read before adding an app or overlay (kubernetes/apps/base/<ns>/<app>, kubernetes/apps/main/<ns>/<app>.yaml), editing a Flux Kustomization's healthChecks, wait, dependsOn or postBuild, live-testing a HelmRelease with kubectl, or debugging a Kustomization or HelmRelease that is not Ready. Covers health-check targets, suspend-before-edit, and why flate -n gives a false green."
---

# Flux GitOps: apps, overlays, health checks and live testing

Flux reconciles from `kubernetes/clusters/main` only. `cluster-meta` builds
`kubernetes/apps/base/flux-system/meta`; `cluster-apps` builds
`kubernetes/apps/main`. There is no separate `FluxInstance` manifest: the
flux-instance HelmRelease sets `values.instance.sync.path`.

## Tripwires

1. **`healthChecks` target the workload, and `wait` stays false.** A
   `HelmRelease`-kind check stays `Ready` through a crashloop. Point the list
   at the Deployment, StatefulSet, DaemonSet, CronJob, or the CR whose status
   is the real signal. Flux ignores `spec.healthChecks` when `spec.wait: true`
   and assesses the whole inventory instead (usually just the HelmRelease).
   `healthCheckExprs` run only for objects already in that assessment set.
2. **Suspend the Kustomization before a live HelmRelease edit.** Its interval
   (or any `flux reconcile ks`) reapplies `main` and drops the experiment.
   `flux resume` itself reconciles back to `main`.
3. **`kubectl apply` cannot remove a values key** on a Flux-managed
   HelmRelease. There is no `last-applied-configuration`, so apply merges and
   keeps keys you deleted. Use `kubectl replace`, or delete the key, and
   re-read `.spec.values`.
4. **Helm cannot unset a field it never rendered.** A hand-added
   `securityContext`, or a new `volumes` / `volumeMounts` entry, survives
   `flux reconcile --force`. Undo with a strategic-merge `$patch: delete`.
5. **`Namespace/flux-system` is exempt from `cluster-apps`.** The live object
   carries `kustomize.toolkit.fluxcd.io/ssa: Ignore` and empty
   `managedFields`. A label that `kustomize build` renders for it still needs
   a live check after merge. Do not add a `base/flux-system/namespace.yaml`
   to chase it.
6. **`flate -n` is a false green.** `-n` filters the result and the exit code.
   A broken Kustomization in another namespace still exits 0. `task
   flux:test:all` (unscoped) is the gate. It also does not catch a literal
   `${...}` collision: skill `flux-substitution`.
7. **A stale `Ready=False` can hide a fix already applied.**
   controller-runtime backs off. Compare `lastTransitionTime` to now and
   check operator logs before changing anything else. Restarting a stateless
   reconciler clears the backoff; do not restart cloudnative-pg mid-switchover
   or rook-ceph mid-OSD update.
8. **A new app is done when it renders.** `task flux:test:all` is the
   pre-merge bar. Live confirmation is a post-merge follow-up. An out-of-band
   `kubectl apply` of a brand-new app is the drift GitOps exists to prevent.
9. **Never seed a Secret with client-side `kubectl apply`.** The value is
   copied into `kubectl.kubernetes.io/last-applied-configuration`. Use
   `kubectl create`, or server-side apply. Skill `secrets-1password`.
10. **`FluxResourceSuspendedTooLong` keys `exported_namespace`.**
    `flux_resource_info` is the only live suspended signal. kube-state-metrics
    has no Flux CRD state. The metric's `namespace` label is always
    `flux-system`, because it is a metric on the operator.
11. **`FluxResourceNotReadyTooLong` is the state alert for a stuck release.**
    A HelmRelease that fails its first install goes `Stalled`
    (`MissingRollbackTarget`) and emits no further events, so the one-shot
    Flux Alert event is the only other signal. The rule fires when
    a HelmRelease or Kustomization is not `Ready=True` for 30m, aggregated
    over kind, namespace and name so retries flipping False/Unknown and
    changing `reason` do not reset the timer; fix the cause,
    then `flux reconcile hr <name> -n <ns> --reset`.

## Where things live

| What | Path |
|---|---|
| Flux entry | `kubernetes/clusters/main/{meta,apps}.yaml` |
| App manifests | `kubernetes/apps/base/<ns>/<app>/` |
| Overlay Kustomization CR | `kubernetes/apps/main/<ns>/<app>.yaml` |
| Namespace list | `kubernetes/apps/main/<ns>/kustomization.yaml` and `kubernetes/apps/main/kustomization.yaml` |
| Shared namespace labels | `kubernetes/components/common/namespace.yaml` (`name: not-used`, renamed by the overlay) |
| Helm repo definitions | `kubernetes/apps/base/flux-system/meta/repos/` |
| App scaffold | `scripts/add-app/generate-app.sh` |
| Substitution collisions | skill `flux-substitution` |
| Pod spec, probes, linuxserver | skill `app-workloads` |

## Procedures

- Pick a directory shape and wire health checks: [app-shapes.md](references/app-shapes.md).
- Live-test a change that already exists in the cluster: [live-testing.md](references/live-testing.md).

## Verify

- `task flux:test:all` (unscoped). A scoped `flate -n` is readable output only.
- `flux get ks -A --status-selector ready=false` and
  `flux get hr -A`.
- After a `flux-system` namespace change, read the live Namespace. A green
  build does not prove the label landed.
