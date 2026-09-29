# Restore cache, populators, and drills

Human steps: `docs/backups/kopiur-restore-runbook.md`. This file is why those
steps are shaped the way they are. Full-cluster loss is a different document:
`docs/backups/full-cluster-restore-from-r2.md`.

## The cache cliff

During a restore the kopia cache grows about 1:1 with bytes written into the
target until kopia's internal budget, observed as a **~6.2 GiB plateau**, then
holds flat.

```
restored GiB:  0.4  1.9  2.5  3.1  4.2  5.4  6.4  7.2  8.6  9.7
cache GiB:     0.4  1.7  2.2  2.9  4.0  5.2  6.2  6.1  6.2  6.0
```

**Required cache = min(snapshot `sizeBytes`, ~6.2 GiB), plus headroom.** It is
a cliff. While the snapshot is smaller than the cache, any value works. The
first time a growing claim crosses its capacity, the requirement jumps to the
full plateau and the `Restore` fails terminally. It never retries. Recovery is
a **new** `Restore`, not a patch of the failed one.

kopiur sends `"cache": {}`. Neither `ClusterRepository` sets `cacheDefaults`.
The CRD fields `mover.cache.contentCacheSizeMb` and `metadataCacheSizeMb` are
unset. The plateau is an unpinned kopia default. Sizing a large claim to cover
the whole snapshot is the defence if that default moves.
`mode: Ephemeral` is a generic ephemeral PVC on `ceph-block`, thin-provisioned,
deleted with the mover pod. Cost is not a reason to under-size.

One variable feeds both backup policies and the standing `Restore`. The restore
sets the value.

### Proven versus sized

"r2-proven" means a `Restore` from **r2** completed at **exactly** that
capacity and the tree was checked. A ceph restore does not count: `media/plex`
succeeded from ceph at 2Gi and failed from r2 at 2Gi.

| Claim | Cache now | What is proven |
|---|---|---|
| `ai/hermes` | 48Gi | r2-proven at the previous **16Gi** (65,978 files / 10,419,954,664 B, the only run that reached the plateau). Raised with the claim 25Gi to 40Gi. 16Gi still clears `min(snapshot, ~6.2 GiB)`. 48Gi is conservatism against the unpinned plateau, sized off the claim's usable ceiling (~39 GiB). The exact-capacity proof does not cover 48Gi. |
| `media/plex` | 10Gi | r2-proven at exactly 10Gi: 24,726 files / 4,948,787,362 B in 7m36s, 0 mode and 0 type diffs across 37,201 entries. SQLite opened both DBs. Peak cache is an extrapolation (~47% of usable); 3.541 GiB was the direct sample. Never quote 4.618 GiB as measured. |
| `media/tdarr` | 10Gi | r2-proven at 10Gi: 17,281 files / 1,820,653,922 B. |
| `downloads/radarr` | 10Gi | Sized only. The demonstration for this size class is tdarr. |
| `downloads/sabnzbd` | 10Gi | Both destinations byte-identical at the drill's own capacity, not as a standing-populator proof. |
| `ai/opencode` | 5Gi | Sized only. |
| Default | 2Gi | Fine while the snapshot stays under the cache. |

`paperless-ngx-media` (50Gi claim, 5Gi cache) and `syncthing-data` (15Gi claim,
5Gi cache) cross the plateau the first time they hold real data. They stay
dual-engine for that reason (skill `volsync-carveouts`). `ntfy` and
`obsidian-livesync` are structurally safe: cache capacity is at least the
claim size.

### Plex proof does not cover these

The 10Gi r2 drill used `target.pvc` (a scratch PVC). It did **not** exercise:

1. Starting Plex against the restored volume. SQLite read the library (73 real
   tables, 364,217 rows, 0 FK violations). That is not "Plex boots".
2. The 7 FTS virtual tables. `PRAGMA integrity_check` aborts on Plex's custom
   collating tokenizer.
3. **The standing populator path.** `plex-kopiur-dst` points at **ceph**, not
   r2. Restoring from r2 is a hand-written `Restore` on purpose. The drill
   proves the r2 repository can restore this claim at 10Gi. It does not prove
   the standing populator object. Exercising that deletes the live claim.
4. Any snapshot other than `offset: 0`.
5. A ceph restore in that same exercise (ceph remains the fleet-proof row).
6. Media files. This claim is `/config` only. The library is `nas-media`,
   which neither kopiur nor the drill touches.

The ceph-versus-r2 reason r2 needs more cache is still unexplained. The run
reproduced the sizing model, not the cause.

Never lower `media/plex`'s cache below 10Gi. A cache raise on `radarr` (and
any other claim) does not reach the live populator until that `Restore` is
deleted after the raise is on `main`.

## `IfNotPresent` populators

`components/kopiur/ceph/restore.yaml` carries
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`. Flux creates it once and never
reconciles it. The Kustomization stays `Ready` / `Applied revision: main`.
`flate` cannot see the drift.

A fleet audit found two frozen objects and then closed them: `ai/hermes` at
5Gi while Git said 16Gi, and `downloads/autobrr` at identity 1000:1000 with no
`credentialProjection` while Git said 2000:2000. tdarr and radarr were
recreated at 10Gi after that raise merged. Deleting before the commit is on
`main` recreates the old value.

After any `KOPIUR_*` change the standing `Restore` consumes:

```sh
kubectl -n <ns> delete restore.kopiur.home-operations.com <app>-kopiur-dst
flux reconcile ks <app> -n <ns>
kubectl -n <ns> get restore <app>-kopiur-dst -o jsonpath='{.spec.mover.cache.capacity}{"\n"}'
```

`Ready=False` / `AwaitingPvcDataSourceRef` after that is the correct passive
state, not a fault.

### Deleting a `Restore` is safe (four legs)

Established from the CRD and from operator behaviour, not from the object's
name:

1. **No data-lifecycle field.** `Snapshot` has `deletionPolicy` and
   `onScheduleDelete`. `SnapshotPolicy` has `deletion.onPolicyDelete`.
   `Restore`'s spec is only `credentialProjection`, `failurePolicy`, `mover`,
   `options`, `policy`, `repository`, `source`, `target`.
2. **Data flow is read-only.** `source` names a policy to read. `target` names
   a PVC to write. Nothing names a snapshot to remove.
3. **Finalizer census.** At the measurement, 276/276 `Snapshot` objects carried
   `snapshot-cleanup` and 60/60 policies carried `policy-cleanup`. 0/30
   `Restore` objects carried a finalizer or an `ownerReference`.
4. **Mid-run observation.** A scratch `Restore` with its mover `Running` and a
   kopia session open still had `finalizers=<none>` and
   `ownerReferences=<none>`.

The `Snapshot` census was 276 before and after deleting stale populators and
completed scratch `Restore` objects. Deletions returned immediately rather than
sticking in `Terminating`.

Nothing in that exercise deleted a `Snapshot`, `SnapshotPolicy`,
`SnapshotSchedule`, `ClusterRepository` or `ReplicationSource`.

## Drill rules

- New, uniquely named `Restore` plus a scratch PVC. Never patch
  `<app>-kopiur-dst` or a VolSync `<app>-dst`.
- `onMissingSnapshot: Fail` on the scratch object too. `Continue` yields an
  empty volume that looks healthy.
- `credentialProjection.enabled: true` in that object's own spec.
- Export `LC_ALL=C` for both `sort` and `comm`. A C-sorted file compared by a
  UTF-8 `comm` reports phantom missing paths.
- **busybox `find -exec {} +` silently truncates and exits 0.** One manifest
  returned 33 of 24,055 entries. busybox `xargs` has no `-a` or `-d`, and
  busybox `find` has no `-printf`. Use `-exec \;` and cross-check the manifest
  line count against an independent `find | wc -l` before believing it.
- Do not read "live" content through the app pod when another volume is mounted
  over a path inside the claim. `esphome` `/config/secrets.yaml` is an
  ExternalSecret file mounted over a zero-byte placeholder. `find -xdev` still
  reports that single file. Compare `stat -c %d`, or read the live reference
  from a CSI `VolumeSnapshot` clone.
- A restore-target PVC does not inherit the `Restore`'s labels. A label-only
  sweep misses it.
- Page-cache lag: a crash-consistent snapshot can differ from both sides of a
  stable-set bracket that both read through the filesystem. A strict prefix of
  an append-only log, or a SQLite `-shm` placeholder with an identical db, is
  a question, not automatic fidelity loss. The volume root is provisioned by
  CSI and is not restored by kopia.

## Open follow-ups (still open)

1. **Fleet cache default.** A single 10Gi component default would clear the
   plateau for every current claim, at thin-provisioned cost, and would
   require recreating every standing populator once. It is a captain decision.
   It is not implemented. The component default is still `2Gi`.
2. **Greenfield apply** of the `pgadmin` and `calibre-web-automated` split
   shape has not been demonstrated. See [onboarding.md](onboarding.md).
3. **sizeBytes-versus-cache detector.** Compare each claim's newest snapshot
   `sizeBytes` with its configured cache. Both numbers are already in the
   cluster. Not built. `media/plex` grew 8% between the day 10Gi was chosen
   and the day it was proved, and nothing in CI said so. Do not quote a growth
   rate from that window: a 4-day and an 8-day window disagreed by 2.3x.
4. **Populator-versus-Git check.** Nothing compares standing `Restore` objects
   to the resolved substitute map. Proposed and not built: when a PR changes a
   `KOPIUR_*` value that `ceph/restore.yaml` consumes, emit the
   delete-and-reconcile commands for those claims. A general live-versus-Git
   drift detector was deliberately not proposed.

## Crash-consistent databases

No application hook runs before the CSI snapshot. FalkorDB, same snapshot:
AOF `appendonly yes` / `appendfsync everysec` recovered 2174 of 2199 nodes
(25-node unfsynced tail). The image's stock RDB-only recovered 200 of 2199.
Both reported a healthy start. Durability has to be the process's own fsync
settings. `database/surrealdb` uses a `SnapshotPolicy` hook for that reason
([onboarding.md](onboarding.md)).

CloudNativePG is not a kopiur volume. One barman destination per `Cluster`.
Off-site is the suspended mirror in
`kubernetes/apps/base/database/cloudnative-pg/offsite-mirror/`.
