# Recreating a corrupt claim (runbook)

> **Durable procedure.** Written while recreating `ai/opencode` on 2026-08-31, but the
> ordering, the gates, and the traps are the reusable part. Measured numbers from that
> run are in [Appendix A](#appendix-a---the-2026-08-31-aiopencode-run) and are evidence,
> not a spec.
>
> Sibling documents: [`restore-drill-2026-08-23.md`](restore-drill-2026-08-23.md) (VolSync
> restore) and [`kopiur-restore-drill-2026-08-30.md`](kopiur-restore-drill-2026-08-30.md)
> (kopiur restore). Those two prove a repository can be read back; **this one is about
> throwing a live volume away and rebuilding it from that repository.** Read the
> "Hard constraint" section of the 2026-08-23 drill first - it binds here too.

---

## ⚠ Read this before you delete anything

**"Delete the PVC and let Flux recreate it from `dataSourceRef`" does not restore from your
latest backup, and on a newly-onboarded app it may restore NOTHING while every signal
reports success.**

The populator clones `${APP}-dst.status.latestImage` - a snapshot frozen at first-deploy
time - **not** the restic repository. If that app was onboarded before its repository held
a snapshot, `latestImage` is a snapshot of an empty volume, permanently, because
`ssa: IfNotPresent` stops Flux ever re-running the destination.

**One command decides whether this applies to you:**

```sh
kubectl -n <ns> get replicationdestination <app>-dst \
  -o jsonpath='{.status.lastSyncTime}{"\n"}{.status.latestMoverStatus.logs}{"\n"}'
```

`No eligible snapshots found` / `No data will be restored` in that output means the
naive path destroys the data. **`ai/opencode` read exactly that on 2026-08-31, four days
after onboarding** - the task that produced this runbook was briefed on the assumption
that the populator restores from the last good backup, and that assumption was wrong.

The fix is [below](#the-trap-that-decides-the-whole-procedure): delete the
`ReplicationDestination` **together with** the PVC.

---

## When this applies

A claim whose **live mount works fine** but whose **snapshots cannot be cloned**. The
signature is that every mover pod from every backup engine sits in `Init`, and the pod
events carry an ext4 fsck failure against the staged clone device:

```
MountVolume.MountDevice failed for volume "pvc-…":
  'fsck' found errors on device /dev/rbdNN but could not correct them:
  /dev/rbdNN: Resize inode not valid.
  /dev/rbdNN: UNEXPECTED INCONSISTENCY; RUN fsck MANUALLY.
```

Because both VolSync and kopiur use `copyMethod: Snapshot`, **a claim in this state is
un-backupable by every engine at once** while looking perfectly healthy in Gatus, in
`flux get ks`, and in the app's own probes. The live RBD image is readable; only clones
of its snapshots fail. The device number changes on every retry (`rbd11/13/14/16/17/18/21`
were all seen), which is how you tell this from a single bad artifact.

**This runbook replaces the volume. It does not repair it.** Do not run a repairing
`fsck` against live storage, and do not try to salvage the image.

## Authorization gate

Recreating a claim is destructive and irreversible. Before starting you need, explicitly
and for **that one named claim**:

- the owner's confirmation that the data is disposable **or** that the backup you are
  about to restore from is an acceptable recovery point, and
- an independent check of that claim: file timestamps, the app's own write pattern, and
  the age of the newest backup. Do not take "it's fine" on trust when a one-command check
  can confirm it.

The authorization covers one claim. It does not generalize to a namespace or an app.

## The trap that decides the whole procedure

The obvious move - "delete the PVC, Flux recreates it from `dataSourceRef`, done" - is
**wrong on its own**, and wrong in a way that silently destroys data.

`kubernetes/components/volsync/pvc.yaml` creates the claim with
`dataSourceRef -> ReplicationDestination ${APP}-dst`. The VolSync volume populator does
**not** read the restic repository when it populates that PVC. It clones
`${APP}-dst.status.latestImage`, a VolumeSnapshot produced by the **last time that
ReplicationDestination actually ran**. And `${APP}-dst` carries
`trigger: { manual: restore-once }` plus the label
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`, which together mean:

- it runs **exactly once**, when the app is first deployed, and
- Flux never reconciles it again, so it never re-runs on its own.

So `latestImage` is frozen at first-deploy time. **Always read it before relying on it:**

```sh
kubectl -n <ns> get replicationdestination <app>-dst \
  -o jsonpath='{.status.lastSyncTime}{"\n"}{.status.latestImage.name}{"\n"}{.status.latestMoverStatus.logs}{"\n"}'
```

On a **newly onboarded app** the repository was still empty at first deploy, so that log
reads `No eligible snapshots found` / `No data will be restored` and `latestImage` is a
snapshot of an **empty 20 GiB volume**. Deleting the PVC and letting Flux recreate it
would then restore *nothing at all*, and it would look like a success: PVC `Bound`, app
`Running`, Flux `Ready`. This is exactly the state `ai/opencode` was in.

**Precision about the evidence**, because this claim is load-bearing: what is *measured*
is VolSync's own mover log for that run (`No data will be restored`) plus the fact that
`latestImage` names the snapshot of that same destination PVC. The empty image was not
separately cloned and file-counted - it is deleted as part of this procedure, so the
opportunity is gone once you start. Treat the mover log as conclusive enough to act on,
and never as a reason to skip the pre-check restore below.

**The fix is to make the ReplicationDestination run again against the now-populated
repository.** Do that by **deleting the `ReplicationDestination` along with the PVC** and
letting Flux recreate it. A recreated object is new, so `IfNotPresent` applies it and its
`restore-once` trigger has never fired - it restores from the newest restic snapshot.

Do **not** instead patch `spec.trigger.manual` on the app's own `<app>-dst` to a new
value. `IfNotPresent` means Flux will never reconcile that edit away, so it is permanent
drift; this is the documented hazard in `AGENTS.md` ("Never patch an app's own `<app>-dst`
ReplicationDestination to trigger a restore") and a live example of it drifting
`spec.restic.repository` already exists in the fleet. Deleting and letting Flux rebuild
leaves zero drift.

Deleting a `ReplicationDestination` does **not** touch the restic repository. It only owns
its own destination PVC and snapshot.

## Prove the restore before you destroy anything

The one gate that must never be skipped. It costs about two minutes and it is the
difference between a recoverable mistake and an unrecoverable one.

Restic is a **file-level** repository, so a restore always writes into a filesystem the
CSI driver has just created. That is *why* this procedure works at all - the corruption
cannot travel through restic. But prove it for this claim rather than assuming it, using
the sanctioned scratch-destination pattern (never the app's own `<app>-dst`):

1. Create a scratch `ReplicationDestination`, uniquely named, pointing at the app's
   existing read-only credential Secret (`<app>-volsync-ceph-secret`), same capacity,
   storage class and `moverSecurityContext` as the real one, `trigger.manual: precheck-1`.
2. Wait for `status.latestMoverStatus.result: Successful` and read its log - it names the
   restic snapshot and confirms data was restored.
3. Create a scratch PVC with `dataSource` pointing at that new snapshot, and mount it in a
   throwaway pod. **This clone-and-mount is precisely the operation that was failing**, so
   a clean mount here is the proof.
4. Compare against the Step-1 inventory: file count, byte total, per-file sha256.
5. Delete the scratch pod and PVC. **Keep the scratch ReplicationDestination and its
   snapshot until the real recreation has succeeded** - it is your fallback.

If that clone also fails to mount, **stop**. The cause is upstream of the volume and the
diagnosis has to be reopened.

## Ordering

Work out the ordering before touching anything; it is the part that bites. Both engines
hold clones of the corrupt image, and Flux will happily recreate the PVC underneath you
while you are trying to delete the destination.

1. **Inventory the live claim** (below) - you cannot state a difference you never measured.
2. **Pre-check the restore** (above). Keep the fallback snapshot.
3. **Suspend the app's Flux Kustomization.**
   `flux suspend ks <app> -n <ns>`
   Without this, Flux re-applies `pvc.yaml` between your PVC delete and your
   ReplicationDestination delete, and you end up recreating the claim from the *stale*
   `latestImage` you were trying to get rid of.
4. **Scale the app to 0** and wait for the pod to go. The claim is RWO; while a pod holds
   it, `kubernetes.io/pvc-protection` blocks deletion.
5. **Clear both engines' wedged movers.** Delete the mover **Jobs**, then the staged clone
   PVCs and the VolumeSnapshots taken from the corrupt claim. The Jobs are owned by their
   controllers (`ReplicationSource` / kopiur `Snapshot`) and are recreated on the next run,
   so deleting a Job destroys nothing.
   - **Never delete a kopiur `Snapshot` CR.** It owns its kopia snapshot through a
     finalizer, so deleting the CR deletes backup data. Delete its Job and staging
     artifacts only, and leave the CR. A wedged kopiur Job carries
     `activeDeadlineSeconds: 172800` (48 h), so it will not clear itself in any useful
     time - you do have to clear it by hand. A CR left `Running` also blocks later
     scheduled kopiur backups for that claim under `concurrencyPolicy: Forbid` - check
     the phase rather than waiting on the 48 h deadline (on the 2026-08-31 run the
     wedged CR flipped to `Failed` within ~4 minutes after its staging PVC was removed,
     and the replacement run started on its own).
   - **Delete the VolSync `ReplicationSource`s too**, not only their Jobs. Flux is
     suspended so Git will not recreate them mid-procedure, but the in-cluster VolSync
     controller still reconciles live RS objects and **re-stages a new clone within
     seconds of each Job/clone delete** - fighting that loop is how the 2026-08-31 run
     got stuck. Deleting an RS never touches the restic repository; Flux recreates the
     sources cleanly on resume.
   - Dependent VolumeSnapshots must go before the PVC. Deleting the claim while RBD
     snapshots of its image still exist makes the ceph-csi `DeleteVolume` fall back to the
     RBD trash instead of a clean delete.
6. **Delete the PVC.** Confirm the storage class reclaim policy first:
   `kubectl get sc <class> -o jsonpath='{.reclaimPolicy}'`. On `ceph-block` this is
   `Delete`, so the PV and the underlying RBD image really are destroyed - which is the
   point. On a `Retain` class you would have to remove the PV and the image yourself.
7. **Delete the `ReplicationDestination`** and any leftover `<app>-dst-dest` PVC/snapshot.
8. **Resume Flux and reconcile.** The Kustomization recreates the ReplicationDestination
   (which restores from the newest restic snapshot), then the populator builds the PVC
   from that fresh image.
9. **Restore the app's replica count through Helm, not by hand** - reconcile the
   HelmRelease so the scale-down drift is removed rather than left behind.
10. **Verify** (below), then delete the scratch pre-check destination and its snapshot.

Everything in steps 3-7 is out-of-band by necessity: Flux has no way to express "destroy
and rebuild this volume". Keep it to that minimum, name each action, and leave **nothing
suspended** at the end.

## Inventory (step 1)

Capture enough to state a difference precisely, from inside the running app pod:

```sh
POD=$(kubectl -n <ns> get pod -l app.kubernetes.io/name=<app> -o name | head -1)
kubectl -n <ns> exec $POD -c app -- sh -c 'cd <mountpath>
  echo "files=$(find . -type f | wc -l)"
  echo "dirs=$(find . -type d | wc -l)"
  echo "symlinks=$(find . -type l | wc -l)"
  echo "bytes=$(find . -type f -exec stat -c %s {} + | awk "{s+=\$1} END {print s}")"'

# per-file sha256 manifest
kubectl -n <ns> exec $POD -c app -- sh -c \
  'cd <mountpath> && find . -type f -print0 | sort -z | xargs -0 sha256sum' > sha256-live.txt

# modes and ownership
kubectl -n <ns> exec $POD -c app -- sh -c \
  'cd <mountpath> && find . -mindepth 1 -exec stat -c "%a %U:%G %F %n" {} + | sort -k4' > modes-live.txt
```

Reduce the manifest to one comparable number with `sort sha256-live.txt | shasum -a 256`.

**Secrets:** a home directory routinely holds credentials (`.git-credentials`,
`.npmrc`, SSH keys). Record only **presence, mode and size** - `stat -c '%n mode=%a
owner=%U:%G size=%s'`. Never `cat` one, never let one reach a log, a PR body, or a commit.
A sha256 in the manifest is a hash, not content, and is fine to keep locally; keep the
full manifest out of the repository anyway and publish only the aggregate digest.

`busybox`/alpine `find` has no `-printf`; use `-exec stat -c` as above.

## Verification - the actual deliverable

A running app is **not** proof. The volume was mountable throughout the original failure.
All five of these:

1. **Data is back.** Re-run the inventory and diff it against step 1. Expect three
   differences and be able to name each:
   - files the app rewrites on its own between the backup and the cutover (caches, logs,
     SQLite state);
   - `lost+found`, which restic never stores and which VolSync's `--delete` restore
     removes from the fresh filesystem;
   - **every file mode relaxed by one group-write bit** - see the warning below. Check
     modes explicitly; a file-content diff alone will not show this.

   > **A VolSync restore silently widens permissions, credential files included.** The
   > mover stages its destination PVC **writable**, so kubelet's recursive `fsGroup` walk
   > runs before restic writes: the volume comes back `644→664`, `755→775`, `2755→2775`,
   > `600→660`, `444→664`. On `ai/opencode` that took `.git-credentials` from `0600` to
   > `0660` across the restore. It was benign there only because the widened group was the
   > app's own gid and the app is the sole principal on the volume - **that is not
   > something to assume.** Before restoring a volume that holds credentials, work out who
   > else is in that gid, and re-tighten modes afterwards if anyone is. Ownership is
   > unaffected, and the relaxation persists on the live volume. kopiur restores stage
   > read-only and reproduce the original modes.
2. **kopiur backs up successfully** to ceph, with a non-zero file count matching the live
   claim. A `Succeeded` phase alone is worth nothing - **read `.status.stats`**; a snapshot
   of an empty volume also succeeds.
3. **VolSync backs up successfully** to ceph. Its `status.latestMoverStatus.logs` names the
   processed file count; check it against the live claim.
4. **A clone of one of those NEW backups mounts cleanly.** This is the one that proves the
   corruption is gone, because cloning a snapshot is exactly what was failing. Nothing else
   substitutes for it.
5. **The alerts cleared.** `VolSyncSyncStalled*` and `VolSyncVolumeOutOfSync` for that
   claim, plus the `KubeJobNotCompleted` / `KubeContainerWaiting` the wedged movers raised.
   Query Alertmanager directly rather than trusting a dashboard:
   ```sh
   kubectl -n monitoring exec alertmanager-0 -c alertmanager -- \
     wget -qO- 'http://127.0.0.1:9093/api/v2/alerts?active=true'
   ```

If the recreated volume shows the **same** clone failure, stop and escalate. That would
mean the cause is upstream of the volume.

## Leaving the cluster clean

- `flux get ks -A --status-selector ready=false` is empty, and nothing is suspended
  (`flux get ks -A | grep -i true` for the suspended column).
- The scratch pre-check `ReplicationDestination`, its dest PVC and its snapshot are gone.
- No hand-patched fields survive. Helm cannot un-set a field it never set, so a
  `kubectl patch` of a pod spec **survives `flux reconcile --force`** - undo such edits
  explicitly with a merge patch setting each key to `null`, then confirm the live object
  matches git. Scaling a Deployment is owned by the chart and does come back on reconcile.

## Appendix A - the 2026-08-31 `ai/opencode` run

See [`opencode-volume-recreation-2026-08-31.md`](opencode-volume-recreation-2026-08-31.md)
for the measured record of this procedure's first execution: the empty-`latestImage`
discovery, the pre-check numbers, and the post-recreation verification evidence.
