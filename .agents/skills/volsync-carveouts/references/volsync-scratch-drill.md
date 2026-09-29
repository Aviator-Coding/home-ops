# VolSync scratch restore drill

Prove the in-cluster VolSync restore path end to end (ReplicationDestination, mover Job, PVC via
`dataSourceRef`) against both the ceph and MinIO destinations without touching a live app.
Applies to the three carve-outs that still run `components/volsync`. The last full exercise
(2026-08-23, `home-automation/esphome`, since retired from VolSync) proved ceph and minio
restores work after the RGW `v20.2.4` SigV4 fix; re-measure timings before relying on them.
Rebuilding a claim is [corrupt-claim-recreation.md](corrupt-claim-recreation.md).

## Hard constraint

**Never scale down, overwrite or touch a live app's volume, and never run `just kube restore`
(`kubernetes/mod.just`) against a live claim.** That recipe scales the app to 0 and restores into
the app's own PVC in place with `copyMethod: Direct`. Every object below is new, uniquely named
and drill-owned; nothing existing is patched, scaled or written into.

## Procedure

Any app under `kubernetes/components/volsync` with recent `ReplicationSource` syncs works. Pick
the smallest repository.

### 1. Confirm the source has current, successful syncs

```bash
kubectl -n <namespace> get replicationsource <app>-ceph -o jsonpath='{.status.lastSyncTime}{" "}{.status.latestMoverStatus.result}{"\n"}'
kubectl -n <namespace> get replicationsource <app>-minio -o jsonpath='{.status.lastSyncTime}{" "}{.status.latestMoverStatus.result}{"\n"}'
```

Both `Successful` with a recent `lastSyncTime`. A failing ceph source is not "the app has no
backup": check the `rgw_sigv4_insecure` / SigV4 outage class first (skill `rook-ceph`).

### 2. Scratch `ReplicationDestination` + PVC (never reuse `<app>-dst`)

`<app>-dst` is `ssa: IfNotPresent`: a trigger set by hand persists forever and can drift its
`spec.restic.repository` from Git (a live `-dst` once pointed at the MinIO secret after an
Oct-2025 manual restore). Create new objects reusing only the existing read-only restic Secret:

```yaml
---
apiVersion: volsync.backube/v1alpha1
kind: ReplicationDestination
metadata:
  name: <app>-restore-drill-dst        # NOT <app>-dst
  namespace: <namespace>
  labels:
    fm.homeops/restore-drill: "ceph"   # or "minio"
spec:
  trigger:
    manual: restore-drill-ceph-<unique>   # any change fires a sync
  restic:
    repository: <app>-volsync-ceph-secret   # or <app>-volsync-minio-secret
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

`kubectl apply -f` both; the PVC stays `Pending` until the mover publishes a VolumeSnapshot.

### 3. Wait

```bash
kubectl -n <namespace> get replicationdestination <app>-restore-drill-dst \
  -o jsonpath='{.status.lastSyncTime}{"\n"}'
```

Poll about every 10 s; empty means running. On completion `.status.latestMoverStatus.logs` names
the restic snapshot ID and timestamp, so you can confirm it was the newest.

### 4. Mount-verify with a read-only pod

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

```bash
kubectl -n <namespace> exec restore-drill-verify -- ls -la /restore
kubectl -n <namespace> exec restore-drill-verify -- sh -c "find /restore -type f | wc -l"
kubectl -n <namespace> exec restore-drill-verify -- sh -c "sha256sum /restore/<a real file>"
kubectl -n <namespace> exec deploy/<app> -- sh -c "sha256sum /config/<same file>"
```

Matching checksums against the live pod's file are the strongest proof; restic's "Restore
completed" only proves the mover exited 0. A small app restores and verifies in about a minute.

### 5. Clean up drill artifacts only

```bash
kubectl -n <namespace> delete pod restore-drill-verify
kubectl -n <namespace> delete pvc <app>-restore-drill
kubectl -n <namespace> delete replicationdestination <app>-restore-drill-dst
kubectl get pod,pvc,replicationdestination -A -l fm.homeops/restore-drill   # must print nothing
```

**Never delete anything without the `fm.homeops/restore-drill` label**, in particular the app's
own `<app>-dst`, its claim or its `ReplicationSource`s.

Modes: a VolSync restore widens permissions by one group-write bit (see
[restore-procedures.md](restore-procedures.md)); a kopiur restore does not.
