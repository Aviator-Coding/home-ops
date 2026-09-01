# kopiur per-volume restore proof - 2026-09-01

> **Status: evidence base only. Nothing was retired by this exercise.**
> No `ReplicationSource`, VolSync claim, or VolSync configuration was removed or modified
> anywhere. Every VolSync source remains live and untouched. This document is the evidence
> that a later, separate, irreversible retirement step is entitled to rely on.

This is the Stage 5 prerequisite: a per-volume restore proof for **every** kopiur-protected
claim, on **both** the `ceph` and `r2` destinations. The captain chose a demonstrated restore
over file-count parity as the bar (2026-09-01) precisely so that no volume loses its second
backup engine on anything weaker.

Sibling documents:

- [`kopiur-restore-drill-2026-08-30.md`](kopiur-restore-drill-2026-08-30.md) - the procedure
  this run follows, and the Stage 2 `sabnzbd-config` proof.
- [`recyclarr-config-readable-check-2026-08-31.md`](recyclarr-config-readable-check-2026-08-31.md)
  - the CSI-clone technique reused here for claims with no readable live mount.
- [`restore-drill-2026-08-23.md`](restore-drill-2026-08-23.md) - the VolSync equivalent and
  the house standard both are built to.

## The gate

Each volume had to clear all four criteria:

1. **Readable at the mover identity** - zero entries unreadable by kopiur's mover uid/gid.
2. **Both destinations hold a real snapshot** - `lastSuccessfulSnapshot` non-`NEVER` on `ceph`
   and `r2`, with `filesNew` non-zero and equal to the live on-disk file count.
3. **A restore proof** - restore into a scratch claim and compare against live by per-file
   sha256 manifest, plus a mode/uid/gid manifest.
4. **`SecurityContextCompatible` where present** - its **absence is not a failure**. The
   condition is positive-only and is emitted only when the mover uid matches every container
   of every mounting pod; see the `scc-condition-waiver` precedent on `changedetection`.

## Method

Per claim, following the Stage 2 procedure:

1. Record a baseline: PVC uid + bound PV, and the mounting pod's `startTime`/`restartCount`.
2. Capture live manifest **L1** (`sha256` per file) and **L1meta** (`mode uid gid` per file
   and directory), both with `-xdev` and `lost+found` pruned.
3. Create a fresh on-demand `Snapshot` CR against **each** destination's `SnapshotPolicy`, so
   the two destinations are captured minutes apart rather than the ~10 h that separates the
   scheduled `ceph` (4-hourly) and `r2` (daily) runs. Comparing a 10-hour-old `r2` snapshot
   against a current live tree would manufacture spurious diffs on any active volume.
4. Capture live manifest **L2** after the snapshots complete. The **stable set** is
   `comm -12 L1 L2`: the files whose content did not change during the snapshot window. Every
   stable-set entry must come back byte-identical; anything outside it is legitimate drift.
5. Create one scratch `Restore` per destination, `source.fromPolicy` at `offset: 0`, into a
   brand-new PVC. Confirm `.status.resolved.kopiaSnapshotID` equals the snapshot from step 3.
6. Mount **both** restored PVCs read-only in one pod and recompute both manifests, which also
   diffs the two destinations against each other.
7. Re-check the baseline, then delete only the labelled drill artifacts.

Every object this run created carries `fm.homeops/restore-drill: kopiur-stage5`.

### Deviations from the Stage 2 procedure, and why

- **`recyclarr-config` and `ntfy` have no readable live mount.** `recyclarr-config` is
  unmounted between CronJob runs; `ntfy` is mounted only through two `subPath`s (`cache`,
  `lib`), so its pod never sees the claim root that kopiur actually backs up. For both, the
  live reference is a **CSI `VolumeSnapshot` clone** of the live claim mounted read-only -
  option 3 of the recyclarr readable-check document, chosen there for the same reason: a probe
  holding the live RWO attachment is a standing collision risk against the workload.
- **The mover identity is read from the live `SnapshotPolicy`**, never from component defaults
  and never inferred from the pod's `runAsUser` - Stage 3 established that `plex`,
  `tdarr-config` and `calibre-web-automated` run as uid 0 while owning files `2000:2000`, and
  `hermes` pins no `runAsUser` at all while owning 89k entries as `10000`.

## Safety properties of this run

- **No `Snapshot` CR was deleted.** A kopiur `Snapshot` owns its kopia snapshot through a
  finalizer, so deleting one deletes backup data. The 60 on-demand verification snapshots
  created here were left in place for normal retention to prune.
- **Restores only ever targeted new scratch PVCs.** `Restore.spec.target.pvc` creates a new
  claim and cannot address a live one.
- **The standing `*-kopiur-dst` passive populators were not touched.** They report `Pending`
  by design until Stage 5 repoints a claim's `dataSourceRef`.
- **`KOPIUR_WORKER_THREADS=2` was left alone.** The operator's global mover concurrency
  throttles this run; raising it for a drill would have been a production change.

## Results

_(table generated below)_
