# `media/plex` r2 restore proof at the standing 10Gi - 2026-09-12

> **Status: COMPLETE. The gate is CLOSED - `media/plex` restores from r2 at its standing
> 10Gi.** No manifest change was needed. No `Snapshot`, `SnapshotPolicy`,
> `SnapshotSchedule`, `ClusterRepository` or standing `Restore` was created, patched or
> deleted; the live claim was never touched. Scratch objects were removed and confirmed gone.

## Verdict

**PROVEN.** A restore from **r2** at **exactly the 10Gi that Git carries** completed in
**7m36s** and reproduced the snapshot exactly: **24,726 regular files** (= snapshot
`filesNew`) and **4,948,787,362 bytes** (= snapshot `sizeBytes`), both to the byte, with
**zero read errors**. The restored Plex database opens, replays its WAL, and returns real
library content. The peak kopia cache reached an estimated **47% of usable capacity**, so
the standing value is correct with roughly 2x headroom - and, because the snapshot is below
the ~6.2 GiB eviction plateau, that headroom is structural rather than lucky.

The claim that motivated this task - *10Gi is a prediction, not a demonstration* - is now
discharged. The prediction was right, and it was still worth running: the snapshot had grown
8% since the figure was chosen, and nothing in CI or in Flux would have reported that.

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

## The gate

Five criteria, the same standard the 2026-09-02 `ai/hermes` gate was held to. All five pass.

| # | Criterion | Result |
|---|---|---|
| 1 | Readable at the mover identity | **pass** - 0 read errors across 24,726 files |
| 2 | The restore reads the snapshot we identified, not another one | **pass** - `kopiaSnapshotID` matched |
| 3 | `Restore` reaches `Completed` at the **standing** cache value, from **r2** | **pass** - 10Gi, 7m36s |
| 4 | Content verified against a live reference | **pass** - identical path sets, 0 stable-set mismatches |
| 5 | Modes, ownership and file types verified | **pass** - 0 mode diffs, 0 type diffs, 3 explained owner diffs |

## Evidence

### The restore

| | |
|---|---|
| `Restore` | `media/plex-kopiur-drill-20260912-r2` |
| repository | **`r2`** (Cloudflare R2) |
| `credentialProjection` | `enabled: true` **in its own spec** - required; the component's `ssa: IfNotPresent` never reaches a new object |
| source | `fromPolicy: plex-r2`, `offset: 0` |
| resolved snapshot | `daecfb94352d63dca81b8eae03151c02` = `plex-r2-20260912190639` - **the snapshot identified in the baseline** |
| target | new PVC `plex-kopiur-drill-20260912-r2`, 20Gi, `ceph-block` |
| `onMissingSnapshot` | `Fail` (fail-closed; the standing populator uses `Continue`) |
| mover identity | `2000:2000` |
| **cache** | `mode: Ephemeral`, **`capacity: 10Gi`** - the value under test |
| result | **`Completed`**, 20:48:25 -> 20:56:01 UTC (mover Job 7m38s) |

### The cache curve

Sampled from the kubelet stats API on the mover pod's `kopia-cache` ephemeral volume
alongside the restore target (`source`). kubelet aggregates volume stats roughly every
90 s, so a 7.6-minute restore yields only three distinct readings:

| kubelet stats time | restore target | kopia cache | cache/target |
|---|--:|--:|--:|
| 20:51:31 | 2.842 GiB | 2.848 GiB | 100.2% |
| 20:52:58 | 3.116 GiB | 3.124 GiB | 100.2% |
| 20:54:53 | 3.536 GiB | **3.541 GiB** | 100.2% |

| | |
|---|--:|
| usable capacity at `10Gi` | 10,464,022,528 B = **9.745 GiB** |
| **measured** peak cache | 3.541 GiB = **36.3%** of usable |
| extrapolated final cache | 4.618 GiB = **47.4%** of usable |

**Read the measured peak as a lower bound, not the peak.** The last sample is 68 s before
completion, and the restore ran on to 4.609 GiB. What makes the extrapolation trustworthy
here rather than hand-waving is the ratio: the cache tracked the restore target at
**100.2% on every single sample**, so the final cache is the snapshot size times that
ratio. This is [finding 1 of the 2026-09-02 gate](kopiur-r2-restore-cache-gate-2026-09-02.md)
- the ~1:1 regime - reproduced on a second claim and a second destination, and it is tighter
here than on `hermes` (89-99%) because plex never reaches the eviction plateau at all.

**Why 10Gi is structurally right rather than narrowly right.** Required cache is
`min(snapshot sizeBytes, ~6.2 GiB)`. plex's snapshot is 4.609 GiB, below the plateau, so the
cache tracks the snapshot 1:1 - but the plateau is the ceiling for *any* snapshot size, and
9.745 GiB usable clears it by 57%. So even if this claim grows past 6.2 GiB, 10Gi still
holds, provided kopia's eviction behaves as measured. That is the reason not to lower it
toward the observed 4.6 GiB peak: the peak is a property of today's snapshot size, the
plateau is the property that has to be cleared.

### Completeness

Read through a `python:3.12-alpine` pod mounting the scratch PVC **read-only** as 2000:2000.

| measure | restored | expected | |
|---|--:|--:|---|
| regular files | **24,726** | 24,726 (snapshot `filesNew`) | **exact** |
| total bytes | **4,948,787,362** | 4,948,787,362 (snapshot `sizeBytes`) | **exact** |
| directories | 12,468 | 12,468 (live) | exact |
| symlinks | 7 | - | see below |
| entries | 37,201 | 37,201 (live) | exact |
| read errors | **0** | 0 | |
| content manifest sha256 | `f9daf6bdc0b170dd75917a7bbf167aa0b230e0ab3b6b70946ea2fbf94898f619` | | |
| mode/uid/gid manifest sha256 | `ff015307a575fc729d3af5850fd213624a72885b3ff1a316b2b3b676fedb1ec3` | | |

The counts and the byte total are printed beside the digests deliberately - `e3b0c442…b855`
is the sha256 of the empty string, and a manifest comparison that "matches" because both
sides are empty proves nothing.

**The 7-file discrepancy that isn't one.** A first pass counted 24,733 restored "files"
against a `filesNew` of 24,726. The 7 extra are **symlinks**, which `find -type f` and
kopia's `filesNew` both exclude and which a directory walk counts. All 7 are Intel GPU
driver links (`libigc.so`, `libiga64.so`, `libigdfcl.so`, the VA-API `iHD_drv_video.so`),
and all 7 restored with their targets intact. Regular files are 24,726 on both sides.
Worth stating because the instinct is to wave a 7-file gap through as rounding; it is not
rounding, it has an exact cause.

### Content, against a live reference

Live manifest **L1** captured 21:01:24-21:02:03 UTC (24,726 files, 0 errors), compared by
**path-keyed dictionary** rather than `sort` + `comm`.

| check | result |
|---|--:|
| live files / restored files | 24,726 / 24,726 |
| paths in both | **24,726** |
| present live, absent from restore | **0** |
| present in restore, absent live | **0** |
| digest mismatches among common paths | **4** |
| ... of those, in the stable set | **0** |

The path sets are **identical**. The 4 digest mismatches are exactly the app's own
live-write set, and the snapshot is ~2 h older than L1, so they are expected:

```
Library/.../Logs/Plex Media Server.log                  (append-only log)
Library/.../Databases/com.plexapp.plugins.library.db    (see below)
Library/.../Databases/com.plexapp.plugins.library.db-shm
Library/.../Databases/com.plexapp.plugins.library.db-wal
```

Three of the four churned again between L1 and a targeted L2 re-read six minutes later, so
they are not in the stable set. **The fourth did not, and was not waved through.**
`com.plexapp.plugins.library.db` was byte-identical across L1 and L2 while still differing
from the restore - the shape that trap (d) says to treat as a question rather than a
failure. Reading the SQLite headers settles it:

| | live | restored |
|---|--:|--:|
| page size | 1024 | 1024 |
| **file change counter** | **18763** | **18762** |
| database size (pages) | 67,110 | 67,110 |
| `version_valid_for` | 18763 | 18762 |

The restored database is **exactly one write transaction behind live**, at the same page
count, and each file's `version_valid_for` equals its own change counter - i.e. each is
internally consistent. That is a valid earlier state, which is precisely what a
point-in-time backup should hold. In WAL mode the main `.db` only changes at a checkpoint,
which is why it can sit still for six minutes and still differ from a two-hour-old snapshot.

### Modes, ownership and types

| check | result |
|---|--:|
| entries compared | **37,201** |
| **mode differences** | **0** |
| **file-type differences** | **0** |
| ownership differences | **3** - all explained |

Zero mode differences across the whole tree is the read-only-staging property that makes a
kopiur restore mode-faithful, where a VolSync restore relaxes every mode by a group-write
bit. The 3 ownership differences are both documented non-defects, reproduced here:

| entry | live | restored | why |
|---|---|---|---|
| `Library/` | `0:2000` | `2000:2000` | a non-root mover cannot reproduce a root-owned entry |
| `Library/Application Support/` | `0:2000` | `2000:2000` | same |
| `./` (volume root) | `2000:2000` | `0:2000` | the target root is provisioned by CSI and never restored by kopia |

None is a functional problem: the app runs as uid 0 with `fsGroup: 2000`, so it reaches all
three either way.

### Usability - what "restored" actually buys you

A byte-exact tree is necessary and not sufficient; the question a disaster asks is whether
Plex would come back. The restored databases were copied to writable scratch (so SQLite
could replay the WAL exactly as Plex does at startup) and exercised:

| | `library.db` | `library.blobs.db` |
|---|--:|--:|
| opened, WAL replayed | yes (69.6 MB WAL) | yes (39.6 MB WAL) |
| tables / indexes | 82 / 164 | 82 / 164 |
| **real tables fully scanned** | **73 / 73** | **73 / 73** |
| rows read | **364,217** | 1,878 |
| **foreign key violations** | **0** | **0** |

Real content in the restored library database:

```
movies    770      media_items  14,611      accounts  551
shows       5      media_parts  14,611      sections  3 (Movies, TV Shows, Music)
seasons    74
episodes 1,244     newest metadata added_at: 2026-09-11T17:19:13Z
```

**What was NOT checked, and why.** `PRAGMA integrity_check` **could not be run**. Plex
registers a custom FTS tokenizer (`collating`) that stock SQLite does not have, so the
pragma aborts with `unknown tokenizer: collating` as soon as it reaches one of the 7 virtual
tables. That is a limitation of the verifying tool, not a signal about the data. What was
run instead is a **full scan of all 73 real tables** - every row and index page read - plus
`foreign_key_check`, which together cover the b-tree structure of everything except the FTS
shadow tables. **The 7 FTS virtual tables are therefore unverified.** They are derived
search indexes that Plex rebuilds, so the practical exposure is low, but it is an unverified
region and is recorded as one rather than rounded up to "database verified".

One further honest note: a first pass reported a scan error on `play_queues`. That was the
verifier assuming TEXT is UTF-8; the column holds gzip-compressed binary (magic `1f8b08`,
decompresses cleanly). Re-read with `text_factory=bytes` it returns all 9 rows. **73/73**
above is the corrected figure.

### The live claim was never touched

| | before | after |
|---|---|---|
| PVC uid | `edec9098-5716-4e83-8f3c-3a02c778e4cc` | unchanged |
| bound PV | `pvc-f088c770-207a-4da3-8488-db49f1383ecb` | unchanged |
| phase / capacity | `Bound` / 20Gi | unchanged |
| pod `startTime` | `2026-09-05T13:10:05Z` | unchanged |
| container `restartCount` | 0 | 0 |
| pod `Ready` | True | True |

The mover Job mounted only its own scratch volumes:

```
kopia-cache => (ephemeral)
source      => plex-kopiur-drill-20260912-r2
```

Snapshot census was **30 plex / 925 cluster-wide** before and after: no `Snapshot` CR was
created, so no GFS eviction was triggered on the `keepHourly`-less r2 tier.

### Cleanup

`Restore` (no finalizers, no ownerReferences - deletion cannot cascade), scratch PVC and
verify pod all deleted and confirmed gone; the ephemeral cache PVC went with the mover pod.
Nothing carrying `fm.homeops/restore-drill=kopiur-plex-r2` remains cluster-wide, and no PV
was left `Released`. The standing populator `plex-kopiur-dst` is untouched (ceph, 10Gi,
`Pending`).

## Finding: the headroom is being consumed, and nothing reports it

This is worth recording independently of the pass, because the pass alone hides it.

| r2 snapshot | `filesNew` | `sizeBytes` | GiB |
|---|--:|--:|--:|
| 2026-09-08 | 24,250 | 4,869,454,689 | 4.535 |
| 2026-09-09 | 24,467 | 4,881,234,262 | 4.546 |
| 2026-09-10 | 24,584 | 4,898,769,978 | 4.562 |
| 2026-09-11 | 24,592 | 4,898,642,952 | 4.562 |
| 2026-09-12 | 24,726 | 4,948,787,362 | **4.609** |

The 10Gi was sized on 2026-09-04 against a **4.27 GiB** snapshot at 44% of usable. Eight
days later it is **4.609 GiB** at 47% - roughly **8% growth in the sizing window**, about
**0.019 GiB/day** over the measured five days.

Extrapolated naively, plex reaches the ~6.2 GiB plateau in roughly **80 days**. **That is
not a deadline**, and the arithmetic is the point: once the snapshot crosses the plateau the
requirement *stops* tracking the snapshot and pins at ~6.2 GiB, which 9.745 GiB usable
already clears. So growth moves this claim toward a **ceiling it has headroom for**, not
toward a cliff - the opposite of the `hermes` case, where the cache sat below the plateau
and every GiB of growth was real exposure.

What deserves attention is not the number but that **no part of this system would have told
anyone.** `KOPIUR_CACHE_CAPACITY` is asserted by CI as a literal string; nothing compares it
against the live snapshot size, Flux reports `Ready` either way, and the failure mode
appears only during a restore. The generic detector - alert when any claim's newest snapshot
`sizeBytes` exceeds some fraction of its configured cache - remains unbuilt, and is now the
strongest follow-up this area carries. It is cheap: both numbers are already in the cluster,
one on the `Snapshot` status and one on the `SnapshotPolicy` spec.

## What this does and does not settle

**Settles.** `media/plex` restores from r2 at the standing 10Gi, completely and usably. The
1:1 cache regime is confirmed on a second claim and second destination. Single-engine
retirement of this claim now rests on a demonstration rather than an inference.

**Does not settle.** The 7 FTS virtual tables are unverified (above). The measured cache
peak is a lower bound between 90-second samples, so the 47% figure is an extrapolation from
a very tight ratio rather than a direct reading. And the ceph-vs-r2 asymmetry that started
all of this is still not mechanistically explained - this run reproduces the *sizing model*,
not the underlying cause.
