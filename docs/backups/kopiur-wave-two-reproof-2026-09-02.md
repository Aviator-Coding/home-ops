# kopiur migration wave two - populator closure and near-empty re-proof, 2026-09-02

> **Status: evidence and one live fix. Nothing was retired by this exercise.**
> No `ReplicationSource`, `SnapshotPolicy`, `SnapshotSchedule` or `ClusterRepository` was
> created, deleted, patched or suspended, and **78 `ReplicationSource` objects remain** -
> 26 dual-engine claims x 3 destinations, the arithmetic confirmation that nothing was retired.
> Two `Snapshot` CRs were **created** (prowlarr verification snapshots) and, as a direct
> consequence, kopiur's GFS retention **pruned two older ones**. That is read honestly in
> [the retention finding](#finding-1-creating-a-verification-snapshot-deletes-an-older-one),
> not filed as "census unchanged".

Sibling documents:

- [`kopiur-populator-drift-2026-09-02.md`](kopiur-populator-drift-2026-09-02.md) - the audit
  that raised `media/tdarr` and `downloads/radarr` to 10Gi and left the live half owing;
  [part 0](#part-0---the-outstanding-populator-fix) discharges its post-merge prerequisite.
- [`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md) -
  the cache-sizing model applied in [part 3](#part-3---the-exposure-a-thin-proof-actually-hides).
- [`kopiur-restore-drill-2026-08-30.md`](kopiur-restore-drill-2026-08-30.md) - the procedure
  followed here.
- [`kopiur-restore-proof-2026-09-01.md`](kopiur-restore-proof-2026-09-01.md) - the fleet proof
  whose finding 4 (five claims too small to prove much) and prowlarr caveat this task closes.

## Verdict

| | |
|---|---|
| populators verified live at 10Gi | **2 of 2** - `media/tdarr`, `downloads/radarr` |
| near-empty claims re-measured live | **5 of 5** - none has grown; all identical to the 2026-09-01 proof |
| claims re-proven destination-identically | **6 of 6** - content **and** mode/uid/gid digests match across `ceph` and `r2` |
| `prowlarr-config` ambiguous row | **closed** - one identical snapshot pair, zero differences |
| claims ready to retire on this evidence | **4 of 6** - see [part 4](#part-4---per-claim-retirement-verdict) |
| claims **not** ready | **2** - `paperless-ngx-media`, `syncthing-data` |
| `Snapshot` CRs deleted by this task | **0** (2 pruned by the operator's own retention - [finding 1](#finding-1-creating-a-verification-snapshot-deletes-an-older-one)) |

The honest headline: **four of the five near-empty claims cannot be given a stronger proof than
they already have, because there is nothing else on the volume to prove.** What this task could
add - and did - is a different assurance: that each backup is *complete* with respect to its
live claim, and that the restore is destination-identical in metadata as well as content. The
substantive new risk it surfaced is not fidelity at all, it is
[restore-cache capacity for the content these volumes do not yet hold](#part-3---the-exposure-a-thin-proof-actually-hides).

## Part 0 - the outstanding populator fix

PR #1544 (`f3760b09`) put `KOPIUR_CACHE_CAPACITY: 10Gi` on `main` for `media/tdarr` and
`downloads/radarr`. Both live populators were still `2Gi`, created `2026-08-31T04:10Z`, because
`kubernetes/components/kopiur/ceph/restore.yaml` carries
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent` - Flux applies those objects once and never
reconciles them again.

Confirmed before acting: `main` at `f3760b09` declares 10Gi, both owning Kustomizations report
`Applied revision: refs/heads/main@sha1:f3760b09…`, and both live objects read `2Gi`. Neither
carried a finalizer or an `ownerReference`, so deletion could not cascade anywhere.

```bash
kubectl -n media     delete restore.kopiur.home-operations.com tdarr-kopiur-dst
kubectl -n downloads delete restore.kopiur.home-operations.com radarr-kopiur-dst
flux reconcile ks tdarr  -n media
flux reconcile ks radarr -n downloads
```

Both deletions returned immediately rather than hanging in `Terminating`, which is the
observable signature of having no finalizer.

**Verified live, after reconcile, and again after a second reconcile:**

| object | as found | after | recreated |
|---|--:|--:|---|
| `media/tdarr-kopiur-dst` | `2Gi` | **`10Gi`** | `2026-09-02T16:38:04Z` |
| `downloads/radarr-kopiur-dst` | `2Gi` | **`10Gi`** | `2026-09-02T16:38:06Z` |

Every other templated field resolved as Git declares: `cache.mode: Ephemeral`,
`podSecurityContext` `2000:2000:2000`, `credentialProjection.enabled: true`,
`repository.name: ceph`, `source.fromPolicy.offset: 0`, `policy.onMissingSnapshot: Continue`,
`target.populator`, and `ssa: IfNotPresent` back in place. Both report
`Ready=False` / `AwaitingPvcDataSourceRef`, the correct passive populator resting state.

**Populator drift is now closed fleet-wide against `main`: 0 of 30.**

## Part 1 - the five near-empty claims

### They have not grown

Measured against each claim's newest snapshot on each destination, before any restore:

| claim | files | bytes | vs the 2026-09-01 proof |
|---|--:|--:|---|
| `downloads/autobrr` | 1 | 2 179 | unchanged |
| `selfhosted/paperless-ngx-media` | 1 | **0** | unchanged |
| `selfhosted/ntfy` | 2 | 188 416 | unchanged |
| `selfhosted/syncthing-data` | 5 | 531 | unchanged |
| `selfhosted/obsidian-livesync` | 8 | 574 930 | unchanged |

`ceph` and `r2` reported **identical `filesNew` and `sizeBytes` for all five**, despite
snapshots taken up to 22 hours apart - the first indication that these volumes are static. The
brief's conditional ("if a claim's live content is now larger, prove against the current
content") therefore does not fire for any of them.

### The backups are complete - which is the assurance a thin restore cannot give

The risk a near-empty backup actually carries is not "the restore is wrong". It is "the backup
is thin because it is silently *missing* content that is on the volume". That is checkable, and
it is what the earlier proof did not state in this form. Live content was read with a device
check on every entry (`stat -c %d`), per the `esphome-config` false-FAIL trap:

| claim | live files / bytes | snapshot files / bytes | complete? |
|---|--:|--:|---|
| `downloads/autobrr` | 1 / 2 179 | 1 / 2 179 | yes |
| `selfhosted/paperless-ngx-media` | 1 / 0 | 1 / 0 | yes |
| `selfhosted/ntfy` | 2 / 188 416 | 2 / 188 416 | yes |
| `selfhosted/syncthing-data` | 5 / 531 | 5 / 531 | yes |
| `selfhosted/obsidian-livesync` | 8 / 574 930 | 8 / 574 930 | yes |

Two entries that would have looked like gaps are not:

- **`autobrr` `./log`** is an `emptyDir` mounted over the claim, on device `66309` against the
  claim root's `64640`, and it is empty even live. The claim's *own* `log` directory is what
  kopiur backed up and restored (`2775 2000:2000`). The live `2777 0:2000` belongs to the
  `emptyDir`, not to the claim - the same class as the `esphome-config` finding, caught the
  same way.
- **`ntfy` has no readable live root.** It is mounted only through two `subPath`s
  (`cache`, `lib`), so the claim root cannot be listed from the app pod at all. The restore
  supplied it: the root holds exactly `cache/` (with `attachments/` at mode `2700` and
  `cache.db`) and `lib/` (`auth.db`) - 2 files, nothing hidden outside the `subPath`s.

### Restore results - all five, both destinations

Scratch `Restore` objects with `target.pvc` (a brand-new PVC that cannot address a live claim),
`source.fromPolicy` `offset: 0`, `policy.onMissingSnapshot: Fail` (fail-closed), their own
`credentialProjection.enabled: true`, and each claim's **live populator cache capacity and mover
identity** - so the drill exercises the values a real restore would use. Every restore's
`.status.resolved.kopiaSnapshotID` was matched against the snapshot predicted before the run;
all ten hit their intended snapshot.

Restored volumes were mounted **read-only on the volume source**, in pods matching each claim's
mover identity. `LC_ALL=C` was exported for every `sort` **and** `comm`.

| claim | files | bytes | vs `filesNew`/`sizeBytes` | content digest (ceph = r2) | mode+uid+gid digest (ceph = r2) |
|---|--:|--:|---|---|---|
| `downloads/autobrr` | 1 | 2 179 | exact | `fa14f3e480fb…4130` | `bb4d77b598d7…50bc` |
| `selfhosted/paperless-ngx-media` | 1 | 0 | exact | `f6a6a1ecf1b4…2066` | `bede74e999eb…0388` |
| `selfhosted/ntfy` | 2 | 188 416 | exact | `027268e78c5f…e792` | `61ca1385c9f2…3323` |
| `selfhosted/syncthing-data` | 5 | 531 | exact | `c525096cb0b4…41c2` | `9660695677c3…cb49` |
| `selfhosted/obsidian-livesync` | 8 | 574 930 | exact | `88858eb0b4e1…fa22` | `8013e113f90a…8fb9` |

**Every claim: `ceph` == `r2` == live, zero per-file gaps in either direction.** The content
digests reproduce the 2026-09-01 proof's values exactly, which independently corroborates that
the volumes are unchanged.

The metadata comparison is new here and is the stronger half. For
`paperless-ngx-media`, `syncthing-data` and `obsidian-livesync` the restored mode/uid/gid
manifest is **identical to live, entry for entry, including the volume root** - the read-only
staging property that makes a kopiur restore mode-faithful where a VolSync restore relaxes every
mode by a group-write bit.

### What "properly" can and cannot mean here

Asked to re-prove these claims "properly", the honest answer differs by claim, and for most of
them it is that **no ceremony can make the proof deeper than the volume is**:

- `autobrr` holds one 2 179-byte TOML file. Its real state is in `postgres-17`, backed up
  separately. The volume is thin **by design**, not by omission - and that is now measured, not
  assumed.
- `paperless-ngx-media` holds one **zero-byte** file, `media.lock`. Its content digest is the
  sha256 of the empty string. This is the Stage 1 failure mode in a milder form: the comparison
  is arithmetically valid and evidentially close to empty.
- `syncthing-data` holds four `.stfolder` marker files and one 48-byte `hello-from-cluster.txt`.
  It is a **scaffold**: four empty sync folders, no synced content at all.
- `ntfy`'s 2 files are its *entire* state - a message cache and `auth.db` (users and ACLs).
  Small in count, but the proof covers 100% of what the claim holds, including a `2700`
  directory only a matching mover identity can read.
- `obsidian-livesync`'s 8 files are a real CouchDB: an `obsidian_vault` database across two
  shards plus `_users`/`_replicator`/`_dbs`/`_nodes`. Small in count, genuinely irreplaceable in
  content.

So: **`ntfy` and `obsidian-livesync` are not really "too small to prove much"** - their proofs
cover their whole content and that content is real. `autobrr`, `paperless-ngx-media` and
`syncthing-data` are exactly what the 2026-09-01 finding said they were, and re-running the
comparison at greater ceremony would not change that. Manufacturing a synthetic file to fatten
the proof was considered and rejected: it would write to a live claim, and it would prove kopiur
can round-trip a file this task wrote rather than anything about the app's data.

## Part 2 - `downloads/prowlarr-config`, re-drilled destination-identically

The 2026-09-01 row was sound but **not destination-identical**: `offset: 0` on `ceph` resolved to
a *scheduled* snapshot that ran between the verification snapshot and the restore, while `r2`
resolved to the verification snapshot. The two trees were restores of different points in time,
so they could only be compared with caveats.

Two facts made a clean pair achievable. First, a churn probe: two live manifests **75 seconds
apart were byte-identical across all 713 files**, so the volume is quiescent at short range even
though it drifts over hours. Second, the run was placed in the gap between cluster-wide
scheduled `ceph` bursts (~13:2xZ and ~17:2xZ), so nothing could intervene.

Verification snapshots were created back-to-back and both reported **identical stats**:

| | `ceph` | `r2` |
|---|---|---|
| `Snapshot` | `prowlarr-ceph-w2ver` | `prowlarr-r2-w2ver` |
| created | `2026-09-02T16:47:20Z` | `2026-09-02T16:47:21Z` |
| `filesNew` / `sizeBytes` | **713 / 56 777 475** | **713 / 56 777 475** |
| resolved `kopiaSnapshotID` | `fc53141c44c7af094ab8b4edde85e9ed` | `91b71c1c54e5c57bf3e3cbeaf205c5b2` |

Both restores resolved to exactly those IDs - no scheduled snapshot intervened.

An `L1`/`L2` bracket around the snapshot window gave a stable set of **712 of 713** files; the
single file outside it is `./logs/prowlarr.txt`, the app's own continuously-appended log.

| measure | `ceph` restore | `r2` restore |
|---|--:|--:|
| files | **713** | **713** |
| directories | 7 | 7 |
| symlinks | 0 | 0 |
| bytes | **56 777 475** | **56 777 475** |
| content tree digest | `576321fa720f9c3a4c0dc9a15d53462e683be61f8cbb22b368451a2a3b143578` | **identical** |
| mode+uid+gid digest | `31d7ffd53bf2ec1aa857128e7cf9b218730487551f1a0d38108a9eaf8c2b9530` | **identical** |
| stable-set files missing | **0** | **0** |
| ceph-vs-r2 path/content differences | **0** | |

**Both destinations reproduced 713 files and 56 777 475 bytes - matching both snapshots exactly,
byte for byte - with identical content *and* identical metadata digests.** The last ambiguous
row in the fleet evidence table is closed.

## Part 3 - the exposure a thin proof actually hides

The forward-looking risk on these claims is not fidelity. It is that **retiring a claim makes
kopiur the only engine, so its restore must work at the volume's plausible future size, not its
current one** - and a near-empty volume is precisely the case where that regime is untested.

Applying the [cache gate](kopiur-r2-restore-cache-gate-2026-09-02.md) model - required cache =
`min(snapshot sizeBytes, ~6.2 GiB)`, a **cliff rather than a slope**, and a failed `Restore` is
terminal and never retries:

| claim | PVC | cache | usable cache | now | exposure |
|---|--:|--:|--:|--:|---|
| `downloads/autobrr` | 5Gi | 2Gi | 1.95 GiB | 2 179 B | exposed above 1.95 GiB - implausible for a TOML config |
| `downloads/prowlarr-config` | 5Gi | 2Gi | 1.95 GiB | 54.1 MiB | exposed above 1.95 GiB - low risk at 2.7% |
| `selfhosted/paperless-ngx-media` | **50Gi** | 5Gi | 4.87 GiB | **0 B** | **exposed above 4.87 GiB** |
| `selfhosted/ntfy` | 2Gi | 2Gi | 1.95 GiB | 184 KiB | **safe** - cache >= max possible content |
| `selfhosted/syncthing-data` | **15Gi** | 5Gi | 4.87 GiB | 531 B | **exposed above 4.87 GiB** |
| `selfhosted/obsidian-livesync` | 2Gi | 2Gi | 1.95 GiB | 561 KiB | **safe** - cache >= max possible content |

`ntfy` and `obsidian-livesync` are structurally safe: their 2Gi PVCs cannot hold more than the
~1.95 GiB their caches cover, so the cliff is unreachable without also resizing the claim.

The two flagged claims are the ones whose whole purpose is to hold data they do not yet hold -
`paperless-ngx-media` is where paperless writes scanned originals and thumbnails, and
`syncthing-data` is where synced files land. Both would cross their cache the first time they
are used as intended, and the failure would only be discoverable during an actual restore.

This is the concrete argument for the drift document's deferred proposal: because the ~6.2 GiB
plateau is a property of kopia rather than of any claim, **a single component default of 10Gi
would cover every claim in the fleet**. That remains a captain decision - it would require
recreating all 30 populators once.

## Part 4 - per-claim retirement verdict

Retirement is the captain's decision. This is the evidence position for each claim.

| claim | proof | verdict |
|---|---|---|
| `downloads/prowlarr-config` | 713 files / 54.1 MiB, destination-identical in content and metadata, stable-set gap 0 | **Ready.** The strongest proof of the six and the one that was previously ambiguous. |
| `selfhosted/obsidian-livesync` | 8 files / 561 KiB, 100% of claim content, metadata identical to live | **Ready on the evidence** - but this is a real Obsidian vault, i.e. genuinely irreplaceable user data. Same *class* of content as the captain's `paperless-ngx` carve-out, so worth a deliberate decision rather than an automatic one. |
| `selfhosted/ntfy` | 2 files / 184 KiB, 100% of claim content incl. a `2700` directory | **Ready.** `auth.db` is real non-regenerable state, but the proof covers all of it and the cache is structurally sufficient. |
| `downloads/autobrr` | 1 file / 2 179 B, complete, cache risk implausible | **Ready.** Thin by design; real state is in `postgres-17`, which is backed up independently and is unaffected by retiring this claim. |
| `selfhosted/syncthing-data` | 5 files / 531 B - a scaffold, no synced content | **Not ready.** Nothing meaningful is provable, and 15Gi of intended content sits behind a 4.87 GiB cache. Raise the cache and re-prove once it holds real data. |
| `selfhosted/paperless-ngx-media` | 1 file / **0 bytes** | **Not ready.** Empty because paperless holds **zero documents** (`documents/originals` and `documents/thumbnails` both exist and are empty). This is the volume that will hold the irreplaceable scanned originals the captain protects `paperless-ngx` for; a 50Gi claim behind a 4.87 GiB cache. Recommend it stays dual-engine on the same reasoning as its sibling. |

## Finding 1: creating a verification snapshot deletes an older one

**Creating a kopiur `Snapshot` CR is not a read-only act.** The two `prowlarr` verification
snapshots pushed each policy past its GFS retention limit, and kopiur immediately pruned the
oldest snapshot on each - deleting the underlying kopia snapshot with it, because scheduled
snapshots carry `deletionPolicy: Delete` plus the `snapshot-cleanup` finalizer.

The operator states it plainly:

```
kopiur_controller::snapshot_policy: pruned backup (GFS retention)
    config=prowlarr-ceph backup=prowlarr-ceph-20260901172248
kopiur_controller::snapshot_policy: pruned backup (GFS retention)
    config=prowlarr-r2   backup=prowlarr-r2-20260902153410
kopiur_controller::snapshot: snapshot deleted by batch Job; finalizer removed
    backup=prowlarr-ceph-20260901172248 snapshot_id=2dc7face231ee8ba2c07244380805f9a
kopiur_controller::snapshot: snapshot deleted by batch Job; finalizer removed
    backup=prowlarr-r2-20260902153410   snapshot_id=39cf40965ed49449b9ea04d5a7df1694
```

Both evictions follow the declared retention deterministically:

| policy | retention | evicted | why |
|---|---|---|---|
| `prowlarr-ceph` | `keepHourly: 6`, `keepDaily: 14`, `keepWeekly: 10`, `keepMonthly: 6` | `20260901172248` | fell outside the 6 most recent hourly slots and was not the latest snapshot of its day |
| `prowlarr-r2` | `keepDaily: 30`, `keepWeekly: 12`, `keepMonthly: 12` - **no `keepHourly`** | `20260902153410` | with no hourly tier, only the day's *latest* survives; the verification snapshot became that |

**The `r2` case is the sharp one and applies to every `r2` policy in the fleet:** with no
`keepHourly` tier, taking any on-demand `r2` snapshot **evicts that day's previously-newest
snapshot**. A drill that takes several would walk the daily tier backwards.

**Impact here was negligible, and that is a measured claim rather than a hope.** The evicted
`r2` snapshot was replaced by a newer one of the same volume, restore-proven byte-identical to
`ceph` in [part 2](#part-2---downloadsprowlarr-config-re-drilled-destination-identically), so
the offsite recovery point moved *forward* by 73 minutes and daily granularity for 2026-09-02 is
intact. The evicted `ceph` snapshot was a 4-hour granularity point inside 2026-09-01 that
`keepHourly: 6` would have retired at the next scheduled run ~35 minutes later.

**Practical rule:** prefer restoring from an existing scheduled snapshot. The five near-empty
claims in [part 1](#part-1---the-five-near-empty-claims) needed **no** new `Snapshot` CRs at all,
because their content is static and the existing `ceph`/`r2` snapshots already held identical
content. Create a verification snapshot only when destination-identity genuinely requires a
matched pair - as it did for `prowlarr-config` - and expect it to cost the oldest snapshot in the
tier it lands in.

## Safety and cleanup evidence

| check | result |
|---|---|
| `Snapshot` CRs deleted by this task | **0** |
| `Snapshot` CRs created by this task | 2 (`prowlarr-{ceph,r2}-w2ver`, both `Succeeded`) |
| `Snapshot` CRs pruned by operator retention | 2, both attributed above with operator log evidence |
| cluster-wide `Snapshot` census | 290 before, 290 after (288 pre-dating the task + 2 created - 2 pruned) |
| `Snapshot` CRs left `Running` or `Pending` | **none** - a `Running` snapshot would block later scheduled backups under `concurrencyPolicy: Forbid` |
| `Failed` snapshots | 1, `ai/opencode-ceph-20260831055425` from 2026-08-31 - pre-existing and unrelated |
| `SnapshotPolicy` / `SnapshotSchedule` / `ClusterRepository` / `ReplicationSource` touched | **0** |
| `ReplicationSource` objects remaining | **78** = 26 dual-engine claims x 3 destinations |
| schedules on all 6 touched claims (12 objects) | `Ready=True`, `ScheduleRunnable=True`, none suspended |
| mover Jobs mounting a live claim | **0** - all 12 mounted only their own drill PVC |
| live claim PVC uid / PV / pod name / start time / restart count | unchanged on all 6, before and after |
| `ceph health` | `HEALTH_OK` before and after |
| drill leftovers (`-l fm.homeops/restore-drill`) | none - `No resources found` |
| drill PVCs | all 12 deleted (they do **not** inherit the `Restore`'s labels, so they were removed by name) |
| standing populators | 30, intact; `tdarr` and `radarr` at `10Gi` |

### On "census unchanged"

The brief asked for the `Snapshot` census to be recorded before and after and confirmed
unchanged. Taken literally that is not a meaningful invariant on this cluster: scheduled
snapshots land continuously, and retention prunes continuously, so the count moves on its own.
The invariant that carries the safety meaning is **"no snapshot disappeared that this task did
not account for"**, and that is what is demonstrated above - every one of the 290 rows is
attributed, and the only two deletions are the operator's own retention, with logs naming both.
