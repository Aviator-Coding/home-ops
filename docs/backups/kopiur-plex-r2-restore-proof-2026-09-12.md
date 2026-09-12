# `media/plex` r2 restore proof at the standing 10Gi - 2026-09-12

> **Status: IN PROGRESS.** Baseline and method are recorded below; the restore itself
> had not yet run when this section was committed. The verdict section is filled in
> from the run, not from the prediction.

Closes the single strongest follow-up left open by wave three of the kopiur Stage 5
retirement: [`kopiur-wave-three-retirement-2026-09-04.md`](kopiur-wave-three-retirement-2026-09-04.md)
open item 1 - *"`media/plex`'s 10Gi restore cache has never been r2-exercised"*.

## Why this claim, and what exactly was unproven

`media/plex` is the volume that produced the r2 cache finding in the first place. On
2026-09-01 it restored from **ceph** on a 2 GiB cache and **failed from r2 on that same
2 GiB** with `no space left on device` on `/var/cache/kopia` - same volume, same snapshot
content, same mover identity, different repository. That is
[finding 2 of the fleet restore proof](kopiur-restore-proof-2026-09-01.md).

Be precise about what was and was not already proven, because the record is easy to
misread:

| | status before this run |
|---|---|
| r2 restore **content fidelity** | **proven** 2026-09-01 (fleet proof row 19: 21,251/21,251 files from both destinations, identical manifest digest `9b8b4a197241`) - but that r2 restore ran on a **20 GiB** cache, after failing at 2 GiB |
| r2 restore at the **standing 10Gi** | **never exercised** - predicted safe by the 2026-09-02 model, never demonstrated |

So the open question was never "does plex restore from r2" - it was "does it restore from
r2 at the capacity Git actually carries". Since wave three the claim is **single-engine**:
there is no VolSync fallback, and a failed kopiur `Restore` is terminal and never retries.

## Baseline - measured live 2026-09-12, before anything was created

| | |
|---|---|
| claim | `media/plex`, 20Gi, `ceph-block`, PV `pvc-f088c770-207a-4da3-8488-db49f1383ecb` |
| mounting pod | `plex-7d8fc8dc9f-gzgmf` on `talos-1`, container `app`, `/config`, no `subPath` |
| pod `startTime` | `2026-09-05T13:10:05Z`, `restartCount` 0 |
| pod securityContext | `runAsUser: 0`, `fsGroup: 2000` - the mover identity comes from FILE ownership, not this |
| mover identity (live `plex-r2` policy) | `runAsUser`/`runAsGroup`/`fsGroup` = **2000:2000** |
| live tree | **24,726 files** + 12,468 dirs = 37,194 entries, `du -sh` = 4.8G |
| standing populator `plex-kopiur-dst` | cache **10Gi**, identity 2000:2000, `credentialProjection: true` - **no drift against Git** |

Nothing is mounted *inside* `/config` (`/data/nas-media`, `/tmp`, `/transcode` are all
siblings), so [finding 3 of the fleet proof](kopiur-restore-proof-2026-09-01.md) - an ESO
Secret mounted over a path within the data dir - does not apply, and `-xdev` plus a
`lost+found` prune is a sufficient reading of "live".

### The snapshot under test, and the growth that matters

Restore reads `fromPolicy: plex-r2, offset: 0` = the newest r2 snapshot,
`plex-r2-20260912190639` / kopia `daecfb94352d63dca81b8eae03151c02`, taken 19:06 UTC.

| `filesNew` | `sizeBytes` | |
|--:|--:|---|
| **24,726** | **4,948,787,362** | 4.609 GiB |

Live file count equals `filesNew` **exactly** (24,726 = 24,726), which is the signal that
this claim has **no `CACHEDIR.TAG` omissions** - unlike `ai/hermes`, where 23,467 files sit
under cache tags and are legitimately absent from the restore. For plex, a complete restore
must reproduce the whole live file count.

**The claim has grown since the figure the 10Gi was sized against**, which is the reason
this was worth running rather than closing on the model:

| r2 snapshot | `filesNew` | `sizeBytes` | GiB |
|---|--:|--:|--:|
| 2026-09-08 | 24,250 | 4,869,454,689 | 4.535 |
| 2026-09-09 | 24,467 | 4,881,234,262 | 4.546 |
| 2026-09-10 | 24,584 | 4,898,769,978 | 4.562 |
| 2026-09-11 | 24,592 | 4,898,642,952 | 4.562 |
| 2026-09-12 | 24,726 | 4,948,787,362 | **4.609** |

The retirement record sized 10Gi against a **4.27 GiB** snapshot at 44% of usable. At
4.609 GiB that is **47%** of the 9.74 GiB usable - still inside the 1:1 regime, still below
the ~6.2 GiB plateau, but the margin has narrowed by measurable growth of ~0.02 GiB/day.

## Method

Follows the Stage 2 procedure ([`kopiur-restore-drill-2026-08-30.md`](kopiur-restore-drill-2026-08-30.md))
and the evidence standard of the [2026-09-02 r2 cache gate](kopiur-r2-restore-cache-gate-2026-09-02.md).

1. Record the baseline above (claim uid, bound PV, pod `startTime`/`restartCount`).
2. Read the mover identity from the **live `SnapshotPolicy`**, never from component defaults
   and never inferred from the pod's `runAsUser` (which is 0 here and would be wrong).
3. Capture live manifest **L1** (sha256 per file) and **L1meta** (`mode uid gid type` per
   entry), `-xdev`, `lost+found` pruned.
4. Create **one** scratch `Restore` from **r2** into a brand-new PVC, at
   `cache.capacity: 10Gi` - **exactly the value Git carries**. A proof at a larger number
   would not close the gate.
5. Sample the mover's kopia cache volume throughout the restore via the kubelet stats API,
   to record the peak and therefore the remaining margin.
6. Capture live manifest **L2** after the restore. The **stable set** is the set of paths
   whose digest is identical in L1 and L2 - the files that did not move during the run.
7. Mount the restored PVC read-only and recompute both manifests.
8. Re-check the baseline, then delete only the scratch artifacts.

Every object created carries `fm.homeops/restore-drill: kopiur-plex-r2`.

### Two deliberate choices, and why

**No `Snapshot` CR is created.** The drill restores from the existing scheduled r2
snapshot. Creating an on-demand r2 snapshot is not a read-only act: the `plex-r2` retention
is `keepDaily: 30` / `keepWeekly: 12` / `keepMonthly: 12` with **no `keepHourly` tier**, so
only the latest snapshot of each day survives and a new one would evict today's existing
newest. On a single-engine claim that is a real backup deletion bought for no gain - the
snapshot's own recorded `filesNew`/`sizeBytes` are what the completeness check needs. This
is trap (e) of the kopiur skill, and the wave-two reproof established the preference for
restoring from an existing scheduled snapshot.

**The live reference is bracketed after the fact, and its limit is stated rather than
glossed.** Because no verification snapshot is taken, the L1/L2 bracket sits *hours* after
the snapshot rather than minutes around it. Files that changed between the snapshot and L1
will therefore appear as mismatches even when the restore is perfect. That makes the live
comparison a **corroborating** check here, not the primary one - it is read for the shape of
each difference (append-only prefix, SQLite ephemerals - see finding (d) of the 2026-09-02
gate), not as a pass/fail count. The primary completeness evidence is the restored tree
against the snapshot's own `filesNew` and `sizeBytes`, which is exact and which a
cache-starved restore cannot fake: the failure mode is a terminal error, not a silent
truncation.

### Comparisons are path-keyed

Per the 2026-09-02 deviation: comparisons use path-keyed dictionaries rather than `sort` +
`comm`, which removes the locale-collation hazard that produced a phantom 10-file gap in the
2026-09-01 run structurally rather than by remembering to export `LC_ALL=C` in both places.
