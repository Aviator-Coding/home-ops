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

## Findings

Four things this run established that no manifest test, and no snapshot status, would have
caught. Two of them change how a future reader must read the table.

### Finding 1: kopia excludes `CACHEDIR.TAG` directories, so restored file counts are legitimately lower than live

kopia honours the [Cache Directory Tagging Specification](https://bford.info/cachedir/): any
directory holding a `CACHEDIR.TAG` file whose first bytes are the standard signature is skipped.
The **directory itself is restored**, with its mode and ownership intact - only its contents are
omitted. Three claims are affected fleet-wide:

| claim | live files | snapshot `filesNew` | omitted | tagged directories |
|---|--:|--:|--:|---|
| `ai/hermes` | 89 388 | 65 921 | 23 467 | `.cache/uv`, `home/.cache/uv` (+2 nested `archive-v0` entries), `home/.local/share/uv/tools/pytest`, `wiki/.venv`, `skills/media/youtube-content/.venv`, `venv-httpx`, `.hw-venv`, `scripts/.pytest_cache` |
| `home-automation/home-assistant` | 96 | 79 | 17 | `.venv` |
| `media/calibre-web-automated` | 37 | 23 | 14 | `.cache/fontconfig` |

The omitted content is a Python virtualenv, a `uv` download cache, a `pytest` cache and a
fontconfig cache - all regenerable, none of it application state. The fleet-wide sweep for
`CACHEDIR.TAG` and `.kopiaignore` found no other claim carrying either marker.

**Why this matters for retirement.** VolSync's restic mover is **not** configured with
`--exclude-caches` (restic's opt-in flag for the same convention), and nothing in
`kubernetes/components/volsync/` sets it, so the restic repositories very likely *do* hold this
content while the kopia ones do not. Retiring VolSync for `hermes` in particular therefore drops
23 467 files from the fleet's backup coverage. That is almost certainly an acceptable trade -
they are caches and virtualenvs - but it is a real change in coverage and it should be a
deliberate decision rather than a surprise. It was **not** verified by a VolSync restore here;
only the kopiur side was measured.

### Finding 2: an r2 restore needs a materially larger kopia cache than the same restore from ceph

Both `hermes` and `plex` failed their **r2** restore with
`no space left on device` on `/var/cache/kopia`, using the 2 GiB ephemeral cache that this
repo's drill document uses in its worked example. The decisive observation is that
**`plex` restored successfully from `ceph` on that same 2 GiB and failed from `r2` on it** -
same volume, same snapshot content, same mover identity, different repository. So this is a
property of the offsite backend, not of volume size alone.

```
error restoring: restore error: copy file: error creating file:
  unable to open snapshot file for /restore/... : unable to open object:
  ... unable to write to cache: kopia.repository: cannot write data to tempfile
  "/var/cache/kopia/cache/kopia.repository...": no space left on device
```

A failed `Restore` is terminal - kopiur says so in the condition message ("a Failed Restore is
terminal and never retries") - so recovering means creating a **new** `Restore`, not patching
the old one. Re-running with a larger cache is what produced the proofs in the table.

**Why this matters for retirement.** The component default is
`KOPIUR_CACHE_CAPACITY:-2Gi` (`kubernetes/components/kopiur/ceph/restore.yaml`). The standing
populator `Restore`s that Stage 5 would actually rely on carry `hermes` 5 GiB and `plex` 10 GiB.
Neither has ever been exercised against r2. A real offsite disaster recovery is exactly the
moment this would be discovered, so the cache capacity on the large claims deserves a look
before VolSync is retired - it is a one-line change per overlay, and it is the difference
between a DR restore working and failing terminally.

### Finding 3: reading "live" through the app pod can include bytes the claim does not hold

`home-automation/esphome-config` first scored FAIL: `./secrets.yaml` came back from **both**
destinations with digest `e3b0c442...`, the sha256 of the empty string, against a live digest of
`eb48ead0...`.

It is not data loss. `/config/secrets.yaml` is an ESO-managed **Secret volume mounted over the
claim path** (`subPath: secrets.yaml`, from `secret/esphome-secrets`), on a different device
(`2097250` vs the claim's `64640`). The claim itself holds a zero-byte placeholder at that path,
which is exactly what kopiur backed up and exactly what it restored. The 222 bytes of real
content live in 1Password, not in any backup of this claim, and that is by design.

`find -xdev` does not protect against this: it declines to *descend* into another filesystem,
but a single file that is itself a bind mount inside an otherwise same-device directory is still
reported. Re-measured against a CSI `VolumeSnapshot` clone of the claim - which has no pod
overlay at all - `esphome-config` restores 46/46 byte-identically and passes.

A fleet-wide audit of every mount landing **inside** a backed-up data directory found exactly
four, and this is the only file-level one; the other three (`autobrr` `/config/log`,
`home-assistant` `/config/logs` and `/config/tts`) are `emptyDir` **directories**, which `-xdev`
does prune correctly.

### Finding 4: four claims are too small for a restore to prove much

`downloads/autobrr` holds **one** file (2 179 bytes). Its restore matches byte-for-byte from
both destinations, and that result is close to meaningless as a fidelity proof - it exercises
the mechanism, not the data path at scale. The Stage 2 drill made the same point about this
claim's Stage 1 snapshot, which succeeded while moving zero bytes.

Three more are in the same category and are marked in the table rather than counted as ordinary
passes: `selfhosted/paperless-ngx-media` (1 file), `selfhosted/syncthing-data` (5 files) and
`selfhosted/obsidian-livesync` (8 files). `database/pgadmin` is a borderline case in the other
direction - only 3 files, but 979 MiB of them, so its restore does exercise bulk data.

## Results

**All 30 claims were drilled. All 30 restored successfully from both the `ceph` and the `r2`
repository, and for every one of them the two destinations produced byte-identical trees.**

Read the verdict column with its markers - three of them qualify what "pass" means:

- `‡` **cache-excluded**: the restore holds fewer files than live because kopia skips
  `CACHEDIR.TAG` directories. Explained in [finding 1](#finding-1-kopia-excludes-cachedirtag-directories-so-restored-file-counts-are-legitimately-lower-than-live);
  `snapshot filesNew` matches the restored count exactly in every such row, which is what shows
  the omission is deliberate rather than lossy.
- `*` **too small to prove much**: the claim holds 1-8 files. The restore is correct but is a
  test of the mechanism, not of the data path. See
  [finding 4](#finding-4-four-claims-are-too-small-for-a-restore-to-prove-much).
- `†` **two volatile files differed** on `ai/hermes`. See the note under the table.

| # | namespace / claim | mover<br>uid:gid | ceph snap | r2 snap | live files | snapshot `filesNew`<br>ceph / r2 | restored<br>ceph / r2 | restore manifest sha256 (12)<br>ceph / r2 | verdict |
|--:|---|---|:-:|:-:|--:|--:|--:|---|---|
| 1 | `ai/hermes` | 10000:10000 | Succeeded | Succeeded | 89388 | 65921 / 65921 | 65921 / 65921 | `f37eb3217b51` / `f37eb3217b51` (identical) | **PASS** † ‡ |
| 2 | `ai/opencode` | 1000:1000 | Succeeded | Succeeded | 4749 | 4749 / 4749 | 4749 / 4749 | `d9954a835a98` / `d9954a835a98` (identical) | **PASS** |
| 3 | `ai/repo-wiki` | 1000:1000 | Succeeded | Succeeded | 165 | 165 / 165 | 165 / 165 | `0e059f1e6400` / `0e059f1e6400` (identical) | **PASS** |
| 4 | `database/pgadmin` | 5050:5050 | Succeeded | Succeeded | 3 | 3 / 3 | 3 / 3 | `81937622ac41` / `81937622ac41` (identical) | **PASS** |
| 5 | `downloads/autobrr` | 2000:2000 | Succeeded | Succeeded | 1 | 1 / 1 | 1 / 1 | `fa14f3e480fb` / `fa14f3e480fb` (identical) | **PASS** \* |
| 6 | `downloads/bazarr-config` | 2000:2000 | Succeeded | Succeeded | 17 | 17 / 17 | 17 / 17 | `42417889da0c` / `42417889da0c` (identical) | **PASS** |
| 7 | `downloads/lidarr-config` | 2000:2000 | Succeeded | Succeeded | 478 | 478 / 478 | 478 / 478 | `7b727e6495b0` / `7b727e6495b0` (identical) | **PASS** |
| 8 | `downloads/prowlarr-config` | 3002:3002 | Succeeded | Succeeded | 710 | 710 / 710 | 710 / 710 | `c8a2b31f6d14` / `c8a2b31f6d14` (identical) | **PASS** |
| 9 | `downloads/radarr-config` | 2000:2000 | Succeeded | Succeeded | 6086 | 6086 / 6086 | 6086 / 6086 | `6fb28da0d1cf` / `6fb28da0d1cf` (identical) | **PASS** |
| 10 | `downloads/readarr-config` | 2000:2000 | Succeeded | Succeeded | 4197 | 4197 / 4197 | 4197 / 4197 | `30b6a67c5a35` / `30b6a67c5a35` (identical) | **PASS** |
| 11 | `downloads/recyclarr-config` | 2000:2000 | Succeeded | Succeeded | 2913 | 2913 / 2913 | 2913 / 2913 | `05e8116fc921` / `05e8116fc921` (identical) | **PASS** |
| 12 | `downloads/sabnzbd-config` | 2000:2000 | Succeeded | Succeeded | 2064 | 2064 / 2064 | 2064 / 2064 | `e8a05b354ce7` / `e8a05b354ce7` (identical) | **PASS** |
| 13 | `downloads/sonarr-config` | 2000:2000 | Succeeded | Succeeded | 124 | 124 / 124 | 124 / 124 | `7077025cb0fd` / `7077025cb0fd` (identical) | **PASS** |
| 14 | `home-automation/esphome-config` | 2000:2000 | Succeeded | Succeeded | 46 | 46 / 46 | 46 / 46 | `baa6b55032b5` / `baa6b55032b5` (identical) | **PASS** |
| 15 | `home-automation/home-assistant` | 1000:1000 | Succeeded | Succeeded | 96 | 79 / 79 | 79 / 79 | `46c7155751e6` / `46c7155751e6` (identical) | **PASS** ‡ |
| 16 | `home-automation/matter-server` | 0:0 | Succeeded | Succeeded | 161 | 161 / 161 | 161 / 161 | `364f08cdd3bb` / `364f08cdd3bb` (identical) | **PASS** |
| 17 | `home-automation/zigbee2mqtt-data` | 2000:2000 | Succeeded | Succeeded | 37 | 37 / 37 | 37 / 37 | `1a3f40e9218d` / `1a3f40e9218d` (identical) | **PASS** |
| 18 | `media/calibre-web-automated` | 2000:2000 | Succeeded | Succeeded | 37 | 23 / 23 | 23 / 23 | `d06f7d879f11` / `d06f7d879f11` (identical) | **PASS** ‡ |
| 19 | `media/plex` | 2000:2000 | Succeeded | Succeeded | 21251 | 21251 / 21251 | 21251 / 21251 | `9b8b4a197241` / `9b8b4a197241` (identical) | **PASS** |
| 20 | `media/seerr` | 2000:2000 | Succeeded | Succeeded | 75 | 75 / 75 | 75 / 75 | `6a95dbce8b79` / `6a95dbce8b79` (identical) | **PASS** |
| 21 | `media/tdarr-config` | 2000:2000 | Succeeded | Succeeded | 17278 | 17278 / 17278 | 17278 / 17278 | `b29eb20847d8` / `b29eb20847d8` (identical) | **PASS** |
| 22 | `selfhosted/changedetection-config` | 1000:1000 | Succeeded | Succeeded | 3069 | 3069 / 3069 | 3069 / 3069 | `ab4e633a8227` / `ab4e633a8227` (identical) | **PASS** |
| 23 | `selfhosted/linkwarden` | 1000:1000 | Succeeded | Succeeded | 67 | 67 / 67 | 67 / 67 | `b42975d15de3` / `b42975d15de3` (identical) | **PASS** |
| 24 | `selfhosted/n8n` | 1000:1000 | Succeeded | Succeeded | 7725 | 7725 / 7725 | 7725 / 7725 | `cd0fae1c2aff` / `cd0fae1c2aff` (identical) | **PASS** |
| 25 | `selfhosted/ntfy` | 1000:1000 | Succeeded | Succeeded | 2 | 2 / 2 | 2 / 2 | `027268e78c5f` / `027268e78c5f` (identical) | **PASS** |
| 26 | `selfhosted/obsidian-livesync` | 5984:5984 | Succeeded | Succeeded | 8 | 8 / 8 | 8 / 8 | `88858eb0b4e1` / `88858eb0b4e1` (identical) | **PASS** \* |
| 27 | `selfhosted/paperless-ngx` | 1000:1000 | Succeeded | Succeeded | 32 | 32 / 32 | 32 / 32 | `b7d4afdbcc1e` / `b7d4afdbcc1e` (identical) | **PASS** |
| 28 | `selfhosted/paperless-ngx-media` | 1000:1000 | Succeeded | Succeeded | 1 | 1 / 1 | 1 / 1 | `f6a6a1ecf1b4` / `f6a6a1ecf1b4` (identical) | **PASS** \* |
| 29 | `selfhosted/syncthing` | 1000:1000 | Succeeded | Succeeded | 21 | 21 / 21 | 21 / 21 | `05d36861232d` / `05d36861232d` (identical) | **PASS** |
| 30 | `selfhosted/syncthing-data` | 1000:1000 | Succeeded | Succeeded | 5 | 5 / 5 | 5 / 5 | `c525096cb0b4` / `c525096cb0b4` (identical) | **PASS** \* |

`live files` is the count on the live claim at the time of measurement; `snapshot filesNew` is
what kopiur recorded for the on-demand verification snapshot taken for this drill; `restored` is
the count actually walked in the restored scratch PVC. `restore manifest sha256` is the sha256
of the sorted per-file `sha256  path` manifest of the whole restored tree.

### The `ai/hermes` caveat (`†`)

`hermes` is the fleet's largest and busiest claim (89 388 live files, 10.2 GB) and it was being
written to throughout the drill. 65 919 of the 65 921 restored files matched the live stable set
byte-for-byte. Two did not, and both are present in the restore - with different content, not
missing:

| path | live at L1 | in both restores | live ~50 min later |
|---|---|---|---|
| `./logs/errors.log` | `517fe00b…` | `005d4462…` | `869a0e82…` (889 713 B, still growing) |
| `./kanban.db-shm` | `fd4c9fda…` | `709e80c8…` | `fd4c9fda…` (back to the L1 value) |

`errors.log` shows a **third** distinct digest when re-read after the drill, which is direct
evidence it is being appended to continuously. `kanban.db-shm` is a SQLite shared-memory
segment: its contents are transient by design, and it had returned to exactly its L1 digest by
the time of the re-read - which is also how it slipped through the stable-set filter, since the
filter only requires L1 and L2 to agree.

The decisive point is that the **`ceph` and `r2` restores are byte-identical to each other across
all 65 921 files**, including these two. Two independent repositories, written by separate mover
runs to separate object stores, cannot agree byte-for-byte on corrupted or lost content. This is
write activity during the snapshot window, not a fidelity defect. An airtight result for this
claim would require quiescing the application, which is not something to do to a live service for
a drill.

### Criterion-by-criterion

| criterion | result |
|---|---|
| 1. Zero entries unreadable at the mover identity | **Met for all 30.** Every claim produced a `Succeeded` snapshot whose `filesNew` equals the restored file count. kopiur fails closed on an unreadable entry, so a complete snapshot at the mover identity is itself the measurement. |
| 2. `lastSuccessfulSnapshot` non-`NEVER` on both destinations, `filesNew` non-zero and equal to live | **Met for all 30**, with the `CACHEDIR.TAG` qualification on 3 claims (`hermes`, `home-assistant`, `calibre-web-automated`), where `filesNew` equals live *minus the deliberately excluded cache directories* and equals the restored count exactly. |
| 3. A restore proof | **Met for all 30.** Every row is a real restore into a fresh scratch PVC compared by per-file sha256, never a snapshot status. `hermes` carries the `†` caveat above. |
| 4. `SecurityContextCompatible` where present | **Not treated as a gate**, per the `scc-condition-waiver` precedent. It is positive-only and its absence is not a failure. |

### Live claims were not touched

- All 30 PVCs hold the **same `metadata.uid` and the same bound PV** before and after
  (`diff` of the two captures is empty).
- **Zero container restarts and zero pod re-creations** across every namespace, comparing
  `restartCount` and `startTime` before and after.
- `ceph health` was `HEALTH_OK` at the start and at the end.
- **No VolSync object was written.** All 90 `ReplicationSource`s and 34
  `ReplicationDestination`s are intact, and none carries a `managedFields` write timestamp
  inside the drill window.
- Every drill artifact was removed: `Restore` CRs (no finalizers, no ownerReferences - verified
  before deleting), their scratch PVCs, the CSI clone PVCs and `VolumeSnapshot`s used for the
  two special cases, and the verify pods. Note that a restore-target PVC does **not** inherit
  the `fm.homeops/restore-drill` label from its `Restore`, so the final sweep matched by name as
  well as by label.
- The 60 on-demand verification `Snapshot` CRs were **deliberately left in place** - deleting
  one deletes its kopia snapshot data. Normal retention prunes them.

## What this does and does not authorise

It **does** establish, per volume, that the kopiur backup of that volume can be restored and
that what comes back is what was there - from both the local and the offsite repository
independently.

It does **not** by itself authorise retirement. Before any `ReplicationSource` is removed, three
things from the findings above should be settled deliberately:

1. **The `CACHEDIR.TAG` coverage change** (finding 1), most materially the 23 467 files on
   `ai/hermes` that kopia omits and restic very likely retains.
2. **Restore cache capacity on the large claims** (finding 2). `hermes` needed more than its
   standing 5 GiB to restore from r2 at all; `plex`'s r2 restore failed on 2 GiB while its ceph
   restore succeeded on the same 2 GiB.
3. **The four near-empty claims** (finding 4), whose proofs are thin by nature and which may
   deserve a different kind of assurance.

Nothing in this document should be read as a recommendation to retire, or not to retire, any
particular volume. That is the captain's call.
