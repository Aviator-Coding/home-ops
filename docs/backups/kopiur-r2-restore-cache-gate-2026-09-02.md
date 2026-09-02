# kopiur r2 restore cache gate - `ai/hermes`, 2026-09-02

> **Status: evidence base only. Nothing was retired by this exercise.**
> No `ReplicationSource`, `SnapshotPolicy`, `SnapshotSchedule`, `ClusterRepository` or
> VolSync object was created, deleted, patched or suspended. All 26 dual-engine claims
> remain dual-engine. This document is the evidence that a later, separate, irreversible
> retirement step is entitled to rely on.

This closes [finding 2 of the fleet restore proof](kopiur-restore-proof-2026-09-01.md#finding-2-an-r2-restore-needs-a-materially-larger-kopia-cache-than-the-same-restore-from-ceph---an-operational-prerequisite-for-dr):
*"an r2 restore needs a materially larger kopia cache than the same restore from ceph"*,
which was the last open prerequisite blocking retirement of `ai/hermes` and `media/plex`.

Sibling documents:

- [`kopiur-restore-drill-2026-08-30.md`](kopiur-restore-drill-2026-08-30.md) - the procedure
  this run follows.
- [`kopiur-restore-proof-2026-09-01.md`](kopiur-restore-proof-2026-09-01.md) - the fleet
  proof whose finding 2 this closes, and the evidence standard this run is built to.
- [`kopiur-stage5-pilot-retirement-2026-09-01.md`](kopiur-stage5-pilot-retirement-2026-09-01.md)
  - the four volumes already retired, and the retirement mechanics.

## Verdict

**Finding 2 is CLOSED for `ai/hermes`, and the r2 path is proven for the large size class.**

`ai/hermes` - 25Gi claim, 9.70 GiB snapshot, 104,223 live entries, the largest object in the
fleet - restored from **r2** end to end, at the cache capacity it now carries in Git, and the
restored tree was verified by per-file sha256 and by mode/uid/gid manifest. The restore wrote
**10,419,954,664 bytes across 65,978 files, matching the snapshot's own `sizeBytes` and
`filesNew` exactly**, with **zero mode differences** across all 77,776 entries present in both
trees.

The gate is closed on measurement rather than on a larger round number: the run also
established *why* the cache has to be that size, which turns the sizing question into
arithmetic for every other claim in the fleet (see [finding 1](#finding-1)).

Three honest limits on that verdict, none of which reopen the gate:

- **`media/plex`'s standing 10Gi was not itself exercised.** It is *predicted* safe by the
  measured model - its 4.16 GiB snapshot is well under its 9.74 GiB usable cache, so the
  cache cannot reach the limit at all - but prediction is not the demonstration this
  document gives for `hermes`. See [the fleet audit](#fleet-audit-every-claim-against-the-same-reasoning).
- **The original ceph/r2 asymmetry is still not mechanistically explained.** See
  [finding 5](#finding-5).
- **Merging this change does not itself update the standing CEPH populator.** See
  [Post-merge prerequisite: recreate `hermes-kopiur-dst`](#post-merge-prerequisite-recreate-hermes-kopiur-dst).

### Post-merge prerequisite: recreate `hermes-kopiur-dst`

What merge updates, and what it does not:

- **Updated by Flux on reconcile:** both `hermes` `SnapshotPolicy` objects (`mover.cache.capacity`
  → 16Gi).
- **Not updated:** the standing CEPH populator `ai/hermes-kopiur-dst`. Its template carries
  `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`, so Flux created it once and never reconciles
  it again. It stays frozen at its create-time capacity forever until delete+recreate.

Live evidence (measured before merge):

| object | cache | ssa | finalizers | ownerReferences | phase |
|---|--:|---|---|---|---|
| `ai/hermes-kopiur-dst` | **5Gi** | `IfNotPresent` | none | none | `Pending` |
| `media/tdarr-kopiur-dst` | 2Gi | `IfNotPresent` | — | — | — |
| `media/plex-kopiur-dst` | 10Gi | `IfNotPresent` | — | — | — |
| `downloads/sabnzbd-kopiur-dst` | 10Gi | `IfNotPresent` | — | — | — |

That 5Gi populator is exactly what a rebuilt claim binds to if `ai/hermes` is ever retired to
kopiur-only. 5Gi gives 4.87 GiB usable against the measured ~6.2 GiB plateau, so the CEPH
claim-rebuild path would hit the same terminal cliff this change was written to remove.

The recreate is **safe and has precedent**:

- the component's own `kubernetes/components/kopiur/ceph/restore.yaml` comment already documents
  delete+recreate as the correct remedy for a stale standing `Restore`;
- `sabnzbd-kopiur-dst` was recreated this way during the Stage 5 pilot;
- the object carries no finalizers and no `ownerReferences`, and owns no backup data.

It is an **operational step that must happen AFTER this merges**. Doing it before would just
have Flux recreate the object at the old 5Gi still declared on `main`. It is a **prerequisite
for retiring `ai/hermes`**, not tidiness: without it the standing CEPH populator remains at the
failing size even though Git and both `SnapshotPolicy` objects say 16Gi.

Do **not** add `force: enabled` or remove `ssa: IfNotPresent` - that label is load-bearing
(a bound PVC's `dataSourceRef` is immutable, and force would resolve the conflict by deleting
and recreating the data volume). The remedy is the one-time recreate, not a manifest change.

The **r2 evidence in this document is unaffected**: the hand-written drill `Restore` set
`mover.cache.capacity: 16Gi` in its own spec, so it never depended on the standing populator.

`media/tdarr-kopiur-dst` is live at 2Gi, which reinforces the existing tdarr recommendation in
the fleet audit - if tdarr is ever raised, its standing `Restore` needs the same post-raise
recreate.

## The gate

The subject had to clear all five criteria. All five passed.

| # | Criterion | Result |
|---|---|---|
| 1 | Readable at the mover identity - zero entries unreadable by the mover uid/gid | **pass** - 0 of 104,223 |
| 2 | The restore reads the snapshot we bracketed, not some other one | **pass** - `kopiaSnapshotID` matched |
| 3 | The `Restore` reaches `Completed` at the **standing** cache value, from **r2** | **pass** - 16Gi, ~16 min |
| 4 | Content verified by per-file sha256 against a bracketed live reference | **pass** - 0 unexplained mismatches |
| 5 | Modes, ownership and file types verified | **pass** - 0 mode diffs, 1 explained owner diff |

## Method

Per the Stage 2 procedure, with the live reference bracketed around the snapshot:

1. Record the baseline: claim uid + bound PV, mounting pod `startTime`/`restartCount`.
2. Read the mover identity from the **live `SnapshotPolicy`** - never from component defaults,
   never inferred from the pod's `runAsUser` (`hermes` pins none at all).
3. Capture live manifest **L1** (sha256 per file) and **L1meta** (`mode uid gid type` per
   entry), `-xdev`, `lost+found` pruned.
4. Create a fresh on-demand `Snapshot` against the **r2** policy, so the reference is minutes
   old rather than the ~4 h that a scheduled daily r2 run would be.
5. Capture **L2**. The **stable set** is the set of paths whose digest is identical in L1 and
   L2 - the files that did not change while the snapshot was being taken.
6. Create one scratch `Restore`, `source.fromPolicy` at `offset: 0`, into a brand-new PVC, and
   confirm `.status.resolved.kopiaSnapshotID` equals the snapshot from step 4.
7. Sample the mover's kopia cache volume throughout the restore via the kubelet stats API.
8. Mount the restored PVC read-only and recompute both manifests.
9. Re-check the baseline, then delete only the scratch artifacts.

Every object this run created carries `fm.homeops/restore-drill: kopiur-r2-cache-gate`.

### Deviations, and why

- **Comparisons are done with path-keyed dictionaries, not `sort` + `comm`.** The 2026-09-01
  run recorded a phantom 10-file gap caused by a `LC_ALL=C`-sorted file compared by a
  UTF-8-locale `comm`. Keying on the raw path bytes removes the collation hazard
  *structurally* rather than relying on remembering to export `LC_ALL=C` in both places.
  (`LC_ALL=C` was exported anyway for the shell-side digests.)
- **The restore ran at 16Gi, which is the value this change puts in Git**, not at a
  deliberately generous drill value. Proving the standing value is the point of the exercise;
  a proof at some larger number would not have closed the gate.

## Evidence

### Baseline - the live claim

| | |
|---|---|
| claim | `ai/hermes`, 25Gi, `ceph-block`, PV `pvc-7a69d4d8-4832-473d-b211-14872d8e4f85` |
| mounting pod | `hermes-7cf89979c7-p4nrp` on `talos-3`, containers `app` + `codeserver`, `/opt/data`, no `subPath` |
| pod securityContext | `fsGroup: 10000`, `fsGroupChangePolicy: OnRootMismatch`, **no `runAsUser`** |
| mover identity (live policy) | `runAsUser/runAsGroup/fsGroup: 10000` |
| live entries | 89,445 files + 14,615 dirs = **104,223**; `du -sh` = 11G |
| ownership | 104,222 entries `10000:10000`; 1 entry `0:10000` (`./hermes-agent`, mode `2775`) |
| unreadable at mover identity | **0** |

No volume is mounted *inside* `/opt/data`, so [finding 3 of the 2026-09-01 proof](kopiur-restore-proof-2026-09-01.md)
(an ESO Secret mounted over a path inside the data dir) does not apply here; `-xdev` plus a
`lost+found` prune is sufficient.

### The snapshot, and the bracket

| | |
|---|---|
| `Snapshot` | `ai/hermes-r2-verify-20260902` (`policyRef: hermes-r2`) |
| kopia snapshot ID | `bcacbe60dec35be22065672c4159a4a3` |
| phase | `Succeeded`, mover Job ran **48 s** |
| `filesNew` | **65,978** |
| `sizeBytes` | **10,419,954,664** (9.70 GiB) |
| credential projection | worked - `status.cleanup.credsReapedAt` set |

```
L1 capture      03:24:31 - 03:25:14 UTC
snapshot        03:26:47 - 03:27:33 UTC
L2 capture      03:28:24 - 03:29:07 UTC
```

Stable set: **89,435 of 89,445** files. The 10 that drifted are exactly the app's own live
state, and all 10 are legitimately allowed to differ:

```
./channel_directory.json   ./cron/jobs.json      ./cron/ticker_heartbeat
./cron/ticker_last_success ./logs/agent.log      ./logs/errors.log
./state.db                 ./state.db-shm        ./state.db-wal
./state/gateway.heartbeat
```

### The restore

| | |
|---|---|
| `Restore` | `ai/hermes-kopiur-drill-20260902-r2` |
| repository | **`r2`** (`ClusterRepository`, Cloudflare R2) |
| `credentialProjection` | `enabled: true` **in its own spec** - required, see [finding 3](#finding-3) |
| source | `fromPolicy: hermes-r2`, `offset: 0` |
| target | new PVC, 25Gi, `ceph-block` - `target.pvc` cannot address a live claim |
| `onMissingSnapshot` | `Fail` (fail-closed; the standing populator uses `Continue`) |
| mover identity | `10000:10000` |
| **cache** | `mode: Ephemeral`, **`capacity: 16Gi`** |
| resolved snapshot | `bcacbe60dec35be22065672c4159a4a3` - **matches the bracketed snapshot** |
| result | **`Completed`**, 03:30:51 -> 03:47:01 UTC (~16 min) |

### The cache curve - the measurement that closes the finding

Sampled from the kubelet stats API (`/api/v1/nodes/<node>/proxy/stats/summary`) on the mover
pod's `kopia-cache` ephemeral volume, alongside the bytes landing in the restore target:

| time (UTC) | restored | kopia cache | cache/restored |
|---|--:|--:|--:|
| 03:31:40 | 0.415 GiB | 0.411 GiB | 99% |
| 03:33:40 | 1.922 GiB | 1.711 GiB | 89% |
| 03:34:46 | 2.452 GiB | 2.244 GiB | 92% |
| 03:35:41 | 3.100 GiB | 2.897 GiB | 93% |
| 03:37:32 | 4.241 GiB | 4.039 GiB | 95% |
| 03:39:00 | 5.362 GiB | 5.165 GiB | 96% |
| 03:41:00 | 6.409 GiB | **6.171 GiB** | 96% |
| 03:42:50 | 7.216 GiB | 6.128 GiB | 85% |
| 03:44:39 | 8.628 GiB | **6.212 GiB** (peak) | 72% |
| 03:46:40 | 9.713 GiB | 6.001 GiB | 62% |

- **peak cache: 6.212 GiB** (6,669,979,648 bytes)
- usable capacity at `16Gi`: **15.581 GiB** (a 16Gi request yields ~97.4% usable)
- **peak / usable capacity: 40%**

Sampling resolution is bounded by kubelet's volume-stats aggregation period (~60 s), so the
peak is a **lower bound**; a short spike between samples would not appear. That is one of the
reasons the chosen capacity is not sized tightly to it.

### Content verification

Live reference is the bracketed L1/L2 pair; restored tree read through a `busybox:1.36` pod
mounting the scratch PVC **read-only** as uid/gid 10000.

| measure | value |
|---|--:|
| restored files | **65,978** (= snapshot `filesNew` exactly) |
| restored dirs | 11,777 |
| restored entries | 77,777 |
| **restored total bytes** | **10,419,954,664** (= snapshot `sizeBytes` exactly) |
| restored manifest sha256 | `a6a5f961e80a30e6a4b67a290d1a0493b69093ef315c4f8618e766b505cea6a5` |
| live (L2) manifest sha256 | `a7f48a8bd9539cc86ddff911eae9656a7b169cd8b773335fdd4fef7d2fac4dea` |

> The counts and byte total are printed next to the digests deliberately. `e3b0c442…b855` is
> the sha256 of the empty string; a manifest comparison that "matches" because both sides are
> empty proves nothing, and the drill document records an attempt that fell into exactly that
> trap.

Per-path comparison:

| check | result | required |
|---|--:|---|
| paths in both restore and stable set | 65,968 | - |
| digest mismatches among them | **2** | 0, both explained - [finding 4](#finding-4) |
| stable-set files absent from restore | 23,467 | - |
| ... of those, under a `CACHEDIR.TAG` | **23,467** | all of them |
| ... **not** under a tag (would be data loss) | **0** | **0** |
| restored paths not present live | **0** | 0 |
| mode differences (77,776 common entries) | **0** | 0 |
| file-type differences | **0** | 0 |
| ownership differences | **1** | explained - [finding 6](#finding-6) |

The 23,467 omissions reconcile exactly: 89,445 live files − 65,978 snapshot files = **23,467**,
and `filesNew` equals the restored count, which is the signal that the omission is kopia's
deliberate `CACHEDIR.TAG` exclusion rather than a lossy backup. The ten tag roots:

```
./.cache/uv                                   ./skills/media/youtube-content/.venv
./.hw-venv                                    ./venv-httpx
./home/.cache/uv                              ./wiki/.venv
./home/.cache/uv/archive-v0/qtN2eVLzEmPwq7RVtnDt5
./home/.cache/uv/archive-v0/rsLOV4wBODlxlT81qdfa8
./home/.local/share/uv/tools/pytest           ./scripts/.pytest_cache
```

### The live claim was never touched

| | before | after |
|---|---|---|
| pod `startTime` | `2026-08-31T23:49:16Z` | `2026-08-31T23:49:16Z` |
| container `restartCount` | `0 0` | `0 0` |
| PVC uid | `bfe697fc-5bd7-4b83-8d47-36f6a4587794` | unchanged |
| bound PV | `pvc-7a69d4d8-…f85` | unchanged |
| live file count | 89,445 | 89,445 |

## Findings

### Finding 1

**The kopia cache grows ~1:1 with the bytes written into the restore target until it reaches
kopia's own internal budget, then holds flat. That makes the required cache
`min(snapshot sizeBytes, ~6.2 GiB)` - and it is a cliff, not a slope.**

The curve above has two clearly separated regimes. Below ~6.2 GiB the cache tracks restored
bytes at 89-99%: every pack blob pulled out of the repository is retained. At ~6.2 GiB
eviction engages and the cache holds flat at 6.0-6.2 GiB while the restore runs on to 9.7 GiB.

This explains the original failure exactly. At `5Gi` the mover gets **4.87 GiB usable**, which
the 1:1 regime consumes at roughly **45% restored** - long before eviction could ever help.
The claim could not be restored from r2 at its configured size, and the failure is terminal.

The practical consequence is the cliff. While a claim's snapshot is *smaller* than its cache,
the cache simply never reaches the limit and any value works - which is why 25 of 30 claims
are fine on the 2Gi default and why nothing has failed in normal operation. The first time a
growing claim's snapshot crosses its cache capacity, the requirement does not creep up by a
little; it jumps to the full plateau. **A claim sitting just under its capacity is not
"nearly fine", it is one growth spurt away from a terminal restore failure**, discovered
during a disaster.

### Finding 2

**kopiur passes kopia no cache budget at all, so the plateau is an unpinned default and must
not be leaned on.**

The mover's work spec ends with `"cache":{}`:

```
KOPIUR_WORK_SPEC={"version":1,"operation":{...},"repository":{"s3":{...}},...,"cache":{}}
```

The `Restore`/`SnapshotPolicy` CRDs *do* expose `mover.cache.contentCacheSizeMb` and
`mover.cache.metadataCacheSizeMb` (kopia's `--content-cache-size-mb` /
`--metadata-cache-size-mb`), and **nothing in this repo sets either**, on the component or on
any overlay, nor on either `ClusterRepository` via `cacheDefaults`. So the ~6.2 GiB plateau is
whatever kopia's built-in defaults happen to be for the mover image currently in use - not a
value this repo controls, pins, or would be told about if it changed.

That is why `ai/hermes` is sized to survive **both** regimes rather than to the measured peak:

| regime | requirement | 16Gi (15.58 GiB usable) gives |
|---|--:|--:|
| eviction holds at the observed plateau | 6.21 GiB | **2.5x** headroom |
| eviction never fires (cache = whole snapshot) | 9.70 GiB | **1.6x**, i.e. ~60% growth room |

Sizing to the plateau alone would have justified something near 10Gi; sizing to the snapshot
covers the case where the unpinned default moves or the plateau is not the hard ceiling it
appears to be. Given that the failure mode is terminal and only discovered during recovery,
that is the right side to err on.

Bounding kopia explicitly with `contentCacheSizeMb` is the structural alternative - it would
let a small capacity be made *safe* rather than merely *lucky* - and it is recorded in
`kubernetes/components/kopiur/Readme.md` as the fix to reach for if these capacities ever
become expensive. It was deliberately not done here: it changes cache semantics for every
backup mover in the fleet, which is a wider blast radius than the one-line change this
finding actually called for.

### Finding 3

**`mode: Ephemeral` renders a generic ephemeral *PVC*, not an `emptyDir` - so the cost of a
raised capacity is close to zero, and there was never a node-disk reason to keep it small.**

The mover pod's volume is:

```yaml
- name: kopia-cache
  ephemeral:
    volumeClaimTemplate:
      spec:
        accessModes: ["ReadWriteOnce"]
        resources: { requests: { storage: 5Gi } }
        volumeMode: Filesystem
```

with no `storageClassName`, so it lands on the cluster default - `ceph-block`. The comment on
`kubernetes/apps/main/ai/hermes.yaml` previously read *"Ephemeral, so it sizes an emptyDir for
the run and adds no PVC"*, which is wrong in both halves; it is corrected in the same change
as this document, along with the variable's row in the component README.

This matters for the sizing decision: RBD is thin-provisioned and the claim is deleted with
the mover pod, so a 16Gi request consumes only what a run actually writes (~6.2 GiB here) and
only for the duration of the run. Ceph has 3.5 TiB available.

Also worth stating plainly, because one variable does two jobs: `KOPIUR_CACHE_CAPACITY` feeds
the **ceph backup policy, the r2 backup policy, and the standing `Restore` template**. Backups
were succeeding at 5Gi and did not need the raise. **The restore is the demanding direction and
is what must set the value.**

**Caveat on the standing object:** `${APP}-kopiur-dst` carries
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`, so Flux creates it once and never reconciles
it again. Raising `KOPIUR_CACHE_CAPACITY` in Git updates both `SnapshotPolicy` objects on the
next reconcile, but **does not** move a live standing `Restore` off the capacity it was created
with. `hermes-kopiur-dst` was created at 5Gi and stays there until it is deleted and recreated
(safe: no finalizers, no ownerReferences, owns no backup data - the Stage 5 `sabnzbd-kopiur-dst`
precedent). That recreate is a **post-merge / pre-retirement** operational step for the CEPH
populator path; it is not required for hand-written r2 drills, which always carry capacity in
their own spec. This run deliberately left the standing object untouched.

### Finding 4

**The L1/L2 stable-set bracket cannot see page-cache lag, so a file can be in the stable set
and still legitimately differ in the restore.** Two did here, and neither is a fidelity
failure.

`copyMethod: Snapshot` means the backup is taken from a **CSI block-level `VolumeSnapshot`**,
which is *crash-consistent*, not filesystem-flushed. Bytes an application has written but that
are still dirty in the page cache - or mmap'd and never written back - are simply not in the
block image. The L1/L2 bracket cannot detect this, because **both brackets read through the
filesystem and therefore see the same cached view the snapshot does not have.**

| path | live | restored | verdict |
|---|--:|--:|---|
| `./cron/usage_audit.jsonl` | 611,116 B | 610,304 B | restored content is **byte-identical to the live file's first 610,304 bytes** - a strict prefix of an append-only log |
| `./kanban.db-shm` | 32,768 B | 3 B (`\0\0\0`) | SQLite shared-memory index; `kanban.db-wal` is 0 bytes on both sides and `kanban.db` matched exactly, so there is no unreplayed WAL and SQLite rebuilds `-shm` on open |

The prefix result is the decisive one: the restore did not corrupt or truncate anything, it
faithfully reproduced an earlier point in an append-only file. This is inherent to
snapshot-based backup and applies identically to VolSync, which uses the same copy method.

Methodological consequence for future drills: **treat a stable-set mismatch as a question, not
automatically as a failure.** Check whether the restored content is a prefix of live (append-only
lag) or an application-managed ephemeral file, before concluding fidelity loss. The 2026-09-01
run did not hit this only because its two large subjects happened not to have such a file in
their stable sets.

### Finding 5

**The ceph/r2 asymmetry that opened finding 2 remains unexplained, and should not be read as
"ceph restores are cheap".**

Finding 2's decisive datapoint was that `media/plex` restored from **ceph** on a 2 GiB cache
and **failed from r2** on that same 2 GiB. This run did not reproduce a configuration reason
for that difference: both `ClusterRepository` objects were inspected and **neither sets
`cacheDefaults`**, and the mover receives `"cache":{}` in both cases, so kopia's cache budgets
are identical for the two destinations.

Under the model measured here, a 4.16 GiB `plex` snapshot restored into a 1.95 GiB usable
cache should exhaust it regardless of repository - so the recorded ceph success at 2Gi is the
observation that does not fit, not the r2 failure. Absent an explanation, the safe reading is
that **a ceph restore succeeding at a given cache size is not evidence that size is adequate**,
which is also finding 2's original conclusion arrived at from the other direction. Since one
variable sizes both destinations, sizing to the measured r2 behaviour covers ceph automatically.

### Finding 6

**A restored volume's root directory is provisioned by CSI, not restored by kopia, and a
non-root mover cannot reproduce a root-owned entry.** Both are cosmetic here; both are worth
knowing before reading a future diff as a defect.

| | live | restored |
|---|---|---|
| volume root | `0700 10000:10000` | `2775 0:10000` |
| `./hermes-agent` | `2775 0:10000` | `2775 10000:10000` |

The root is the target PVC's own filesystem root, created by the CSI driver and then touched
by the kubelet's `fsGroup` handling; kopia restores *into* it and never sets its mode. The
`./hermes-agent` directory was `uid 0` live, and the mover runs as uid 10000 without
`CAP_CHOWN`, so it cannot restore a uid it does not own - the same class as the root-mover
ownership note already recorded for `home-automation/matter-server`. Its group is `10000` with
`rwx`, so the workload's access is unchanged.

Everything else was exact: **0 mode differences and 0 type differences across all 77,776
common entries.** That is the kopiur property the component README already claims and this run
confirms at scale - kopiur stages its source read-only, gets no `fsGroup` fixup, and therefore
restores original modes, unlike a VolSync restore which relaxes every mode by a group-write
bit.

## Fleet audit: every claim against the same reasoning

Applying `required cache ≈ min(snapshot sizeBytes, ~6.2 GiB)` to all 30 r2 policies, using each
policy's **live** `mover.cache.capacity` and its newest snapshot's `sizeBytes`. Usable capacity
is the request × 0.974, the ratio measured on this run's 16Gi volume.

| claim | cache | usable | snapshot | snapshot / usable | verdict |
|---|--:|--:|--:|--:|---|
| `ai/hermes` | 5Gi -> **16Gi** | 4.87 -> 15.58 GiB | 9.70 GiB | 199% -> **62%** | **was FAILING - fixed by this change** |
| `media/tdarr` | 2Gi | 1.95 GiB | 1.70 GiB | **87%** | **under-provisioned - recommend 10Gi** |
| `downloads/radarr` | 2Gi | 1.95 GiB | 1.36 GiB | **70%** | **watch - recommend 10Gi** |
| `database/pgadmin` | 2Gi | 1.95 GiB | 0.93 GiB | 48% | ok |
| `media/plex` | 10Gi | 9.74 GiB | 4.16 GiB | 43% | ok (predicted, not exercised) |
| `selfhosted/n8n` | 2Gi | 1.95 GiB | 0.47 GiB | 24% | ok |
| `downloads/sabnzbd` | 10Gi | 9.74 GiB | 2.06 GiB | 21% | ok |
| remaining 23 claims | 2Gi / 5Gi | 1.95-4.87 GiB | ≤ 0.29 GiB | ≤ 15% | ok |

**Two claims are under-provisioned for an r2 restore and are deliberately left unchanged by
this task**, because neither is failing today and per-app configuration is a captain decision.
The recommendation for both is one line each, and the reasoning is the cliff in finding 1:

- **`media/tdarr` - the real one.** A 1.70 GiB snapshot against 1.95 GiB usable is **13%
  margin on a volume that grows**. The moment it crosses 1.95 GiB the requirement does not
  rise to 2.1 GiB, it jumps to the full ~6.2 GiB plateau, and the restore fails terminally.
  Suggested change in `kubernetes/apps/main/media/tdarr.yaml`: `KOPIUR_CACHE_CAPACITY: 10Gi`
  (matching `plex` and `sabnzbd` in the same size class).
- **`downloads/radarr`** - 70%, the same shape with more runway.
  `kubernetes/apps/main/downloads/radarr.yaml`: `KOPIUR_CACHE_CAPACITY: 10Gi`.

`media/plex` is **not** on that list: its 4.16 GiB snapshot is well under its 9.74 GiB usable
cache, so under the measured model the 1:1 regime tops out at ~4.2 GiB and never reaches the
limit - 2.3x margin. That is a prediction from this run's measurement, not a demonstration;
the honest statement is that plex's standing value looks comfortably safe and has not itself
been exercised against r2. Proving it would cost one more ~7-minute drill restore.

A useful simplification falls out of finding 1: because the plateau is a property of kopia's
budget rather than of the claim, **a single standing value of 10Gi would cover every claim in
the fleet**, present and foreseeable, including any that later grows past its snapshot size.
Raising the component default from `2Gi` to something plateau-covering would remove this class
of problem permanently, at no real storage cost (finding 3). That is a component-wide change
and is left as a recommendation.

## Safety properties of this run

- **No `Snapshot` CR was deleted.** A kopiur `Snapshot` owns its kopia snapshot through the
  `kopiur.home-operations.com/snapshot-cleanup` finalizer, so deleting one deletes backup data.
  The single on-demand snapshot created here (`hermes-r2-verify-20260902`) was verified present
  and `Succeeded` after cleanup, and is left for normal retention to prune.
- **No `SnapshotPolicy`, `SnapshotSchedule`, `ClusterRepository` or `ReplicationSource` was
  created, deleted, patched or suspended.**
- **The restore targeted a new scratch PVC.** `Restore.spec.target.pvc` creates its own claim
  and cannot address a live one.
- **The standing `hermes-kopiur-dst` populator was not touched.** It remains `Pending` /
  `AwaitingPvcDataSourceRef` by design, and still carries its create-time **5Gi** cache
  (`ssa: IfNotPresent`). Git now says 16Gi; the live standing object does not until a
  delete+recreate after merge (see finding 3 caveat).
- **Deletion safety was verified before deleting, not assumed**: the scratch `Restore` carried
  no finalizers and no `ownerReferences`, so its deletion could not cascade anywhere.

### Cleanup

Deleted: the scratch `Restore`, its restored PVC, and the read-only verification pod. Retained
deliberately: the `Snapshot` CR.

Post-run state:

- 0 objects matching `fm.homeops/restore-drill=kopiur-r2-cache-gate` except the `Snapshot`.
- Both `hermes` `SnapshotSchedule`s present and **not suspended** (`hermes-ceph`
  `H 1-23/4 * * *`, `hermes-r2` `H 19 * * *`).
- **0 of 276 snapshots fleet-wide in `Running` or `Pending`**, so nothing is blocking a later
  scheduled backup under `concurrencyPolicy: Forbid`. One unrelated pre-existing `Failed`
  snapshot (`ai/opencode-ceph-20260831055425`, 2026-08-31) is historical, not from this run,
  and not blocking - `opencode-ceph` has taken successful snapshots since.

## What this proves, and what it does not

**Proves**

- `ai/hermes` restores from **r2**, end to end, at the cache capacity it now carries in Git,
  with content verified by per-file digest and by mode/uid/gid manifest.
- The mechanism behind finding 2, well enough to size every other claim by arithmetic instead
  of by re-running a drill per volume.
- kopiur restores modes and file types exactly, at 77,776-entry scale.
- The `CACHEDIR.TAG` omission on this claim is deliberate and complete: 23,467 files, all under
  a tag, with `filesNew` equal to the restored count and total bytes equal to `sizeBytes`.

**Does not prove**

- That `media/plex` restores from r2 at its standing 10Gi. Predicted safe, not exercised.
- Why a `plex` ceph restore succeeded at 2Gi when the model says it should not have
  ([finding 5](#finding-5)).
- That the live `hermes-kopiur-dst` CEPH populator already runs at 16Gi. Git and new scratch
  Restores do; the standing object is frozen at 5Gi by `ssa: IfNotPresent` until the
  [post-merge recreate](#post-merge-prerequisite-recreate-hermes-kopiur-dst).
- Anything about retirement. **No volume was retired and no `ReplicationSource` was touched.**
  Whether `ai/hermes` should now lose its second engine is a separate captain decision that
  this evidence informs but does not make.
