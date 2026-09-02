# kopiur per-volume restore proof - 2026-09-01

> **Status: evidence base only. Nothing was retired by this exercise.**
> No `ReplicationSource`, VolSync claim, or VolSync configuration was removed or modified
> anywhere. Every VolSync source remains live and untouched. This document is the evidence
> that a later, separate, irreversible retirement step is entitled to rely on.

> **That step has since happened, for four volumes.** On 2026-09-01, after this document merged,
> VolSync was retired from `ai/repo-wiki`, `downloads/recyclarr-config`,
> `downloads/sabnzbd-config` and `media/seerr` - a deliberate pilot on low-stakes targets, not a
> fleet-wide retirement. **Those four claims now have kopiur as their only backup engine; the
> other 26 remain dual-engine.** Selection rationale, the retirement mechanics, and the
> post-retirement re-proof are in
> [`kopiur-stage5-pilot-retirement-2026-09-01.md`](kopiur-stage5-pilot-retirement-2026-09-01.md);
> the machine-readable record of which claims are single-engine is `RETIRED_CLAIMS` in
> `scripts/ci/kopiur-stage3-test.py`. Nothing in the results below was re-measured or amended by
> that exercise. **Finding 2 has since been closed for `ai/hermes`** (2026-09-02, raised to
> 16Gi and re-proven from r2 at that value):
> [`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md),
> which is now the authority on cache sizing. `media/plex`'s standing 10Gi is predicted safe by
> that run's measurement but has not itself been exercised against r2.

This is the Stage 5 prerequisite: a per-volume restore proof for **every** kopiur-protected
claim, on **both** the `ceph` and `r2` destinations. The captain chose a demonstrated restore
over file-count parity as the bar (2026-09-01) precisely so that no volume loses its second
backup engine on anything weaker.

Sibling documents:

- [`kopiur-restore-drill-2026-08-30.md`](kopiur-restore-drill-2026-08-30.md) - the procedure
  this run follows, and the Stage 2 `sabnzbd-config` proof.
- [`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md) -
  closes finding 2 for `ai/hermes`; authority on `KOPIUR_CACHE_CAPACITY` sizing.
- [`recyclarr-config-readable-check-2026-08-31.md`](recyclarr-config-readable-check-2026-08-31.md)
  - the CSI-clone technique reused here for claims with no readable live mount.
- [`restore-drill-2026-08-23.md`](restore-drill-2026-08-23.md) - the VolSync equivalent and
  the house standard both are built to.

## Bottom line for Stage 5

**All 30 claims restored from both `ceph` and `r2`.** Destination trees are byte-identical except `downloads/prowlarr-config`, whose re-drill restored two different snapshot points (see the prowlarr note).

Two results decide whether that is enough to retire VolSync, and they point in different
directions:

> ### 1. kopia omits `CACHEDIR.TAG` content and restic does not. On `ai/hermes` that is a 23 467-file gap - and it is **not** a loss.
>
> **Adjudicated: nothing irreplaceable sits under a `CACHEDIR.TAG` on `hermes`. kopiur is a
> faithful replacement for VolSync on that volume, not a lossy one.** The evidence is in
> [finding 1](#finding-1-kopia-excludes-cachedirtag-directories---adjudicated-no-irreplaceable-data-is-affected);
> the short form is that all 23 467 files are Python virtualenvs and `uv`/`pytest` caches that
> those tools tagged as caches themselves, containing only public PyPI packages and no
> user-authored file anywhere.
>
> ### 2. A real offsite restore needs more kopia cache than any of our claims are configured with. Fix this **before** retiring anything. *(closed for `ai/hermes` on 2026-09-02)*
>
> `media/plex` restored from `ceph` on a 2 GiB cache and **failed from `r2` on that same
> 2 GiB**. `ai/hermes` needed more than the 5 GiB its standing populator then carried. A failed
> `Restore` is terminal and never retries. **Closed for `ai/hermes`** (16Gi, r2-proven) -
> authority
> [`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md);
> discovery record:
> [finding 2](#finding-2-an-r2-restore-needs-a-materially-larger-kopia-cache-than-the-same-restore-from-ceph---an-operational-prerequisite-for-dr).
> `media/plex` 10Gi is predicted safe but not itself r2-exercised.

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
  `hermes` pins no `runAsUser` at all while owning 89k entries as `10000`. One harness trap
  bit the original `prowlarr-config` row: the drill derived the mover gid from the uid, which
  is correct for 29 of 30 claims (their policies use uid==gid) and wrong only for
  `prowlarr-config`, the single fleet claim where they differ (`3002:3000`). That claim was
  re-drilled at the true policy identity after the gate opened; see the prowlarr note under
  the table.
- **Compare manifests under a single collation.** Sorting with `LC_ALL=C sort` and then
  comparing with a `comm` that inherits a UTF-8 locale produces **silent false differences**:
  the two tools disagree on ordering, so `comm` reports paths as missing that are present in
  both inputs. This bit the `hermes` analysis mid-run and manufactured a phantom 10-file gap
  under `./scripts/` before being caught. Export `LC_ALL=C` for the whole comparison, and
  sanity-check the result against the arithmetic - the corrected `hermes` exclusion set is
  exactly 23 467 paths, reconciling with 89 388 - 65 921, which is what confirmed the fix.

## Safety properties of this run

- **No `Snapshot` CR was deleted.** A kopiur `Snapshot` owns its kopia snapshot through a
  finalizer, so deleting one deletes backup data. The 60 on-demand verification snapshots
  created here were left in place for normal retention to prune.
- **Restores only ever targeted new scratch PVCs.** `Restore.spec.target.pvc` creates a new
  claim and cannot address a live one.
- **The standing `*-kopiur-dst` passive populators were not touched.** They report `Pending`
  by design until a rebuilt claim from `components/kopiur/pvc` claims them - bound claims never
  repoint (`dataSourceRef` is immutable).
- **`KOPIUR_WORKER_THREADS=2` was left alone.** The operator's global mover concurrency
  throttles this run; raising it for a drill would have been a production change.

## Findings

Four things this run established that no manifest test, and no snapshot status, would have
caught. Two of them change how a future reader must read the table.

### Finding 1: kopia excludes `CACHEDIR.TAG` directories - adjudicated, no irreplaceable data is affected

kopia honours the [Cache Directory Tagging Specification](https://bford.info/cachedir/): a
directory holding a `CACHEDIR.TAG` whose first bytes carry the standard signature is skipped.
The **directory itself is restored**, mode and ownership intact; only its contents are omitted.
Three claims are affected fleet-wide:

| claim | live files | snapshot `filesNew` | omitted |
|---|--:|--:|--:|
| `ai/hermes` | 89 388 | 65 921 | 23 467 |
| `home-automation/home-assistant` | 96 | 79 | 17 |
| `media/calibre-web-automated` | 37 | 23 | 14 |

VolSync's restic mover is **not** configured with `--exclude-caches` (restic's opt-in flag for
the same convention) and nothing in `kubernetes/components/volsync/` sets it, so the restic
repositories almost certainly *do* hold this content. Retiring VolSync therefore drops it from
the fleet's backup coverage. The question that decides Stage 5 for `hermes` is not whether the
gap exists - it does - but whether anything in it matters.

#### The adjudication: it does not

**1. Every omitted file is under a tagged root. Zero are outside.** Comparing the live path set
against the restored path set gives exactly 23 467 omitted paths, reconciling precisely with
89 388 − 65 921, and filtering that set against the ten tagged roots leaves **0** entries.

**2. All ten tags are genuine.** Every `CACHEDIR.TAG` on the volume carries the standard
signature `8a477f597d28d172789f06886806bc55`, and each was written by the tool that owns the
directory (`uv`, `pytest`) - each root also carries the `.gitignore` those tools drop in. These
are not stray or hand-placed tags shadowing real data.

**3. Every root is tool-generated machinery**, by file count:

| tagged root | omitted files | what it is |
|---|--:|---|
| `./home/.cache/uv` | 21 659 | `uv` download/wheel cache (`wheels-v6`, `sdists-v9`, `simple-v21`, …) |
| `./home/.local/share/uv/tools/pytest` | 596 | `uv`-managed `pytest` tool venv |
| `./wiki/.venv` | 398 | Python venv |
| `./.cache/uv` | 227 | `uv` cache |
| `./skills/media/youtube-content/.venv` | 202 | Python venv |
| `./.hw-venv` | 191 | Python venv |
| `./venv-httpx` | 189 | Python venv |
| `./scripts/.pytest_cache` | 5 | `pytest` cache |

(The two `home/.cache/uv/archive-v0/*` roots are nested inside `home/.cache/uv` and their 14 319
files are counted once, within its 21 659.) Every venv is stock
`bin/ lib/ lib64/ pyvenv.cfg`; every cache is `uv`'s or `pytest`'s own layout.

**4. Nothing user-authored is inside any of them.** Searching all four venvs for files outside
the standard machinery (`site-packages/`, `bin/`, `pyvenv.cfg`, `.gitignore`, `.lock`,
`CACHEDIR.TAG`) returns nothing.

**5. The contents are public packages, and the largest venv is declaratively reconstructible.**
`wiki/.venv` has both `wiki/pyproject.toml` and `wiki/uv.lock` beside it, and **both are present
in the kopiur restore** - so it rebuilds with `uv sync`. The other three venvs
(`skills/media/youtube-content/.venv`, `venv-httpx`, `.hw-venv`) have no sibling manifest, but
they hold only public PyPI distributions - `httpx`/`anyio`/`h11`/`certifi`/`idna` (14 entries
each; `venv-httpx` and `.hw-venv` are the same stack) and `youtube_transcript_api`/`requests`
(17 entries). Their package sets are readable from `site-packages` and reinstallable in seconds.

**Verdict.** The 23 467-file delta is entirely regenerable, tool-owned cache and virtualenv
content, self-declared as cache by the tools that created it. Losing it costs a `uv sync` and
two `uv pip install` lines, not data. **kopiur is a faithful replacement for VolSync on
`hermes`.** The same reasoning applies, far more cheaply, to `home-assistant` (17 files, one
`.venv`) and `calibre-web-automated` (14 files, a fontconfig cache).

The one residual, and it is small: three venvs record their dependency set only inside
themselves. If those environments are ever considered load-bearing, committing a
`requirements.txt` or `pyproject.toml` beside each would remove the last trace of doubt - and is
worth doing on its own merits, independent of backups.

### Finding 2: an r2 restore needs a materially larger kopia cache than the same restore from ceph - an operational prerequisite for DR

> **CLOSED for `ai/hermes` on 2026-09-02.** The claim was raised to `KOPIUR_CACHE_CAPACITY:
> 16Gi` and then restored from **r2** end to end at exactly that value - 65,978 files and
> 10,419,954,664 bytes, matching the snapshot's own `filesNew` and `sizeBytes`, with zero mode
> differences. That run also measured *why*: the kopia cache grows ~1:1 with the bytes written
> into the restore target until it reaches kopia's own (unpinned) internal budget, observed as
> a ~6.2 GiB plateau, so the requirement is `min(snapshot sizeBytes, ~6.2 GiB)` - and it is a
> cliff, not a slope. That turns the sizing question below into arithmetic for every claim.
> Evidence, the fleet audit, and the two claims still under-provisioned (`media/tdarr` at 87%
> of usable, `downloads/radarr` at 70%):
> [`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md).
>
> **`media/plex` is predicted safe but was not itself exercised** - its 4.16 GiB snapshot is
> well under its 9.74 GiB usable cache, so the cache never reaches the limit. Read the
> paragraphs below as the record of how this was discovered; the closure document is the
> current authority on cache sizing.

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

#### Why this was a Stage 5 blocker, not a footnote

Measured cache behaviour *as of this 2026-09-01 run*, all with `mode: Ephemeral`:

| claim | live size | ceph restore | r2 restore | standing populator cache (then) |
|---|--:|---|---|---|
| `media/plex` | 4.4 GB | **succeeded** at 2 GiB | **failed** at 2 GiB, succeeded at 20 GiB | 10 GiB |
| `ai/hermes` | 10.2 GB | failed at 2 GiB, succeeded at 30 GiB | failed at 2 GiB, succeeded at 30 GiB | 5 GiB |

`plex` is the decisive row: identical volume, identical snapshot content, identical mover
identity, and the outcome differs purely by repository. So this is a property of the offsite
backend, not of volume size alone, and a ceph restore succeeding tells you nothing about whether
the r2 restore of the same claim will. That conclusion still holds after the 2026-09-02
closure - see finding 5 of
[`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md).

At the time of this run neither standing `*-kopiur-dst` populator had been exercised against
r2, so the risk was latent while VolSync was still in place and would become a terminal DR
failure the moment VolSync was retired. The work this paragraph called for - raise
`KOPIUR_CACHE_CAPACITY` on the large claims and re-prove an r2 restore at the new value - is
**done for `ai/hermes`** (16Gi in Git, r2-proven); `media/plex` 10Gi is predicted safe by that
measurement but not itself r2-exercised. Current authority:
[`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md).

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

### Finding 4: five claims are too small for a restore to prove much

`downloads/autobrr` holds **one** file (2 179 bytes). Its restore matches byte-for-byte from
both destinations, and that result is close to meaningless as a fidelity proof - it exercises
the mechanism, not the data path at scale. The Stage 2 drill made the same point about this
claim's Stage 1 snapshot, which succeeded while moving zero bytes.

Four more are in the same category and are marked in the table rather than counted as ordinary
passes: `selfhosted/ntfy` (2 files), `selfhosted/paperless-ngx-media` (1 file),
`selfhosted/syncthing-data` (5 files) and `selfhosted/obsidian-livesync` (8 files).
`database/pgadmin` is a borderline case in the other direction - only 3 files, but 979 MiB of
them, so its restore does exercise bulk data.

## Results

**All 30 claims were drilled. All 30 restored successfully from both the `ceph` and the `r2`
repository.** For 29 of them the two destinations produced byte-identical trees; `prowlarr-config`
is the deliberate exception after its correct-identity re-drill (different snapshot points, not a
fidelity defect - see the prowlarr note).

Read the verdict column with its markers - three of them qualify what "pass" means:

- `‡` **cache-excluded**: the restore holds fewer files than live because kopia skips
  `CACHEDIR.TAG` directories. Explained in [finding 1](#finding-1-kopia-excludes-cachedirtag-directories---adjudicated-no-irreplaceable-data-is-affected);
  `snapshot filesNew` matches the restored count exactly in every such row, which is what shows
  the omission is deliberate rather than lossy.
- `*` **too small to prove much**: the claim holds 1-8 files. The restore is correct but is a
  test of the mechanism, not of the data path. See
  [finding 4](#finding-4-five-claims-are-too-small-for-a-restore-to-prove-much).
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
| 8 | `downloads/prowlarr-config` | 3002:3000 | Succeeded | Succeeded | 710 | 710 / 710 | 710 / 710 | content-matched stable set (not destination-identical; see note) | **PASS** |
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
| 25 | `selfhosted/ntfy` | 1000:1000 | Succeeded | Succeeded | 2 | 2 / 2 | 2 / 2 | `027268e78c5f` / `027268e78c5f` (identical) | **PASS** \* |
| 26 | `selfhosted/obsidian-livesync` | 5984:5984 | Succeeded | Succeeded | 8 | 8 / 8 | 8 / 8 | `88858eb0b4e1` / `88858eb0b4e1` (identical) | **PASS** \* |
| 27 | `selfhosted/paperless-ngx` | 1000:1000 | Succeeded | Succeeded | 32 | 32 / 32 | 32 / 32 | `b7d4afdbcc1e` / `b7d4afdbcc1e` (identical) | **PASS** |
| 28 | `selfhosted/paperless-ngx-media` | 1000:1000 | Succeeded | Succeeded | 1 | 1 / 1 | 1 / 1 | `f6a6a1ecf1b4` / `f6a6a1ecf1b4` (identical) | **PASS** \* |
| 29 | `selfhosted/syncthing` | 1000:1000 | Succeeded | Succeeded | 21 | 21 / 21 | 21 / 21 | `05d36861232d` / `05d36861232d` (identical) | **PASS** |
| 30 | `selfhosted/syncthing-data` | 1000:1000 | Succeeded | Succeeded | 5 | 5 / 5 | 5 / 5 | `c525096cb0b4` / `c525096cb0b4` (identical) | **PASS** \* |

`live files` is the count on the live claim at the time of measurement; `snapshot filesNew` is
what kopiur recorded for the on-demand verification snapshot taken for this drill; `restored` is
the count actually walked in the restored scratch PVC. `restore manifest sha256` is the sha256
of the sorted per-file `sha256  path` manifest of the whole restored tree.

### The `downloads/prowlarr-config` re-drill

The original table row transcribed the mover as `3002:3002`. That was a documentation / harness
error, not a policy mismatch: live `SnapshotPolicy` mover `podSecurityContext` on both
`prowlarr-ceph` and `prowlarr-r2`, the overlay pin (`KOPIUR_PUID`/`KOPIUR_PGID`),
`EXPECTED_IDENTITY` in `scripts/ci/kopiur-stage3-test.py`, and the live Deployment
`securityContext` all agree on `3002:3000`. The drill harness derived the mover gid from the
uid, which is right for the other 29 claims and wrong only here.

`prowlarr-config` was therefore **re-drilled at `3002:3000`** after the gate opened. Fresh
verification snapshots `prowlarr-ceph-s5db` and `prowlarr-r2-s5db` both reported `filesNew` 710
and `sizeBytes` 57 529 420; live held 710 files; both restores returned 710 files with **0
unexplained gaps** against the stable set. The content proof stands at the true policy identity.

Two honest caveats, not papered over:

1. **Different snapshot points between destinations.** The ceph `Restore` used
   `source.fromPolicy` `offset: 0`, which resolved to a newer **scheduled** snapshot
   (`393f79bd60c310ce6357ee2f9d48e1d9`) that ran between the verification snapshot and the
   restore, while r2 resolved to the verification snapshot
   (`19c7b3199225b95aee6fabdc68fc2c07`). The two restored trees therefore differ by a small
   number of lines purely because they are different points in time, not because of any
   fidelity defect. That is why this row no longer claims destination-identical digests.
2. **Ownership is the mover's, not the original owner's.** A kopiur restore materialises files
   owned by the mover uid. Live prowlarr files are `1000:3000`; restored copies come back
   `3002:3000` (526 metadata lines differ). Same class as the already-documented
   `matter-server` behaviour, where a root restore mover materialises mixed live uids as
   `0:0`. Functionally harmless here: directories are mode `2775` and files `664` with the
   shared gid `3000`. The evidence does **not** claim ownership is reproduced exactly.

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
independently. That is the complete Stage 5 evidence base the captain asked for.

Of the two questions that gate retirement:

1. **The `CACHEDIR.TAG` coverage change is settled.** Finding 1 adjudicates it: the omitted
   content on all three affected claims is regenerable, tool-owned cache and virtualenv
   material, with nothing user-authored and nothing irreplaceable. kopiur is a faithful
   replacement for VolSync on `ai/hermes`, not a lossy one. No further work is required here
   before retirement; the optional cleanup is committing a dependency manifest beside the three
   unmanifested venvs, which is worth doing for its own sake.
2. **Restore cache capacity was the one genuine prerequisite this run uncovered - and is
   settled for `ai/hermes`.** Finding 2 recorded that `plex` failed its r2 restore on the 2 GiB
   that its ceph restore succeeded on, and that `hermes` needed more than the 5 GiB its standing
   populator then carried. A failed `Restore` is terminal. **Closed for `ai/hermes` on
   2026-09-02** (raised to 16Gi, r2 restore proven end-to-end at that value); authority on the
   sizing rule and remaining under-provisioned claims is now
   [`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md).
   `media/plex` 10Gi is predicted safe by that measurement but was not itself r2-exercised.

Separately, the five near-empty claims (finding 4) carry proofs that are thin by nature -
`autobrr` holds a single file - and may deserve a different kind of assurance than a restore
comparison can give.

Nothing in this document should be read as a recommendation to retire, or not to retire, any
particular volume. That is the captain's call.
