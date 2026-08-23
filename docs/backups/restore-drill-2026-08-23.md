# VolSync restore drill - 2026-08-23

> **Historical snapshot, but the procedure is durable.** Numbers below were measured live
> against the cluster on 2026-08-23 between 20:51Z and 20:55Z, on branch `fm/homeops-restore-drill`
> at repo HEAD `0817cfda`. Re-measure timings before relying on them; the *procedure*
> itself (manifests, ordering, verification method, cleanup) is the reusable part.

## Why this exists

Per the task's supporting investigation (Q2), **no restore of the actual in-cluster VolSync
mechanism had ever been exercised from the Ceph destination**, and no VolSync-mediated restore
had run from any destination since 2026-04-14. The only prior restore-shaped events on record
were: the automatic single run every `ReplicationDestination` performs once at creation against
an empty repo (not a real restore); a MinIO-sourced batch restore from 2025-10-05/06; and, one
day before this drill, `docs/backups/volsync-coverage-2026-08-22.md` section 5 proving the
Ceph-backed restic repositories for `paperless-ngx` and `syncthing` were intact and restorable,
via a local restic CLI restore over a `kubectl port-forward` with `--no-lock`, checksummed
against the live PVCs. That proved the repository contents were sound, but not the in-cluster
restore mechanism itself - it never touched the `ReplicationDestination` CRD, its mover `Job`,
or a PVC provisioned via `dataSourceRef`. This drill is the first exercise of that actual
Kubernetes-native restore path end-to-end, and the first following the RGW `v20.2.3` ->
`v20.2.4` SigV4 write-outage fix earlier the same day (see `docs/ceph-cluster-changelog.md`) -
so it is also the first live proof that Ceph-destination restores work again post-fix.

This drill proves the restore path end to end, against both the Ceph and MinIO destinations,
without touching any live app's data.

## Hard constraint this procedure satisfies

**Never scale down, overwrite, or touch a live app's volume. Never run the in-place
`just kube restore` flow (`kubernetes/mod.just` `restore` recipe) against a live claim** - that
recipe scales the target app to 0, then derives a brand-new `<app>-manual` object from `<app>-dst`
(`kubectl apply --server-side`, not a patch of `<app>-dst` itself) with `copyMethod: Direct` and
`destinationPVC` set to the app's own claim - i.e. it restores straight into the live PVC in
place, which is exactly the kind of live-claim interaction this drill avoids. Every object
created below is new, uniquely named, and owned only by the drill; nothing that already exists
in the cluster is patched, scaled, or written into directly.

## Procedure

Pick any app already protected by `kubernetes/components/volsync` (see that directory's
`Readme.md` for the full component contract). This drill used `home-automation/esphome`
(claim `esphome-config`, repo name `esphome`) because it was the smallest live, current
repository at drill time (48 objects in the Ceph bucket versus hundreds-to-thousands for
larger apps - checked with `radosgw-admin bucket radoslist --bucket=volsync | grep -c "_<app>/"`
from a `rook-ceph-tools` pod) - the app itself doesn't matter, any app with recent
`ReplicationSource` syncs works identically.

### 1. Confirm the source has current, successful syncs

```bash
kubectl -n <namespace> get replicationsource <app>-ceph -o jsonpath='{.status.lastSyncTime}{" "}{.status.latestMoverStatus.result}{"\n"}'
kubectl -n <namespace> get replicationsource <app>-minio -o jsonpath='{.status.lastSyncTime}{" "}{.status.latestMoverStatus.result}{"\n"}'
```

Both must show `Successful` with a recent `lastSyncTime`. If the Ceph one is failing, this is
not a "the app has no backup" problem - see `docs/ceph-cluster-changelog.md` for the
`rgw_sigv4_insecure` / v20.2.4 SigV4 outage class of failure first.

### 2. Create a scratch `ReplicationDestination` + PVC (do not reuse `<app>-dst`)

The app's own `<app>-dst` object (created by the `volsync` component) is Git-managed with
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`, meaning Flux only creates it once and never
reconciles it again - any trigger you set by hand persists forever and can drift its
`spec.restic.repository` away from what Git declares (live example found during this drill:
`home-automation/esphome`'s `esphome-dst` still points at the MinIO secret from a manual restore
performed in October 2025). **Never patch `<app>-dst` for a drill.** Instead create new objects
with drill-specific names, reusing only the existing (read-only) restic credential Secret:

```yaml
---
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata:
  name: <app>-restore-drill-dst        # NOT <app>-dst
  namespace: <namespace>
  labels:
    fm.homeops/restore-drill: "ceph"   # or "minio" - makes cleanup/sweep trivial
spec:
  trigger:
    manual: restore-drill-ceph-<unique>   # any change to this value fires a sync
  restic:
    repository: <app>-volsync-ceph-secret   # or -minio-secret; existing Secret, read-only
    copyMethod: Snapshot
    volumeSnapshotClassName: csi-ceph-blockpool
    cacheStorageClassName: ceph-block
    cacheAccessModes: ["ReadWriteOnce"]
    cacheCapacity: 1Gi
    storageClassName: ceph-block
    accessModes: ["ReadWriteOnce"]
    capacity: <same as the app's real PVC>
    moverSecurityContext:
      runAsUser: 1000
      runAsGroup: 1000
      fsGroup: 1000
    enableFileDeletion: true
    cleanupCachePVC: true
    cleanupTempPVC: true
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app>-restore-drill             # NOT the app's real claim name
  namespace: <namespace>
  labels:
    fm.homeops/restore-drill: "ceph"
spec:
  accessModes: ["ReadWriteOnce"]
  dataSourceRef:
    kind: ReplicationDestination
    apiGroup: volsync.backube
    name: <app>-restore-drill-dst
  resources:
    requests:
      storage: <same as above>
  storageClassName: ceph-block
```

`kubectl apply -f` both documents together - the PVC will sit `Pending` until the
`ReplicationDestination`'s restic mover completes and publishes a `VolumeSnapshot`, then bind.

### 3. Wait for the restore to complete

```bash
kubectl -n <namespace> get replicationdestination <app>-restore-drill-dst \
  -o jsonpath='{.status.lastSyncTime}{"\n"}'
```

Poll every ~10s; an empty value means still running. `.status.latestMoverStatus.logs` on
completion names the exact snapshot restored (restic snapshot ID + timestamp) - useful for
confirming it restored the *newest* snapshot, not an arbitrary one.

### 4. Mount-verify with a read-only debug pod

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: restore-drill-verify
  namespace: <namespace>
  labels:
    fm.homeops/restore-drill: "ceph"
spec:
  restartPolicy: Never
  securityContext: { runAsUser: 1000, runAsGroup: 1000, fsGroup: 1000 }
  containers:
    - name: verify
      image: docker.io/library/busybox:1.36
      command: ["sh", "-c", "sleep 300"]
      volumeMounts: [{ name: data, mountPath: /restore, readOnly: true }]
  volumes:
    - name: data
      persistentVolumeClaim: { claimName: <app>-restore-drill, readOnly: true }
```

Then, once `Ready`:

```bash
kubectl -n <namespace> exec restore-drill-verify -- ls -la /restore
kubectl -n <namespace> exec restore-drill-verify -- sh -c "find /restore -type f | wc -l"
kubectl -n <namespace> exec restore-drill-verify -- sh -c "sha256sum /restore/<a real file>"
# cross-check against the live app pod to prove it's real data, not an empty restore:
kubectl -n <namespace> exec deploy/<app> -- sh -c "sha256sum /config/<same file>"
```

Matching checksums between the restored PVC and the live pod's current file is the strongest
available proof the restore is genuine - restic's own "Restore completed" log line only proves
the mover exited 0, not that the content is correct.

### 5. Clean up - drill artifacts only

```bash
kubectl -n <namespace> delete pod restore-drill-verify
kubectl -n <namespace> delete pvc <app>-restore-drill
kubectl -n <namespace> delete replicationdestination <app>-restore-drill-dst
```

The `fm.homeops/restore-drill` label makes a final sweep trivial:
`kubectl get pod,pvc,replicationdestination -A -l fm.homeops/restore-drill` must return nothing
before considering the drill done. **Never delete anything without that label** - in particular
never delete the app's own `<app>-dst`, `<app>-config`/`<app>` PVC, or `ReplicationSource`
objects; those are Git-managed and none of this drill's business.

## Results

Both runs restored `home-automation/esphome`'s `esphome-config` claim (5Gi, 46 files, includes
a `.git` checkout and small config/secret files) into a scratch PVC of the same size, then
verified via `sha256sum` cross-check against the live pod's current `.gitignore` /
`.device-builder.json`. Both checksums matched exactly in both runs - the restore is genuine,
not an empty repository skeleton.

| Destination | Restic snapshot restored | Trigger -> sync complete | Restic mover time | PVC bind | Verify pod ready | Total (trigger -> verified) |
|---|---|---:|---:|---:|---:|---:|
| **Ceph** (`ceph-objectstore`, RGW) | `aa39f511`, 2026-08-23 16:15:35 EDT (newest) | 14.5s | 4s | ~15-20s after trigger | 20:52:38Z (37s after trigger) | **~50s** |
| **MinIO** (`nas.sklab.dev:9000`) | `7c7999a7`, 2026-08-23 12:15:38 EDT (newest) | 23.9s | 3s | 20:54:27Z (34s after trigger) | 20:54:42Z (49s after trigger) | **~50s** |

Both destinations restore in under a minute for a small (5Gi-class, sub-100-object-repo) app.
Larger apps (`immich` at 100Gi, `syncthing-data` at 100Gi) will take substantially longer -
restic mover time scales with snapshot size, not just object count - so do not extrapolate
these timings to the whole fleet without re-measuring on a representative large app.

## What this drill did and did not prove

- **Proved**: the Ceph destination restore path works end-to-end today (post the RGW
  `v20.2.4` SigV4 fix earlier the same day), producing byte-identical file content to the live
  app. The MinIO path, already known to be healthy, was re-proven identically as a baseline.
  The manual-trigger + scratch-PVC pattern above is safe to repeat against any app without
  touching its live volume.
- **Did not prove**: R2-destination restores (not exercised - time did not permit; the R2
  restic repository uses the same shape, only the `ReplicationDestination`'s
  `repository:` Secret name changes to `<app>-volsync-r2-secret`, so the same procedure applies
  unmodified). Restore behavior for a large PVC (100Gi-class). Restore of a CephFS
  (`ReadWriteMany`) claim - this drill only covered `ceph-block`/RWO.
- **Follow-up worth doing**: turn this into a recurring, scheduled drill (e.g. quarterly,
  rotating which app) now that a safe procedure exists, rather than relying on ad hoc exercises.
  R1.2 in the task's supporting investigation's ordered work list already flagged "exercise one
  restore" as outstanding; this closes that for Ceph and MinIO but leaves R2 and large-PVC
  restores open.

## Safety notes for whoever runs this next

- The scratch `ReplicationDestination`'s `spec.restic.repository` field reuses the app's
  **existing** credential Secret read-only; nothing about this procedure writes to the restic
  repository (VolSync's restore/read path performs no writes to the backup bucket beyond a
  short-lived restic lock file, cleaned up automatically).
- `enableFileDeletion: true` on the scratch `ReplicationDestination` only affects files inside
  the scratch PVC being restored into, not the source repository.
- Always verify the `<app>-restore-drill*` object names are free before applying (`kubectl get
  ... <app>-restore-drill-dst` should 404) so a re-run never collides with a previous drill's
  leftovers.
