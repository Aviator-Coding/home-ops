# k8tz, tuppr, and why the namespaces stay split

`system-controller` holds k8tz. `system-upgrade` holds tuppr. Neither is stateless clutter.

## k8tz excludes its own namespace, with no opt-out

The chart's `k8tz.webhook.ignoredNamespaces` helper always emits the release namespace first. `webhook.ignoredNamespaces` only appends. `values.namespace` is `system-controller`, so the webhook skips that namespace and injects everywhere else, including `system`.

Putting the release in `system` therefore skips `system`. There is no values key that keeps injection on while exempting only the controller pods.

`k8tz.io/controller-namespace: "true"` excludes the whole namespace the same way. The chart defines no `objectSelector`. This repo deletes the chart's Namespace object, and no namespace carries that label, so the by-name prepend is the exclusion that is actually in force.

## What `system` loses

VolSync's cron reads the process `TZ` k8tz injects into the manager pod. kopiur `SnapshotSchedule`s pin `spec.schedule.timezone` themselves, so they do not move. Stripping TZ from `system` shifts every remaining VolSync schedule by four hours (EDT) or five (EST) relative to kopiur.

VolSync remains on three claims only: `selfhosted/paperless-ngx`, `selfhosted/paperless-ngx-media`, `syncthing-data`. The collision is those claims, not the retired fleet-wide count. The stagger those claims still rely on is PR #1509.

The volsync chart exposes no `env` / `extraEnv`. Compensating would be a permanent postRenderer on every workload in `system`, current and future. That is not a fix.

CronJob `spec.timeZone` is also injected, and the webhook is CREATE-only, so a migration that recreates objects re-stamps them. That is incidental. The blocker is the manager-pod TZ. Repair of a missed stamp: [webhook-drift.md](webhook-drift.md).

## The webhook move is not atomic

`MutatingWebhookConfiguration/k8tz` is cluster-scoped and keeps its name across namespaces. Helm cannot change a release namespace, so the move is uninstall plus install, and the two Flux Kustomizations have no order:

- Prune first: `failurePolicy: Fail` still points at a gone Service, and every pod CREATE in the namespaces the webhook covers fails.
- Install first: the new object is created, then the old prune deletes that same name. Injection stops until the next reconcile (interval 30m), with no error.

Neither order is safe for backups.

## Resources

`failurePolicy: Fail` and `timeoutSeconds: 10` sit on pod CREATE. Keep request equal to limit on the webhook container, the cert-watcher sidecar (the chart's `resources` do not cover it; a patch does), and `initContainerResources`. The init container is what keeps an otherwise Guaranteed pod Guaranteed. Talos `OOMController` skips Guaranteed cgroups and kills BestEffort and Burstable on a PSI spike.

The webhook container is cpu 250m and memory 64Mi, request equal to limit (raised together from 50m). The cert-watcher patch is cpu 50m and memory 64Mi, also request equal to limit: chart `resources` do not apply to that sidecar. Init is cpu 100m and memory 128Mi, request equal to limit. Do not raise a limit alone.

`replicaCount: 2`, preferred anti-affinity, plus a `DoNotSchedule` topology spread and a PDB. Preferred anti-affinity alone can co-locate both replicas. `nodeTaintsPolicy: Honor` on that spread does not see the current talos-3 taint (skill `node-scheduling`).

Timezone is `America/New_York`. `cronJobTimeZone: true`.

## imageVolume

The strategy ships in chart 0.20.0, which is the pin. It was declined: upstream's README still says the init container is the recommended strategy because imageVolume does not support `/etc/localtime`. Containers would resolve that path as UTC.

Reopen only when both are true: imageVolume can mount `/etc/localtime`, and upstream's recommendation has changed. Even then, pods whose only requests came from the k8tz init container become BestEffort. Count those pods and accept that transition on purpose, or give them real requests first. imageVolume still uses this webhook and this `ignoredNamespaces` prepend, so it is not a way to fold k8tz into `system`.

## tuppr

`talos/machineconfig.yaml.j2` `features.kubernetesTalosAPIAccess.allowedKubernetesNamespaces` lists `system-upgrade`. That is what lets tuppr's `ServiceAccount.talos.dev` receive `os:admin`. A move to `system` needs `just talos apply-node` on all three nodes before the Flux change. GitOps cannot order that. The wrong order leaves tuppr unable to call the Talos API.

The move also recreates `TalosUpgrade`, whose `rebootMode` is `powercycle`. A new CR makes tuppr re-evaluate every node. Treat that as a node roll (skill `talos-nodes`), not as a namespace rename.

`scripts/ci/version-consistency.sh` and `scripts/ci/talos-renovate-pin-test.py` hardcode these paths. Deleting or moving the manifests fails those gates even when the manifests were fine. `flate` does not notice.
