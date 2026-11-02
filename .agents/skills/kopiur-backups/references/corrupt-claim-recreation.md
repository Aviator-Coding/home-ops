# Recreating a corrupt claim

Throwing a live volume away and rebuilding it from its kopiur repository. A scratch restore that
only proves a snapshot reads back is [kopiur-scratch-drill.md](kopiur-scratch-drill.md). Its hard
constraint (never scale, overwrite or write a live claim outside this procedure) binds here too.
Full-cluster loss is [full-cluster-restore-r2.md](full-cluster-restore-r2.md), not this.

kopiur is the only engine. VolSync was removed on 2026-11-01 (issue #2202 PR-6); its retired
restic repositories are read with [retired-restic-read.md](retired-restic-read.md).

## Read this before you delete anything

**A green rebuild is not a restore.** VolSync's populator cloned a `latestImage` frozen at first
deploy. On `ai/opencode` and `selfhosted/paperless-ngx` that image was an empty volume, so
"delete the PVC and let Flux recreate it" restored nothing while `Bound`, `Running` and `Ready`
all passed. kopiur's shape is different, but the lesson stands: prove the data is back with the
inventory below, never with a status.

Which path a claim takes decides everything:

- **Claim on `components/kopiur/pvc`** (the Stage 5 takeovers, `selfhosted/paperless-ngx` the
  last): Flux recreates the PVC with `dataSourceRef` to the standing `<app>-kopiur-dst`
  `Restore`. It is a passive `target.populator` with `fromPolicy` `<app>-ceph`, `offset: 0`,
  resolved when a new PVC claims it, so it restores the newest ceph snapshot. Pinned by
  `scripts/ci/kopiur-stage5-test.py` (rendered claim) and `kopiur-stage1-test.py`
  (`test_restore_passive_contract`).
- **Chart-owned or plain PVC** (`database/surrealdb`, `selfhosted/paperless-ngx-media`,
  `selfhosted/syncthing-data`, and every born-kopiur app): no populator. A recreated claim is
  **empty**, and the app would then back that emptiness up. Hand-restore it in place with the
  phase 4 manifest of [full-cluster-restore-r2.md](full-cluster-restore-r2.md), against the
  `ceph` repository and `<claim>-ceph` policy, before the app starts.

Before deleting a populator claim, read the standing Restore:

```sh
kubectl -n <ns> get restore <app>-kopiur-dst -o jsonpath='{.status.phase}{"\t"}{.status.conditions[?(@.type=="Resolved")].reason}{"\t"}{.spec.mover.cache.capacity}{"\n"}'
```

- It must be `Pending` / `AwaitingPvcDataSourceRef`. A `Failed` Restore never retries: delete it
  (no finalizers, owns no data) so Flux recreates it before the PVC comes back.
- Its cache must clear `min(snapshot sizeBytes, ~6.2 GiB)` ([restore-and-cache.md](restore-and-cache.md)).
  It is `ssa: IfNotPresent`: a raised `KOPIUR_CACHE_CAPACITY` does not reach it until it is
  deleted and recreated. Under-sizing fails terminally.
- `offset: 0` is the newest snapshot. If the damage is logical (the app wrote bad data that is
  already backed up), the newest snapshot is the wrong one: use an in-place hand Restore with
  `asOf` instead of the populator.

**Inert VolSync `dataSourceRef`.** A claim taken over at retirement keeps
`dataSourceRef: volsync.backube/ReplicationDestination <app>-dst` on the live object until it is
rebuilt. Kubernetes reads it only at provision, so it is harmless while bound. Never recreate a
PVC from its live YAML (`kubectl get pvc -o yaml | kubectl apply`): that kind has no CRD any more
and the claim stays `Pending`. Let Flux render it from Git, which names the kopiur Restore.

## When this applies

A claim whose live mount works but whose snapshots cannot be cloned. Every mover pod sits in
`Init`, and events carry an ext4 fsck failure on the staged clone device:

```
MountVolume.MountDevice failed for volume "pvc-...":
  'fsck' found errors on device /dev/rbdNN but could not correct them:
  /dev/rbdNN: Resize inode not valid.
  /dev/rbdNN: UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY.
```

kopiur uses `copyMethod: Snapshot`, so the claim is un-backupable while healthy in Gatus,
`flux get ks` and its own probes. The device number changes on every retry
(`rbd11/13/14/16/17/18/21` were all seen), which separates this from one bad artifact. **This
runbook replaces the volume. It does not repair it.** Never run a repairing `fsck` against live
storage and never try to salvage the image.

## Authorization gate

Recreation is destructive and irreversible. Before starting you need, explicitly for that one
named claim: the owner's confirmation that the data is disposable **or** that the snapshot being
restored is an acceptable recovery point, and an independent check of the claim (file
timestamps, the app's write pattern, the age of the newest `Succeeded` snapshot and its
`.status.stats`). It does not generalize to a namespace or app.

## Prove the restore before you destroy anything

Never skipped. kopia is file-level, so a restore writes into a filesystem the CSI driver just
created and the corruption cannot travel through it. Run [kopiur-scratch-drill.md](kopiur-scratch-drill.md)
for this claim from the `ceph` repository: a uniquely named scratch `Restore` with `target.pvc`,
`credentialProjection.enabled: true`, `onMissingSnapshot: Fail`, the claim's mover identity and
cache, then mount the new PVC `readOnly` in a throwaway pod and compare against the inventory.
That clone-and-mount is exactly the operation that was failing.

Record the scratch Restore's `status.resolved.kopiaSnapshotID`. The rebuild must resolve the same
snapshot or a newer `Succeeded` one. If the scratch volume also fails to mount, **stop**: the
cause is upstream of the volume.

## Ordering

Flux will recreate the PVC underneath you unless it is suspended.

1. **Inventory the live claim** (below).
2. **Pre-check the restore** (above).
3. **`flux suspend ks <app> -n <ns>`** and `flux suspend hr <app> -n <ns>`.
4. **Scale the app to 0** and wait for the pod to go (RWO; `pvc-protection` blocks deletion
   while a pod holds it).
5. **Clear the wedged movers.** Delete the mover Jobs, then the staged clone PVCs and the
   VolumeSnapshots taken from the corrupt claim.
   - **Never delete a kopiur `Snapshot` CR.** It owns its kopia snapshot through a finalizer, so
     deleting the CR deletes backup data. Delete its Job and staging artifacts only. A wedged
     kopiur Job has `activeDeadlineSeconds: 172800` (48 h), so clear it by hand. A CR left
     `Running` blocks later scheduled backups under `concurrencyPolicy: Forbid`; check the phase
     (it flipped to `Failed` within about 4 minutes once its staging PVC was removed, and the
     replacement started by itself).
   - VolumeSnapshots must go before the PVC. Deleting the claim while RBD snapshots of its image
     exist makes ceph-csi `DeleteVolume` fall back to the RBD trash.
6. **Check the standing Restore** (populator claims only; "Read this" above). Delete it if it is
   `Failed` or its cache is too small.
7. **Delete the PVC.** Check `kubectl get sc <class> -o jsonpath='{.reclaimPolicy}'` first. On
   `ceph-block` it is `Delete`, so the PV and RBD image really are destroyed (the point). On a
   `Retain` class you would have to remove the PV and image yourself.
8. **Resume the Kustomization only** and reconcile it.
   - Populator claim: the PVC comes back with `dataSourceRef` to `<app>-kopiur-dst`, which
     resolves and populates it. Wait for `Completed` / `RestoreSucceeded` and check
     `status.resolved.kopiaSnapshotID` against step 2.
   - No populator: the PVC comes back empty. Keep the HelmRelease suspended and the app at 0, and
     run the phase 4 in-place Restore (ceph, `<claim>-ceph`) until `Completed`.
9. **Resume the HelmRelease and reconcile it** so the scale-down drift is removed. Do not scale
   by hand.
10. **Verify** (below). Then delete the completed populator Restore and reconcile so it comes
    back unclaimed (kopiur keeps its credential copy while it exists; phase 3 step 6 of
    [full-cluster-restore-r2.md](full-cluster-restore-r2.md)), and delete the scratch drill
    objects.

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

A running app is not proof; the volume was mountable throughout the failure. All four:

1. **Data is back.** Re-run the inventory and diff against step 1. Expect and name the
   differences: files the app rewrites between backup and cutover (caches, logs, SQLite state),
   and `lost+found`. kopiur stages read-only and reproduces modes and ownership, so a mode
   difference is a finding, not noise.
2. **kopiur backs up** to ceph with a non-zero file count matching the live claim. Read
   `.status.stats`; a snapshot of an empty volume also `Succeeded`.
3. **A clone of that NEW snapshot mounts cleanly.** Cloning a snapshot is what was failing, so
   nothing else substitutes.
4. **The alerts cleared:** `KopiurBackupStaleCeph`, and the `KubeJobNotCompleted` /
   `KubeContainerWaiting` the wedged movers raised. Query Alertmanager directly:
   ```sh
   kubectl -n monitoring exec alertmanager-0 -c alertmanager -- \
     wget -qO- 'http://127.0.0.1:9093/api/v2/alerts?active=true'
   ```

The same clone failure on the recreated volume means the cause is upstream: stop and escalate.

## Leaving the cluster clean

- `flux get ks -A --status-selector ready=false` is empty and nothing is suspended
  (`flux get ks -A | grep -i true`).
- The scratch drill `Restore`, its PVC and pod are gone (label `fm.homeops/restore-drill`).
- No hand-patched fields survive. Helm cannot un-set a field it never set, so a `kubectl patch`
  of a pod spec survives `flux reconcile --force`: undo it with a merge patch setting each key to
  `null`, then confirm the live object matches Git. Scaling a Deployment is chart-owned and does
  come back on reconcile.
