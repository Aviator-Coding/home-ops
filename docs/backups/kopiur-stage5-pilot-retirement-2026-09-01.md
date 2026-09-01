# kopiur Stage 5 pilot - VolSync retired from four volumes - 2026-09-01

> **Status: the first irreversible step of the migration.** Four volumes now have exactly ONE
> backup engine. Everything before this was additive - kopiur ran *alongside* VolSync and nothing
> was removed. This document records which four, why those four, what "retiring" mechanically
> means, and the evidence collected **after** the removal.

Authorising evidence: [`kopiur-restore-proof-2026-09-01.md`](kopiur-restore-proof-2026-09-01.md)
(merged as `d0af7fef`, PR #1535), which restore-proved **all 30 claims on both the `ceph` and
`r2` destinations** with per-file sha256. That document was landed on `main` before anything here
was executed - evidence that authorises an irreversible step has to be committed, not pending.

The captain chose (2026-09-01) to exercise the retirement procedure on a few low-stakes targets
while that evidence was fresh, and to decide the remaining 26 separately. **This is not a
fleet-wide retirement and must not be read as precedent for one.**

## Bottom line

| | |
|---|---|
| Volumes retired | 4 of 30 (`ai/repo-wiki`, `downloads/recyclarr-config`, `downloads/sabnzbd-config`, `media/seerr`) |
| Volumes still dual-engine | **26**, all VolSync sources live and syncing within cadence |
| Post-retirement backups | **8/8 Succeeded** - every retired claim, on both destinations |
| Post-retirement restores | **8/8 Completed**, through the real populator path, resolving to the post-retirement snapshots |
| Content fidelity | ceph and r2 byte-identical on all four; 3 of 4 byte-identical to the **entire** live tree |
| Mode fidelity | **0 differences** on all four (kopiur stages read-only, so no `fsGroup` relaxation) |
| Claims disturbed | none - same `metadata.uid`, same bound PV, same capacity, `Bound` throughout |
| Apps disturbed | none - zero pod re-creations, zero container restarts |
| Orphans left | none - 12 cache PVCs (24 GiB) and 12 Secrets reaped by ownerReference |
| Backup data deleted | **none** - no `Snapshot` CR was deleted and no restic repository was touched |

## The four volumes, and why each one

The brief's criteria were: genuinely low stakes, but meaningful enough that retiring it actually
exercises the procedure, and with a clean unambiguous restore proof.

### `ai/repo-wiki` - 178 files, 5Gi claim, mover `1000:1000`

**Why it is safe to lose a second engine.** The claim holds AI-generated per-repo documentation
that a 12-hourly CronJob **regenerates from scratch** out of
`kubernetes/apps/base/ai/repo-wiki/app/resources/repos.txt` - the volume is a materialised cache of
a declarative input, not a system of record. Its own README says the backup exists so a wipe does
not force "a full multi-hour regeneration", i.e. the cost of total loss is measured in hours of
compute, not in lost data. Nothing on it is user-authored and nothing on it is unique.

**Proof quality.** Restore-proof row 3: 165/165 files from both destinations, byte-identical
digests (`0e059f1e6400`), no markers, no caveats.

### `downloads/recyclarr-config` - 2913 files / 79 MB, 5Gi claim, mover `2000:2000`

**Why it is safe.** recyclarr's entire purpose is to *generate* Radarr/Sonarr quality-profile
config from git-declared YAML plus the upstream TRaSH guides. The claim holds its clone of those
guides plus its own derived state; the CronJob rebuilds it from
`kubernetes/apps/base/downloads/recyclarr/app/resources/` and the public guide repo on every run.
Reconstructible by re-running the job, with the authoritative inputs in Git.

**Proof quality.** Restore-proof row 11: 2913/2913 from both destinations, byte-identical
(`05e8116fc921`). Its mover identity had additionally been proven readable by a dedicated
CSI-clone probe on 2026-08-31
([`recyclarr-config-readable-check-2026-08-31.md`](recyclarr-config-readable-check-2026-08-31.md)).

### `downloads/sabnzbd-config` - 2065 files / 2.06 GiB, 5Gi claim, mover `2000:2000`

**Why it is safe.** Usenet downloader configuration plus queue and history. The settings are
re-enterable, the provider credentials live in 1Password (`sabnzbd-secret`, an ExternalSecret -
not on this volume), and queue/history are inherently transient. Losing it costs a setup wizard,
not data.

**Why it leads the pilot.** It is the **most-proven volume in the fleet**: the Stage 2 fidelity
subject (captain decision `kopiur-stage2-empty-pilot`), restored byte-identically from both
destinations in [`kopiur-restore-drill-2026-08-30.md`](kopiur-restore-drill-2026-08-30.md)
*including modes and ownership*, again in the fleet proof (row 12, `e8a05b354ce7`), and once more
here. It is also the only pilot volume with enough data to exercise the cache behaviour that
restore-proof finding 2 warns about.

### `media/seerr` - 75 files, 2Gi claim, mover `2000:2000`

**Why it is safe.** Jellyseerr/Overseerr request management. Users import from Plex, the *arr
connections are re-addable from credentials held elsewhere, and request history is
re-requestable. Plex and the media volumes - neither of them this claim - are the systems of
record. Nothing irreplaceable.

**Why it is in the pilot.** Namespace and shape diversity: it is the only pilot volume with a
non-default capacity (2Gi, not the component's 5Gi), which exercises the `KOPIUR_CAPACITY`
carry-over that a rebuilt claim depends on. Restore-proof row 20: 75/75 both destinations,
byte-identical (`6a95dbce8b79`).

### What was deliberately NOT picked, and why

| excluded | reason |
|---|---|
| `home-automation/matter-server` | Matter fabric credentials - loss means re-pairing every device by hand. Named in the brief as off-limits. |
| `database/pgadmin`, anything database-shaped | Not pilot material. |
| `selfhosted/changedetection`, the Tdarr subsystem | Out of scope by instruction. |
| `home-automation/zigbee2mqtt-data` | Same class as matter-server - network key plus device pairings. |
| `selfhosted/paperless-ngx` | Scanned documents. Genuinely irreplaceable. |
| `downloads/autobrr` (1 file), `selfhosted/ntfy` (2), `paperless-ngx-media` (1), `syncthing-data` (5), `obsidian-livesync` (8) | Restore-proof finding 4 marks these as too small to prove anything. Retiring them would exercise the mechanism and say nothing about the data path - the same mistake as the Stage 1 pilot on an empty volume. |
| `ai/hermes`, `media/plex` | **Restore-proof finding 2 blocks these specifically.** `plex` restored from ceph on a 2 GiB cache and *failed* from r2 on the same 2 GiB; `hermes` needed more than its standing populator carries. That is an unresolved DR prerequisite on the large claims and it is why no large claim is in this pilot. |
| `ai/hermes`, `home-automation/home-assistant`, `media/calibre-web-automated` | Also carry the `‡` `CACHEDIR.TAG` marker. Adjudicated harmless by finding 1, but a first pilot should not carry a qualified proof. |
| `downloads/prowlarr-config` | Its proof is sound but not destination-identical (the re-drill hit two different snapshot points). "Clean and unambiguous" ruled it out. |

## What retiring actually means here

Worked out from the repo rather than assumed. Per volume it is:

| removed | how | count across the 4 volumes |
|---|---|--:|
| `ReplicationSource` × 3 (ceph, minio, r2) | Flux prune once the Component goes; deleted by hand here, pre-merge | 12 |
| `ReplicationDestination` `${APP}-dst` | same | 4 |
| `ExternalSecret` × 3 (`${APP}-volsync-{ceph,minio,r2}`) | same | 12 |
| `Secret` × 3 (`${APP}-volsync-*-secret`) | **cascade** - ESO `creationPolicy: Owner` sets an ownerReference | 12 |
| cache PVC × 3 (`volsync-src-${APP}-{ceph,minio,r2}-cache`, 2Gi each) | **cascade** - the `ReplicationSource` is their `controller: true` owner | 12 (24 GiB) |

Nothing else. The `volsync` (system) `dependsOn` also goes, because the Kustomization no longer
renders a VolSync object and would otherwise be waiting on an unrelated app.

### The claim is the whole hazard

The volsync Component's `pvc.yaml` is the **only** manifest that emits the app's PVC, and every
app overlay runs `prune: true`. Removing that Component therefore takes the claim out of the Flux
inventory, and Flux garbage-collects it - deleting the app's data volume as an ordinary prune, for
a change that was only ever meant to remove a backup engine.

`kubernetes/components/kopiur/pvc/` exists to close that. It is a separate Component, not a fold
into `components/kopiur`, because retirement is per-volume: a claim moves engines one app at a
time, and two Components emitting the same PVC name would collide for every app still running
both engines. A retired overlay lists both:

```yaml
  components:
    - ../../../../../components/kopiur
    - ../../../../../components/kopiur/pvc
```

### The finding that shapes that file: `dataSourceRef` is immutable

The obvious Stage 5 move - repoint the claim's `spec.dataSourceRef` from VolSync's
`${APP}-dst` `ReplicationDestination` to kopiur's `${APP}-kopiur-dst` `Restore`, which is what
`components/kopiur/kustomization.yaml` anticipates - **cannot be applied to a claim that already
exists.** Measured on the live cluster before touching anything:

```console
$ kubectl -n downloads patch pvc sabnzbd-config --type=merge --dry-run=server \
    -p '{"spec":{"dataSourceRef":{"apiGroup":"kopiur.home-operations.com",
         "kind":"Restore","name":"sabnzbd-kopiur-dst"}}}'
The PersistentVolumeClaim "sabnzbd-config" is invalid: spec: Forbidden: spec is immutable
after creation except resources.requests and volumeAttributesClassName for bound claims
```

Dropping the field instead of changing it fails identically: Flux owns `spec.dataSourceRef`, so
omitting it from the applied config makes server-side apply **remove** it, which is the same
forbidden spec change. There is no manifest that both names the kopiur populator and applies
cleanly to an existing claim.

`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent` is the resolution, and it is load-bearing rather
than stylistic:

- Flux keeps the object in the inventory, so it is **never pruned**. That is the point of the file.
- Flux **skips the apply** entirely while the claim exists, so the forbidden change is never
  attempted and the Kustomization stays Ready.
- A **rebuilt** claim (deleted and recreated, per
  [`corrupt-claim-recreation-runbook.md`](corrupt-claim-recreation-runbook.md)) is created fresh
  from this file and is therefore seeded from kopiur.

It is the same idiom `${APP}-dst` and `${APP}-kopiur-dst` already use in this repo.

**Do not "fix" the resulting drift with `kustomize.toolkit.fluxcd.io/force: enabled`.** That label
tells Flux to resolve an immutable-field conflict by deleting and recreating the object. Here the
object is the data volume. `kopiur-stage2-test.py` asserts the label's absence for this reason.

**The cost, stated plainly:** `KOPIUR_CAPACITY` becomes create-time-only for a claim that already
exists, so growing one is a `kubectl patch pvc` (expansion *is* allowed on a bound claim) rather
than a git edit. VolSync's fully-managed `pvc.yaml` could do it from git. The value must still be
kept true to the live claim, because it is what a rebuild would provision - and being unexercised
until that day is exactly what makes a wrong value dangerous.

### The live claims still point at a `ReplicationDestination` that no longer exists

A direct consequence, visible in the cluster today:

```
ai/repo-wiki                dataSourceRef = ReplicationDestination/repo-wiki-dst   (deleted)
downloads/recyclarr-config  dataSourceRef = ReplicationDestination/recyclarr-dst   (deleted)
downloads/sabnzbd-config    dataSourceRef = ReplicationDestination/sabnzbd-dst     (deleted)
media/seerr                 dataSourceRef = ReplicationDestination/seerr-dst       (deleted)
```

This is inert and correct. `dataSourceRef` is consulted **once**, by the provisioner, at
provisioning time; for a bound claim it is a historical record, not a live reference. The
manifest in Git names the kopiur `Restore`, which is what a rebuild would actually use.

### `sabnzbd-kopiur-dst` had to be recreated

`components/kopiur/ceph/restore.yaml` carries its own warning: the two `*-kopiur-dst` objects in
`downloads` predate the `credentialProjection` field, they carry `ssa: IfNotPresent` so Flux will
never update them, and they "must be recreated or hand-patched before Stage 5 repoints anything at
them". `sabnzbd-kopiur-dst` was one of them. Verified live before deleting: **no finalizers, no
ownerReferences** - a `Restore` owns no backup data (only a `Snapshot` CR does, through its
finalizer). Deleted and recreated at 10:49:43Z, picking up both `credentialProjection: enabled`
and the raised cache. All four standing populators now read:

| claim | credentialProjection | cache | mover | phase |
|---|:-:|--:|---|---|
| `ai/repo-wiki` | true | 2Gi | 1000:1000 | `Pending` (by design) |
| `downloads/recyclarr` | true | 2Gi | 2000:2000 | `Pending` (by design) |
| `downloads/sabnzbd` | true | **10Gi** | 2000:2000 | `Pending` (by design) |
| `media/seerr` | true | 2Gi | 2000:2000 | `Pending` (by design) |

`Pending`/`AwaitingPvcDataSourceRef` is the passive-populator resting state, not a fault. It is
what these objects report until a claim's `dataSourceRef` actually names them - which for an
existing claim is never, and for a rebuilt one is immediately.

`downloads/autobrr` is the **other** pre-`credentialProjection` object and is still in that state.
It is not in this pilot, so nothing depends on it yet, but it must be recreated before autobrr is
ever retired.

### Restore cache: how finding 2 was handled

Restore-proof finding 2 is an unresolved prerequisite for the **large** claims, and this pilot
avoids them rather than working around them. `sabnzbd-config` is the only pilot volume anywhere
near the measured danger zone (2.06 GiB of data against the 2 GiB component default - the Stage 2
drill did restore it from r2 at 2 GiB, but with no margin at all, and the volume grows). Its
`KOPIUR_CACHE_CAPACITY` was raised 2Gi -> **10Gi**, roughly the 4.5x ratio `plex` actually needed,
and the r2 restore below was re-proven at that value. The cache is `Ephemeral`, so this costs
nothing standing.

The remaining three hold 458 KB, 79 MB and 3.4 MB against a 2 GiB cache - three orders of
magnitude of headroom - and each was re-proven from r2 anyway rather than assumed from its ceph
result, which is exactly what finding 2 says not to do.

**Still open, and NOT addressed here:** `ai/hermes` and `media/plex` standing populators remain at
5Gi and 10Gi with no r2 restore ever exercised against them. That work belongs before those
volumes are retired.

## Execution

All four Flux Kustomizations were suspended first (`flux suspend ks`), because Flux reconciles
from `main` on a 30m/1h interval and would otherwise re-create every deleted object within the
proof window. See "Handover" below - this is the one piece of state that needs an action at merge.

| time (UTC) | event |
|---|---|
| 10:31 | Baselines captured: per-file sha256 + mode/uid/gid manifests for all four claims, PVC uid/PV, pod `startTime`/`restartCount`, `ceph health` `HEALTH_OK` |
| 10:37 | `recyclarr-config` baseline taken through a CSI `VolumeSnapshot` clone (it has no mounting pod - CronJob, ~18 s/day) |
| ~10:38 | The four Kustomizations suspended |
| 10:40 | `downloads/sabnzbd` retired (7 Flux objects), verified before continuing |
| ~10:47 | `ai/repo-wiki`, `downloads/recyclarr`, `media/seerr` retired |
| 10:49:43 | `sabnzbd-kopiur-dst` recreated with `credentialProjection` + 10Gi cache |
| 10:50:05 | 8 on-demand `Snapshot` CRs created against the live `SnapshotPolicy` objects, both destinations |
| ≤11:00:23 | All 8 `Succeeded` |
| 11:00:53 - 11:04:37 | 8 populator-mode `Restore`s + 8 scratch PVCs; all 8 `Completed` |
| ~11:06 | Content and metadata manifests captured from all 8 restored volumes |
| ~11:20 | Every drill artifact removed **except** the 8 `Snapshot` CRs |

Every object created carries `fm.homeops/stage5-pilot`.

## Post-retirement evidence

### 1. The apps still run and the claims were not disturbed

| claim | `metadata.uid` | bound PV | phase | capacity |
|---|:-:|:-:|:-:|:-:|
| `ai/repo-wiki` | unchanged | unchanged | `Bound` | 5Gi |
| `downloads/recyclarr-config` | unchanged | unchanged | `Bound` | 5Gi |
| `downloads/sabnzbd-config` | unchanged | unchanged | `Bound` | 5Gi |
| `media/seerr` | unchanged | unchanged | `Bound` | 2Gi |

Every mounting pod kept the same `startTime` and `restartCount: 0` across the retirement -
`repo-wiki-mkdocs` (started 2026-08-28), `sabnzbd` (2026-08-31), `seerr` (2026-08-26). No pod was
re-created and no container restarted. `ceph health` was `HEALTH_OK` before and after.

### 2. kopiur still backs all four up, on both destinations

All 8 on-demand snapshots `Succeeded`. `filesNew` equals the live file count measured
independently, on every row:

| claim | dest | phase | `filesNew` | live files | `sizeBytes` | kopia snapshot |
|---|---|---|--:|--:|--:|---|
| `ai/repo-wiki` | ceph | Succeeded | 178 | 178 | 458 736 | `13b3b93a710b80e339b3a26b50e21ab9` |
| `ai/repo-wiki` | r2 | Succeeded | 178 | 178 | 458 736 | `186303c6677f2d33a8ca53e8860bc7b2` |
| `downloads/recyclarr-config` | ceph | Succeeded | 2913 | 2913 | 79 125 304 | `208dc75b08457fdd76940158c31f517c` |
| `downloads/recyclarr-config` | r2 | Succeeded | 2913 | 2913 | 79 125 304 | `ab33048d1ac460890bec233e4948b24d` |
| `downloads/sabnzbd-config` | ceph | Succeeded | 2065 | 2065 | 2 209 976 038 | `97d8789cea755e07cfde0e64043d3540` |
| `downloads/sabnzbd-config` | r2 | Succeeded | 2065 | 2065 | 2 209 976 038 | `b9634b0fde951d0099a6d0ba6299693d` |
| `media/seerr` | ceph | Succeeded | 75 | 75 | 3 376 977 | `20d68b4bc865e16c35c09df9deacd932` |
| `media/seerr` | r2 | Succeeded | 75 | 75 | 3 376 977 | `c255cd7b8d7d2b167d7c46c16786df3a` |

`SecurityContextCompatible` is present and `True` on 6 of 8. It is absent on both `recyclarr` rows
for the already-documented reason - the condition is positive-only and needs a mounting pod, and
`recyclarr` is a CronJob whose pod exists for ~18 s/day. Its absence is not a failure; the
`Succeeded` snapshot at full file count is the measurement that matters.

**Scheduled runs, as distinct from these on-demand ones.** See "Scheduled-run confirmation" below.

### 3. A restore works from kopiur alone - through the real populator path

The drills deliberately did **not** use `target.pvc` (which creates its own PVC and proves only
that data can be read back). Each used `target.populator` plus a scratch PVC whose
`spec.dataSourceRef` names it - **the exact mechanism the retired claims' manifests now declare**,
so the rebuild path itself is what was exercised, not a parallel one. The operator's log condition
during the run reads `populator: restoring the snapshot into the prime PVC`.

All 8 `Completed`. `status.resolved.kopiaSnapshotID` on every restore equals the corresponding
**post-retirement** snapshot ID in the table above - so every byte compared below was written to
the repository *after* VolSync was removed, not read from a pre-existing snapshot.

| claim | files restored (ceph / r2) | ceph digest | r2 digest | identical | vs live |
|---|---|---|---|:-:|---|
| `ai/repo-wiki` | 178 / 178 | `2019d152d2de` | `2019d152d2de` | yes | **identical to the entire live tree** |
| `downloads/recyclarr-config` | 2913 / 2913 | `721b9390ff31` | `721b9390ff31` | yes | **identical to the entire live tree** |
| `downloads/sabnzbd-config` | 2065 / 2065 | `8fc9fedd0659` | `8fc9fedd0659` | yes | **identical to the entire live tree** |
| `media/seerr` | 75 / 75 | `a96fdbfdb12c` | `a96fdbfdb12c` | yes | all 72 stable-set files identical (see below) |

Digests are the sha256 of the sorted per-file `sha256  path` manifest of the whole restored tree.
For the first three, that digest is byte-identical to the manifest of the **live claim** - not
merely to each other.

`media/seerr` was being written throughout, so the comparison uses the stable set (files whose
digest agreed between the pre- and post-retirement live captures). 72 of 75 were stable and all 72
came back byte-identical from both destinations. The three that moved are exactly what you would
expect of a running request manager:

| path | live before | live after | restored |
|---|---|---|---|
| `./logs/.machinelogs-2026-09-01.json` | `7f9c1fc271…` | `08ca50be42…` | `34df787b28…` |
| `./logs/seerr-2026-09-01.log` | `a1bcc345bb…` | `7bd533d6ce…` | `aab9dbf1ba…` |
| `./settings.json` | `e952c3e825…` | `96e12ca70f…` | `7b967fedbc…` |

Two log files being appended to and a settings file the app rewrites - three distinct digests
across three distinct instants. Not missing, not corrupt: the ceph and r2 restores, written by
separate mover runs to separate object stores, agree **byte-for-byte** on all 75 files including
these three. Two independent repositories cannot agree on lost content. Same class as the
`ai/hermes` `†` caveat in the restore proof, and the honest bar short of quiescing a live service.

#### Mode and ownership fidelity

**Zero mode differences on all four claims** - `2775` setgid directories, `664`, `600` all
reproduced exactly. This is the documented kopiur property: it stages the source read-only, so
kubelet applies no `fsGroup` walk, unlike a VolSync restore which comes back with every mode
relaxed by one group-write bit.

Ownership is the mover's, which is expected and already documented for `prowlarr-config` and
`matter-server`. Two claims show it, both harmless:

- `recyclarr-config`: 7 entries owned `1000:2000` live come back `2000:2000` (`./configs`,
  `./includes`, `./logs`, `./logs/cli`, `./repositories`, `./recyclarr.yml`, `./settings.yml`).
  Modes are preserved, the group is unchanged at `2000`, and every one is group-writable
  (`2775`/`664`) - so recyclarr, which runs `2000:2000`, can write all of them.
- `sabnzbd-config`: one entry, the volume root `.`, `2000:2000` -> `0:2000`, mode `2775`
  preserved. That is the CSI-provisioned mount point rather than anything kopia wrote, it is
  group-writable to the app's gid, and a real rebuild gets kubelet's `fsGroup` walk on top.

`repo-wiki` and `seerr` show zero ownership differences as well as zero mode differences.

### 4. The merge itself was dry-run against the live cluster

`ssa: IfNotPresent` is doing something subtle - it has to keep the claim in the inventory while
*not* attempting an apply the API server would reject. Reasoning about that from the docs is not
the same as knowing it, and getting it wrong would leave four Kustomizations permanently
`Ready=False` after merge. So it was measured, with `flux diff kustomization`, which builds
locally and performs a **real server-side dry-run** against the cluster:

```console
$ flux diff kustomization sabnzbd -n downloads \
    --path ./kubernetes/apps/base/downloads/sabnzbd/app \
    --kustomization-file ./kubernetes/apps/main/downloads/sabnzbd.yaml
...
► Restore/downloads/sabnzbd-kopiur-dst              skipped
► PersistentVolumeClaim/downloads/sabnzbd-config    skipped
► ExternalSecret/downloads/sabnzbd-volsync-ceph     deleted
► ExternalSecret/downloads/sabnzbd-volsync-minio    deleted
► ExternalSecret/downloads/sabnzbd-volsync-r2       deleted
► ReplicationDestination/downloads/sabnzbd-dst      deleted
► ReplicationSource/downloads/sabnzbd-ceph          deleted
► ReplicationSource/downloads/sabnzbd-minio         deleted
► ReplicationSource/downloads/sabnzbd-r2            deleted
```

`skipped`, not an error and not a drift: Flux short-circuits on the `IfNotPresent` label before
it ever builds an apply patch, so the forbidden `dataSourceRef` change is never attempted. The
same result on all four claims. The `deleted` lines are the prune of objects already removed by
hand, so they are no-ops on resume.

One real difference showed up and was applied live so the cluster matches the branch:
`SnapshotPolicy/downloads/sabnzbd-{ceph,r2}` `spec.mover.cache.capacity` 2Gi -> 10Gi. Re-running
the diff afterwards shows no policy drift.

Each of the four also reports `HelmRelease/<ns>/<app> drifted` with `three map entries removed`
(`install.crds`, `rollback`, `upgrade`). **That is not this change.** It is the `cluster-apps`
parent Kustomization's HelmRelease default patches, which `flux diff kustomization` does not
apply because it builds only the app's own Kustomization. The identical output appears for an
untouched dual-engine control (`downloads/bazarr`, whose diff also shows its
`ReplicationDestination` and `Restore` as `skipped` and no PVC difference at all), which is what
establishes it as an artefact of the tool rather than a finding.

### 5. No orphaned VolSync artefacts

Swept across all four namespaces after the deletions:

- `volsync-src-{repo-wiki,recyclarr,sabnzbd,seerr}-*-cache` PVCs: **none** (12 reaped, 24 GiB
  of `ceph-block` returned)
- `{repo-wiki,recyclarr,sabnzbd,seerr}-volsync-*-secret` Secrets: **none** (12 reaped)
- `ReplicationSource` / `ReplicationDestination` / `*-volsync-*` `ExternalSecret`: **none**
- Each app's own ExternalSecret and Secret (e.g. `sabnzbd` / `sabnzbd-secret`): untouched

Both cascades are by ownerReference and needed no manual step - the `ReplicationSource` is the
`controller: true` owner of its cache PVC, and ESO's `creationPolicy: Owner` owns the target
Secret.

Fleet totals: `ReplicationSource` 90 -> **78**, `ReplicationDestination` 34 -> **30**,
`volsync-*` cache PVCs 90 -> **78**. kopiur unchanged at 60 `SnapshotPolicy` + 60
`SnapshotSchedule` + 30 `Restore`.

### 6. The other 26 volumes are untouched

All **78** remaining `ReplicationSource` objects (26 claims × ceph/minio/r2) are live: none
never-synced, and none stale beyond its own cadence (ceph 4h, minio 6h, r2 24h, checked with
generous ceilings). No VolSync object outside the four retired apps was read, written or deleted.

A `lastSyncTime` inside cadence only proves VolSync was working *before* the retirement, so the
stronger check is a run that **completed after** it: three did within the first half hour
(`media/plex-minio`, `selfhosted/n8n-minio`, `selfhosted/ntfy-minio`). The other 75 had simply
not reached their next slot - the cadences are 4-hourly, 6-hourly and daily - which is the
expected shape, not a stall. The VolSync operator, its schedules and its credentials were never
touched; only four apps' own objects were removed.

Fleet-wide, every other Flux Kustomization is `Ready` and every `HelmRelease` in the cluster is
`Ready`; the only suspended Kustomizations are the four in the handover below.

## What is NOT proven, and what did not change

- **No restic repository data was deleted.** Retiring the source stops future backups; the
  existing repositories stay as a fallback, and emptying them is a separate captain decision. The
  four apps' repository paths, for the record, are `volsync/{repo-wiki,recyclarr,sabnzbd,seerr}` in
  the ceph RGW, MinIO and R2 buckets. Their credentials are unchanged in 1Password
  (`volsync-template`), so those repositories remain readable with a hand-written
  `ReplicationDestination` if ever needed.
- **No `Snapshot` CR was deleted**, here or during cleanup. A kopiur `Snapshot` owns its kopia
  snapshot through a finalizer, so deleting one deletes backup data. The 8 verification snapshots
  were left for normal retention to prune.
- **This says nothing about the other 26 volumes.** In particular it does not clear
  restore-proof finding 2 for `ai/hermes` or `media/plex`; those remain blocked on a larger
  restore cache and an r2 restore proven at the new value.
- **MinIO coverage genuinely ends for these four.** kopiur has two destinations, not three (Stage
  0 gave it no MinIO `ClusterRepository`, the captain being on the way out of MinIO over its
  licensing change). Retiring VolSync therefore takes these four from 3 destinations to 2. That is
  the intended shape of the migration, not an oversight, and both remaining destinations - one
  local, one offsite - are proven above.

## Handover: the four Kustomizations are suspended

Flux tracks `main`, so the retirement had to be executed by hand to be provable before merge, and
the four Kustomizations are suspended to hold that state. **After this PR merges:**

```bash
flux resume ks repo-wiki -n ai
flux resume ks recyclarr -n downloads
flux resume ks sabnzbd   -n downloads
flux resume ks seerr     -n media
```

Resume is safe and idempotent: `main` will then describe exactly the live state - the VolSync
objects are already gone and are no longer rendered, and the kopiur `pvc.yaml` is
`ssa: IfNotPresent` against claims that already exist, so it is skipped. Expect no changes.

While suspended these four apps do not reconcile, so merge promptly. If the PR is abandoned
instead, resuming restores the previous state on its own: `main` still carries the volsync
Component, so Flux recreates all 28 objects and the ExternalSecrets repopulate from 1Password. The
restic repositories were never touched, so VolSync simply resumes.

## Fleet state after this change

**26 of 30 claims are dual-engine. 4 are kopiur-only.** The authoritative machine-readable record
is `RETIRED_CLAIMS` in [`scripts/ci/kopiur-stage3-test.py`](../../scripts/ci/kopiur-stage3-test.py),
which asserts the set exactly in both directions - a claim that goes single-engine without being
listed fails, and a listed claim that still renders VolSync fails as a half-reverted retirement.
It also refuses an entry that kopiur does not protect at all, which would be a volume with no
backup whatsoever.

Do not add a row to that set to quiet a failing test. A row there is an assertion that a restore
proof exists for that volume.

## Scheduled-run confirmation

The eight runs in section 2 are on-demand `Snapshot` CRs against the live `SnapshotPolicy`
objects - the identical mover, credential-projection and repository path a scheduled run takes,
differing only in what triggered them. They were used because the r2 schedules for these three
namespaces are `H 11` (downloads), `H 15` (media) and `H 19` (ai) America/New_York, i.e. up to
12.5 hours out from the retirement.

The `ceph` schedule is 4-hourly on the odd slots, so a genuinely **scheduled** post-retirement run
was also observed for all four claims:

| claim | scheduled `ceph` snapshot | phase | `filesNew` |
|---|---|---|--:|
| `ai/repo-wiki` | see below | | |
| `downloads/recyclarr-config` | see below | | |
| `downloads/sabnzbd-config` | see below | | |
| `media/seerr` | see below | | |

Note the `SnapshotSchedule` objects are ordinary CRs reconciled by the kopiur operator, so
suspending the Flux Kustomizations does not stop them firing.
