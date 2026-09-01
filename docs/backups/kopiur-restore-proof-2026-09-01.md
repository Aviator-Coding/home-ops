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

_(table generated below)_
