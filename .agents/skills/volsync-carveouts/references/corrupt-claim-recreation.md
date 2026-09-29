# Recreating a corrupt claim

Throwing a live volume away and rebuilding it from its repository. Scratch restores that only
prove a repository reads back: [volsync-scratch-drill.md](volsync-scratch-drill.md) and skill
`kopiur-backups`, `references/kopiur-scratch-drill.md`. Their hard constraint (never scale,
overwrite or write a live claim outside this procedure) binds here too. Contract pinned by
`scripts/ci/corrupt-claim-recreation-contract-test.py`.

## Read this before you delete anything

**"Delete the PVC and let Flux recreate it from `dataSourceRef`" does not restore from your
latest backup, and on a newly onboarded app it may restore NOTHING while every signal reports
success.** The populator clones `${APP}-dst.status.latestImage`, a snapshot frozen at first deploy
(see [restore-procedures.md](restore-procedures.md)), not the restic repository. One command
decides whether this applies:

```sh
kubectl -n <ns> get replicationdestination <app>-dst \
  -o jsonpath='{.status.lastSyncTime}{"\n"}{.status.latestImage.name}{"\n"}{.status.latestMoverStatus.logs}{"\n"}'
```

`No eligible snapshots found` / `No data will be restored` means the naive path destroys the
data: `latestImage` is a snapshot of an empty volume and stays so because `ssa: IfNotPresent`
stops Flux ever re-running the destination. `ai/opencode` read exactly that four days after
onboarding (VolSync has since been retired from it).

Claims measured empty on 2026-09-06: `selfhosted/paperless-ngx`, `paperless-ngx-media`,
`syncthing-data`. `syncthing-data-dst` was then deleted and Flux-recreated and its `restore-once`
succeeded from populated snapshot `9dd34c26`. Only `paperless-ngx` still has a live PVC
`dataSourceRef` pointing at an empty `paperless-ngx-dst`: rebuilding that volume restores nothing
unless the ReplicationDestination is deleted **together with** the PVC. `paperless-ngx-media` has
no `dataSourceRef` consumer now; its empty image is a trap if an overlay re-adds one. Re-measure
before relying on any of this.

Evidence precision: what was measured is VolSync's mover log (`No data will be restored`) and
that `latestImage` names the snapshot of that same destination PVC. The empty image was never
cloned and file-counted (it is deleted by the procedure). Treat the log as conclusive enough to
act on, never as a reason to skip the pre-check restore.

**The fix:** delete the `ReplicationDestination` **with** the PVC and let Flux recreate it. A
recreated object is new, so `IfNotPresent` applies it and its `restore-once` has never fired: it
restores from the newest restic snapshot. Never patch `spec.trigger.manual` on `<app>-dst`
(permanent drift; a live example already drifted `spec.restic.repository`). Deleting a
ReplicationDestination does not touch the restic repository; it owns only its destination PVC and
snapshot.

## When this applies

A claim whose live mount works but whose snapshots cannot be cloned. Every mover pod from every
engine sits in `Init`, and events carry an ext4 fsck failure on the staged clone device:

```
MountVolume.MountDevice failed for volume "pvc-...":
  'fsck' found errors on device /dev/rbdNN but could not correct them:
  /dev/rbdNN: Resize inode not valid.
  /dev/rbdNN: UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY.
```

Both VolSync and kopiur use `copyMethod: Snapshot`, so the claim is un-backupable by every engine
at once while healthy in Gatus, `flux get ks` and its own probes. The device number changes on
every retry (`rbd11/13/14/16/17/18/21` were all seen), which separates this from one bad
artifact. **This runbook replaces the volume. It does not repair it.** Never run a repairing
`fsck` against live storage and never try to salvage the image.

## Authorization gate

Recreation is destructive and irreversible. Before starting you need, explicitly for that one
named claim: the owner's confirmation that the data is disposable **or** that the backup being
restored is an acceptable recovery point, and an independent check of the claim (file timestamps,
the app's write pattern, the age of the newest backup). It does not generalize to a namespace or
app.

## Prove the restore before you destroy anything

Never skipped; about two minutes. Restic is file-level, so a restore writes into a filesystem
the CSI driver just created and the corruption cannot travel through it. Prove it for this claim
with a scratch destination (never the app's own `<app>-dst`):

1. Create a uniquely named scratch `ReplicationDestination` using the app's read-only credential
   Secret (`<app>-volsync-ceph-secret`), same capacity, storage class and `moverSecurityContext`,
   `trigger.manual: precheck-1` (manifest shape in [volsync-scratch-drill.md](volsync-scratch-drill.md)).
2. Wait for `status.latestMoverStatus.result: Successful`; the log names the restic snapshot.
3. Create a scratch PVC with `dataSource` on that new snapshot and mount it in a throwaway pod.
   This clone-and-mount is exactly the operation that was failing.
4. Compare against the inventory: file count, byte total, per-file sha256.
5. Delete the scratch pod and PVC. **Keep the scratch ReplicationDestination and its snapshot
   until the real recreation succeeded**; it is the fallback.

If that clone also fails to mount, **stop**: the cause is upstream of the volume.

## Ordering

Both engines hold clones of the corrupt image and Flux will recreate the PVC underneath you.

1. **Inventory the live claim** (below).
2. **Pre-check the restore** (above); keep the fallback snapshot.
3. **`flux suspend ks <app> -n <ns>`.** Otherwise Flux re-applies `pvc.yaml` between your PVC and
   ReplicationDestination deletes and the claim is rebuilt from the stale `latestImage`.
4. **Scale the app to 0** and wait for the pod to go (RWO; `pvc-protection` blocks deletion
   while a pod holds it).
5. **Clear both engines' wedged movers.** Delete the mover Jobs, then the staged clone PVCs and
   the VolumeSnapshots taken from the corrupt claim. Jobs are owned by their controllers and
   recreated next run, so deleting one destroys nothing.
   - **Never delete a kopiur `Snapshot` CR.** It owns its kopia snapshot through a finalizer, so
     deleting the CR deletes backup data. Delete its Job and staging artifacts only. A wedged
     kopiur Job has `activeDeadlineSeconds: 172800` (48 h), so clear it by hand. A CR left
     `Running` blocks later scheduled backups under `concurrencyPolicy: Forbid`; check the phase
     (it flipped to `Failed` within about 4 minutes once its staging PVC was removed, and the
     replacement started by itself).
   - **Delete the VolSync `ReplicationSource`s too**, not only their Jobs. Flux is suspended, but
     the in-cluster controller re-stages a clone within seconds of each Job/clone delete, and
     fighting that loop is how the first run got stuck. Deleting an RS never touches the
     repository; Flux recreates it on resume.
   - VolumeSnapshots must go before the PVC. Deleting the claim while RBD snapshots of its image
     exist makes ceph-csi `DeleteVolume` fall back to the RBD trash.
6. **Delete the PVC.** Check `kubectl get sc <class> -o jsonpath='{.reclaimPolicy}'` first. On
   `ceph-block` it is `Delete`, so the PV and RBD image really are destroyed (the point). On a
   `Retain` class you would have to remove the PV and image yourself.
7. **Delete the `ReplicationDestination`** and any leftover `<app>-dst-dest` PVC/snapshot.
8. **Resume Flux and reconcile.** The Kustomization recreates the ReplicationDestination (it
   restores from the newest restic snapshot), then the populator builds the PVC from that image.
9. **Restore the replica count through Helm**, not by hand: reconcile the HelmRelease so the
   scale-down drift is removed.
10. **Verify**, then delete the scratch pre-check destination and its snapshot.

Steps 3-7 are out-of-band by necessity: keep them to that minimum, name each action, and leave
nothing suspended at the end.

## Inventory (step 1)

Capture enough to state a difference precisely, from inside the running app pod:

```sh
POD=$(kubectl -n <ns> get pod -l app.kubernetes.io/name=<app> -o name | head -1)
kubectl -n <ns> exec $POD -c app -- sh -c 'cd <mountpath>
  echo "files=$(find . -type f | wc -l)"
  echo "dirs=$(find . -type d | wc -l)"
  echo "symlinks=$(find . -type l | wc -l)"
  echo "bytes=$(find . -type f -exec stat -c %s {} \; | awk "{s+=\$1} END {print s}")"'

# per-file sha256 manifest. Busybox `find -exec {} +` can drop paths and still exit 0.
# Use \; and check the manifest line count against `find | wc -l`.
kubectl -n <ns> exec $POD -c app -- sh -c \
  'cd <mountpath> && find . -type f -print0 | sort -z | xargs -0 sha256sum' > sha256-live.txt

# modes and ownership
kubectl -n <ns> exec $POD -c app -- sh -c \
  'cd <mountpath> && find . -mindepth 1 -exec stat -c "%a %U:%G %F %n" {} \; | sort -k4' > modes-live.txt
```

Reduce the manifest to one number with `sort sha256-live.txt | shasum -a 256`. busybox/alpine
`find` has no `-printf`; use `-exec stat -c`.

**Secrets:** a home directory routinely holds credentials (`.git-credentials`, `.npmrc`, SSH
keys). Record only presence, mode and size (`stat -c '%n mode=%a owner=%U:%G size=%s'`). Never
`cat` one or let one reach a log, PR body or commit. Keep the full manifest out of the repository
and publish only the aggregate digest.

## Verification (the actual deliverable)

A running app is not proof; the volume was mountable throughout the failure. All five:

1. **Data is back.** Re-run the inventory and diff against step 1. Expect and name three
   differences: files the app rewrites between backup and cutover (caches, logs, SQLite state);
   `lost+found` (restic never stores it, VolSync's `--delete` restore removes it); and **every
   file mode relaxed by one group-write bit**. A content diff will not show the last; check modes.

   > A VolSync restore silently widens permissions, credential files included. The mover stages
   > its destination PVC writable, so kubelet's recursive `fsGroup` walk runs before restic
   > writes: `644→664`, `755→775`, `2755→2775`, `600→660`, `444→664`. On `ai/opencode`,
   > `.git-credentials` went `0600` to `0660`. That was benign only because the widened group was
   > the app's own gid and the sole principal on the volume; do not assume that. Before restoring
   > a volume with credentials, work out who else is in that gid and re-tighten modes afterwards
   > if anyone is. Ownership is unaffected; the relaxation persists. kopiur restores stage
   > read-only and reproduce modes.
2. **kopiur backs up** to ceph with a non-zero file count matching the live claim. Read
   `.status.stats`; a snapshot of an empty volume also `Succeeded`.
3. **VolSync backs up** to ceph; `status.latestMoverStatus.logs` names the processed file count.
4. **A clone of one of those NEW backups mounts cleanly.** Cloning a snapshot is what was
   failing, so nothing else substitutes.
5. **The alerts cleared:** `VolSyncSyncStalled*`, `VolSyncVolumeOutOfSync`, and the
   `KubeJobNotCompleted` / `KubeContainerWaiting` the wedged movers raised. Query Alertmanager
   directly:
   ```sh
   kubectl -n monitoring exec alertmanager-0 -c alertmanager -- \
     wget -qO- 'http://127.0.0.1:9093/api/v2/alerts?active=true'
   ```

The same clone failure on the recreated volume means the cause is upstream: stop and escalate.

## Leaving the cluster clean

- `flux get ks -A --status-selector ready=false` is empty and nothing is suspended
  (`flux get ks -A | grep -i true`).
- The scratch pre-check `ReplicationDestination`, its dest PVC and snapshot are gone.
- No hand-patched fields survive. Helm cannot un-set a field it never set, so a `kubectl patch`
  of a pod spec survives `flux reconcile --force`: undo it with a merge patch setting each key to
  `null`, then confirm the live object matches Git. Scaling a Deployment is chart-owned and does
  come back on reconcile.
