---
name: pvc-integrity-checks
description: "Read before editing system/pvc-writable-check or pvc-mover-readable-check, adding a PVC namespace, changing a PVC pod securityContext, or triaging PVC writable or mover-readable alerts."
---

# PVC integrity checks

Two CronJobs in `system`. An app can be unable to write its own volume, or a
backup mover can be unable to read it, while the pod is `Ready`, probes pass
and Gatus is green. Nothing else in CI sees that. These jobs are the detector.

They are not interchangeable. Writable answers "can this container write this
mount?" Mover-readable answers "can this backup identity read this claim?"

## Tripwires

1. **`pods/exec` is never cluster-wide.** Discovery is a ClusterRoleBinding.
   Exec is a ClusterRole with **no** ClusterRoleBinding, plus one namespaced
   RoleBinding per covered namespace. The API server denies the rest. Do not
   "simplify" this into one cluster-wide grant.
   [writable.md](references/writable.md)
2. **The overlay must not set `targetNamespace`.** Flux overwrites
   `metadata.namespace` on every namespaced object, so the per-namespace
   RoleBindings collapse onto `system` and collide.
3. **A new namespace that should be checked needs a RoleBinding in lockstep
   with the CronJob's namespace list.** Writable excludes `rook-ceph`,
   `database` and `security` permanently. Mover-readable binds only `ai`,
   `downloads`, `home-automation`, `media`, `selfhosted`.
   `database/pgadmin` is a permanent gap on the mover check.
4. **UNMEASURED is never a pass.** Unmounted claims, subPath-only mounts
   (`ntfy`), RBAC exclusions, and any walk that hit an error are reported,
   not counted as readable. [mover-readable.md](references/mover-readable.md)
5. **Alert on kopiur. Report-only for VolSync.** VolSync stages its clone
   writable, so `fsGroup` repairs permissions before restic reads. kopiur
   stages read-only and fails closed. The same mismatch is invisible to one
   engine and fatal to the other.
6. **The four walk traps** in `walk.sh` each return a silent false-clean zero
   if reintroduced: busybox `find` has no `-uid`/`-gid`; `/tmp` is read-only
   so there is no temp file; stderr is counted, never discarded; `stat -c %F`
   says `regular empty file`, so match the type by prefix.
   `lost+found` (`0700 root:root`) is pruned from descent and statted aside,
   or every ext4 claim is `INCONCLUSIVE`.
7. **Identities come from the live `ReplicationSource` / `SnapshotPolicy`**,
   never from component defaults. Real movers span uid 0 through 10000.
8. **Expected read-only is `volumeMounts[].readOnly: true`.** The pod
   annotation `pvc-writable-check.home-operations.com/skip` is an unused
   escape hatch. Do not use it to silence a real failure.
9. **Do not shell-interpolate a mount path.** The walker receives the path as
   an argv element. A pod must not be able to inject through its own
   `mountPath`.

## Where things live

| Check | Path | Schedule |
|---|---|---|
| Writable | `kubernetes/apps/base/system/pvc-writable-check/` | every 6h |
| Mover-readable | `kubernetes/apps/base/system/pvc-mover-readable-check/` | every 6h at :47, so the two exec sweeps do not overlap |

Alert annotations point at each app's `app/README.md`. Those stubs stay,
because the annotation is a link a human follows from the page. The rules
fire on `kube_job_status_failed` for the CronJob's Job. There is no custom
exporter.

## Verify

- `python3 scripts/ci/pvc-writable-check-test.py`
- `python3 scripts/ci/pvc-mover-readable-check-test.py`
- `python3 scripts/ci/recyclarr-config-readable-check-test.py` (the CronJob
  claim whose pod is usually absent; the measure script is
  `scripts/ci/fixtures/recyclarr-measure.sh`)

CPU: both jobs were heavily CFS-throttled when their limits were tight, and
nothing pages on throttling. Sizing is skill `node-scheduling`. Do not drop a
declared request on these containers.
