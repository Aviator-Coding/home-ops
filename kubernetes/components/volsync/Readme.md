# Volsync Backup Configuration

This document explains the backup and restore strategy implemented using Volsync in the home-ops Kubernetes cluster.

> **VolSync is being replaced by kopiur, and retirement is under way.** As of 2026-09-02 this
> component protects **22** claims, not 30. VolSync has been retired from eight volumes, in two
> waves:
>
> * 2026-09-01 - `ai/repo-wiki`, `downloads/recyclarr-config`, `downloads/sabnzbd-config`,
>   `media/seerr`: a deliberate low-stakes pilot after every claim was restore-proven on both
>   kopiur destinations.
> * 2026-09-02 - `downloads/prowlarr-config`, `selfhosted/ntfy`, `downloads/autobrr` (app since removed entirely, same day),
>   `selfhosted/obsidian-livesync`: **not all regenerable.** `ntfy` holds real auth state and
>   `obsidian-livesync` is a genuine Obsidian vault, retired on an explicit captain decision after
>   an objection. `selfhosted/paperless-ngx` remains a permanent carve-out and stays dual-engine.
>
> Eight volumes were retired; seven of them are still present as **kopiur-only**
> (`downloads/autobrr` left the fleet when its app was removed the same day). The
> remaining 22 still run both engines, and retiring any of them is a separate
> captain decision.
>
> Two things a reader of this file needs to know:
>
> * **Their claims did not go with the sources.** `pvc.yaml` here is the only manifest that emits
>   an app's PVC, and app overlays run `prune: true` - so removing this Component without a
>   replacement makes Flux delete the data volume. A retired app therefore adds
>   [`../kopiur/pvc`](../kopiur/pvc/pvc.yaml), which takes the claim over. That file also explains
>   why it must carry `ssa: IfNotPresent`: `dataSourceRef` is immutable on a bound claim, so the
>   populator cannot simply be repointed.
> * **Their restic repositories still exist and were not emptied.** Retiring a
>   `ReplicationSource` never touches restic data - that asymmetry against kopiur's `Snapshot`
>   (which owns its kopia snapshot through a finalizer) is load-bearing. All eight repositories
>   stay readable as a fallback via a hand-written `ReplicationDestination`; the credentials are
>   unchanged in 1Password (`volsync-template`).
>
> Record and evidence:
> [`docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md`](../../../docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md)
> (wave one) and
> [`docs/backups/kopiur-wave-two-retirement-2026-09-02.md`](../../../docs/backups/kopiur-wave-two-retirement-2026-09-02.md)
> (wave two).
> Which claims are single-engine is asserted exactly by `RETIRED_CLAIMS` in
> [`scripts/ci/kopiur-stage3-test.py`](../../../scripts/ci/kopiur-stage3-test.py).

## Directory Structure

```
kubernetes/components/volsync/
├── ceph/                    # Local Ceph cluster backups (every 4 hours)
├── minio/                   # MinIO S3-compatible storage backups (every 6 hours)
├── r2/                      # Cloudflare R2 storage backups (daily)
├── backup/                  # The three destinations, without the PVC - for a claim that already exists
├── pvc.yaml                 # Creates ${VOLSYNC_CLAIM} seeded from the ReplicationDestination
└── kustomization.yaml       # Component: ./backup + ./pvc.yaml
```

`backup/` is the single definition of "back this PVC up to all three destinations".
The parent Component adds `pvc.yaml` on top of it; `backup/` on its own is also a
valid Flux Kustomization `path`, which is what the multi-volume pattern below uses.

## Apps with more than one volume

Every resource in this component is named from `${APP}`, and Flux allows exactly **one**
`postBuild.substitute` map per Kustomization. So one instance of the component can only
ever protect one claim - `${VOLSYNC_CLAIM:-${APP}}`. An app with a second volume needs a
second Flux Kustomization; there is no way to include the component twice in one.

Add it to the app's existing overlay yaml (`kubernetes/apps/main/<ns>/<app>.yaml`) as a second document. Point `path` at
`./kubernetes/components/volsync/backup` (the backup-only bundle - the PVC already
exists, so `pvc.yaml` must not be included), and set `APP` to the **claim name**:

```yaml
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app myapp-media          # == the PVC name; drives every object name
  namespace: &namespace mynamespace
spec:
  targetNamespace: *namespace
  commonMetadata:
    labels:
      app.kubernetes.io/name: myapp    # the parent app, so it groups with it
  interval: 30m
  timeout: 5m
  path: "./kubernetes/components/volsync/backup"
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  wait: false
  dependsOn:
    - name: volsync
      namespace: system
    - name: myapp                 # so the PVC exists before the first snapshot
      namespace: *namespace
  postBuild:
    substituteFrom:
      - name: cluster-secrets
        kind: Secret
    substitute:
      APP: *app
      VOLSYNC_CLAIM: *app
      VOLSYNC_CAPACITY: 50Gi      # used by the ReplicationDestination on restore
      VOLSYNC_CACHE_CAPACITY: 5Gi
      VOLSYNC_SCHEDULE_CEPH: "50 */4 * * *"   # stagger against the app's own sources
      VOLSYNC_SCHEDULE_MINIO: "25 */6 * * *"
      VOLSYNC_SCHEDULE_R2: "50 4 * * *"
```

Because `APP` is the claim name, this gets its own restic repository
(`s3://.../<claim>`), its own ExternalSecrets and its own `<claim>-dst`
ReplicationDestination - nothing collides with the parent app's.

Live examples: `kubernetes/apps/main/selfhosted/syncthing.yaml` (`syncthing-data`)
and `kubernetes/apps/main/selfhosted/paperless-ngx.yaml` (`paperless-ngx-media`).

**Pick the schedule minutes so they do not collide** with the other ReplicationSources
in the same namespace - three movers snapshotting the same Ceph pool at the same minute
is the one avoidable way to make backups slow.

## Timezone: VolSync vs kopiur

**Every cron schedule in this component is evaluated in `America/New_York`,
not UTC.** The cluster-wide `k8tz` mutating webhook (`system-controller/k8tz`,
`failurePolicy: Fail`) injects `TZ=America/New_York` plus
`/etc/localtime`/`/usr/share/zoneinfo` into essentially every pod, and
VolSync's Go scheduler honours the process `TZ` - so a `VOLSYNC_SCHEDULE_*`
hour is a **local** hour, and its actual UTC firing time shifts by exactly one
hour at every US DST transition (e.g. `0 */4 * * *` fires at UTC
`0,4,8,12,16,20` in EDT/summer and `1,5,9,13,17,21` in EST/winter).

kopiur (`kubernetes/components/kopiur/`) receives the identical injected `TZ`
from the same `k8tz` webhook but **its operator resolves its own timezone and
defaults to UTC regardless** - it silently ignores `k8tz` unless
`spec.schedule.timezone` is set on the `SnapshotSchedule`. Before 2026-08-31
that field was unset, so every kopiur cron hour was a literal UTC hour while
every VolSync cron hour was an `America/New_York` local hour - two engines
protecting the same claim, disagreeing about what the hour numbers meant.

The ceph and r2 hour offsets in this component (and in kopiur's own
`ceph/`/`r2/` schedules) are **deliberate**: they exist so the two engines
never fire on the same claim in the same UTC hour. With kopiur on literal UTC,
that stagger held only because `4 mod 4 == 0` in EDT - it would have collided
on all 29 claims that were dual-engine when this was measured on 2026-08-31
(22 today, after Stage 5 retired eight across two waves). kopiur's
`SnapshotSchedule`s now set `spec.schedule.timezone` (via
`KOPIUR_SCHEDULE_TIMEZONE`, defaulting to `America/New_York`) to match
VolSync, so both engines shift together across DST and the stagger holds in
every season. Full measurement, arithmetic, and the resulting hour tables:
`kubernetes/components/kopiur/Readme.md` "Timezone: kopiur vs VolSync".

**When changing any `VOLSYNC_SCHEDULE_*` or `KOPIUR_SCHEDULE_*` value, treat
the written hour as `America/New_York` local time for both engines**, and
re-check that the two engines' hours still differ in both DST seasons before
committing - not just in whichever season it is today.

## Backup Schedules & Execution

Hours below are **America/New_York local** (see [Timezone: VolSync vs kopiur](#timezone-volsync-vs-kopiur) above) - not UTC.

### 1. Local Ceph (`ceph/`)
- **Schedule**: `0 */4 * * *` (Every 4 hours at minute 0)
- **Frequency**: **EVERY 4 HOURS** (local 00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
- **Process**:
  - Takes snapshot of `${APP}` PVC
  - Uploads to local Ceph S3 bucket
  - Retention: **6 hourly + 14 daily** + 10 weekly + 6 monthly backups (see `ceph/replicationsource.yaml`; reduced from 24/30)
  - Prunes old backups every 14 days

### 2. Remote NAS MinIO (`minio/`)
- **Schedule**: `30 */6 * * *` (Every 6 hours at minute 30)
- **Frequency**: **EVERY 6 HOURS** (local 00:30, 06:30, 12:30, 18:30)
- **Process**:
  - Takes snapshot of `${APP}` PVC
  - Uploads to MinIO S3 bucket at `s3://bucket/path/${APP}/`
  - Retention: 14 daily + 8 weekly + 6 monthly backups
  - Prunes old backups every 14 days

### 3. Cloudflare R2 (`r2/`)
- **Schedule**: `0 2 * * *` (Daily at 2:00 AM local)
- **Frequency**: **DAILY at local 02:00**
- **Process**:
  - Takes snapshot of `${APP}` PVC
  - Uploads to Cloudflare R2 bucket
  - Retention: 30 daily + 12 weekly + 12 monthly backups
  - Prunes old backups every 30 days

## Cron Schedule Configuration

Volsync uses standard cron expressions for scheduling backups. Here's how to create and customize schedule times:

### Cron Format
```
* * * * *
│ │ │ │ │
│ │ │ │ └── Day of week (0-7, where 0 and 7 are Sunday)
│ │ │ └──── Month (1-12)
│ │ └────── Day of month (1-31)
│ └──────── Hour (0-23)
└────────── Minute (0-59)
```

### Common Schedule Examples

| Schedule | Cron Expression | Description |
|----------|----------------|-------------|
| Every minute | `* * * * *` | Testing only (not recommended) |
| Every 5 minutes | `*/5 * * * *` | Every 5 minutes |
| Every 30 minutes | `*/30 * * * *` | Every 30 minutes |
| Every hour | `0 * * * *` | At minute 0 of every hour |
| Every 2 hours | `0 */2 * * *` | At minute 0 of every 2nd hour |
| Every 3 hours | `0 */3 * * *` | At 00:00, 03:00, 06:00, etc. |
| Every 4 hours | `0 */4 * * *` | At 00:00, 04:00, 08:00, etc. |
| Every 6 hours | `0 */6 * * *` | At 00:00, 06:00, 12:00, 18:00 |
| Daily at 2 AM | `0 2 * * *` | Every day at 02:00 |
| Daily at 2:30 AM | `30 2 * * *` | Every day at 02:30 |
| Weekly (Sunday 3 AM) | `0 3 * * 0` | Every Sunday at 03:00 |
| Monthly (1st at 4 AM) | `0 4 1 * *` | 1st of every month at 04:00 |

### Recommended Schedules by Application Type

#### Critical Applications (Databases, Config)
```yaml
# Frequent local + regular off-site
schedule: "0 */4 * * *"   # Local Ceph (every 4 hours)
schedule: "0 */6 * * *"   # NAS MinIO (every 6 hours)
schedule: "0 1 * * *"     # Cloud R2 (daily at 1 AM)
```

#### Media Applications (Plex, Tdarr)
```yaml
# Every 4 hours local + 6 hour NAS + daily off-site
schedule: "55 */4 * * *"  # Local Ceph (staggered)
schedule: "30 */6 * * *"  # NAS MinIO (staggered)
schedule: "0 4 * * *"     # Cloud R2 (daily at 4 AM)
```

#### Development/Testing Applications
```yaml
# Daily only
schedule: "0 5 * * *"    # Once daily at 5 AM
```

### Schedule Optimization Tips

1. **Stagger backup times** to avoid resource contention:
   ```yaml
   # App 1: Local Ceph
   schedule: "0 */4 * * *"    # Every 4 hours at minute 0

   # App 2: Local Ceph (staggered)
   schedule: "15 */4 * * *"   # Every 4 hours at minute 15

   # App 1: NAS MinIO
   schedule: "0 */6 * * *"    # Every 6 hours at minute 0

   # App 1: Cloudflare R2
   schedule: "0 2 * * *"      # Daily at 02:00
   ```

2. **Consider backup windows** based on usage patterns:
   ```yaml
   # Business hours app - backup after hours
   schedule: "0 22 * * *"       # 10 PM daily

   # Personal media - backup during low usage
   schedule: "0 4 * * *"        # 4 AM daily
   ```

3. **Use online cron generators** for complex schedules:
   - [Crontab.guru](https://crontab.guru/)
   - Test your expressions before deployment

### Testing Schedule Changes

```bash
# Manually trigger a backup (VolSync has no CronJob named volsync-${APP}-ceph;
# the trigger lives on the ReplicationSource)
kubectl patch replicationsource ${APP}-ceph --type merge \
  -p "{\"spec\":{\"trigger\":{\"manual\":\"test-$(date +%s)\"}}}"

# Check if the schedule is valid
kubectl get replicationsource ${APP}-ceph -o yaml | grep schedule
```

## Restore Operations

### `${APP}-dst` (create-once populator source)

```yaml
trigger:
  manual: restore-once
```

**Behavior**:
- Runs **exactly once**, when the object is first created (the `restore-once` trigger fires on
  admit). It does **not** re-run on a schedule, and Flux will not re-run it later -
  `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent` freezes the object after first apply.
- That single run writes into the destination's own temp PVC and publishes
  `status.latestImage` (a VolumeSnapshot). It does not populate the app claim by itself.
- `pvc.yaml` then creates `${VOLSYNC_CLAIM:-${APP}}` with
  `dataSourceRef -> ReplicationDestination/${APP}-dst`, so the volume populator clones
  **`status.latestImage`**, not the restic repository. If the first run found an empty repo
  (`No eligible snapshots found` / `No data will be restored`), `latestImage` is a snapshot of
  an empty volume and stays that way forever - deleting only the PVC and letting Flux recreate
  it restores nothing while PVC `Bound` / app `Running` / Flux `Ready` all report success.
  Always read `status.latestMoverStatus.logs` and `status.lastSyncTime` before trusting the
  populator; to re-fire restore against a now-populated repo, delete the
  `ReplicationDestination` *together with* the PVC so Flux recreates both. Full procedure:
  `docs/backups/corrupt-claim-recreation-runbook.md` (measured on `ai/opencode` 2026-08-31).
- A VolSync restore is **not mode-faithful**: the mover stages the destination writable, so
  kubelet's recursive `fsGroup` walk relaxes every mode by one group-write bit before restic
  writes (`644→664`, `600→660`, …). Ownership is unaffected; kopiur restores preserve modes.
  Details: the VolSync mode-relaxation entry in `AGENTS.md` and the same runbook.

### Manual Restore Process

**Never patch `${APP}-dst` directly.** It carries `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`,
so Flux creates it once and never reconciles it again - a hand-set trigger persists forever and
can silently drift `spec.restic.repository` away from what Git declares (see the `<app>-dst` SSA
drift trap in `AGENTS.md`).

- **Real disaster recovery (restore into the app's live claim)**: `just kube restore <namespace>
  <app>` (recipe in `kubernetes/mod.just`). It derives a new, uniquely-named `${APP}-manual`
  object from `${APP}-dst` via `kubectl apply --server-side` - never a patch of `${APP}-dst`
  itself - scales the app down first, waits for the mover `Job`, then scales back up.
- **Verifying a restore without touching live data**: follow
  `docs/backups/restore-drill-2026-08-23.md` - it restores into a scratch PVC via a
  drill-specific `ReplicationDestination`, never `${APP}-dst`.
- **Replacing a corrupt claim** (live mount works, snapshot clones fail fsck): follow
  `docs/backups/corrupt-claim-recreation-runbook.md`. Do not delete only the PVC.

## Daily Timeline Example

With per-app schedules (20 Flux Kustomizations include this component; 2 more protect a
second claim through `path: ./kubernetes/components/volsync/backup`).
Times are America/New_York local (see [Timezone: VolSync vs kopiur](#timezone-volsync-vs-kopiur)):

```
00:00 ──── [Ceph] 4-hour backup (apps at :00) ──── [MinIO] 6-hour backup (apps at :00) ─────
01:00 ──── [R2] Daily backup window begins (apps spread 01:00-05:00) ───────────────────────
02:00 ──── [R2] Daily backups continue ─────────────────────────────────────────────────────
03:00 ──── [R2] Daily backups continue ─────────────────────────────────────────────────────
04:00 ──── [Ceph] 4-hour backup (apps at :00-:55) ───────────────────────────────────────────
05:00 ──── [R2] Daily backup window ends ───────────────────────────────────────────────────
06:00 ──── [MinIO] 6-hour backup (apps at :00-:45) ─────────────────────────────────────────
08:00 ──── [Ceph] 4-hour backup (apps at :00-:55) ───────────────────────────────────────────
12:00 ──── [Ceph] 4-hour backup ──── [MinIO] 6-hour backup ─────────────────────────────────
16:00 ──── [Ceph] 4-hour backup ─────────────────────────────────────────────────────────────
18:00 ──── [MinIO] 6-hour backup ───────────────────────────────────────────────────────────
20:00 ──── [Ceph] 4-hour backup ─────────────────────────────────────────────────────────────
```

## Per-Application Usage

When you include these components in an application (e.g., `plex`):

```yaml
# In the app's Flux Kustomization (apps/main/<ns>/<app>.yaml)
components:
  - ../../../../../components/volsync
```

**What gets created**:
- `plex-ceph` ReplicationSource (every 4 hours to local Ceph)
- `plex-minio` ReplicationSource (every 6 hours to NAS MinIO)
- `plex-r2` ReplicationSource (daily to Cloudflare R2)
- `plex-dst` ReplicationDestination (manual restore - uses Ceph by default)

## Storage Locations

1. **Ceph**: `s3://ceph-bucket/path/plex/` (local Ceph cluster)
2. **MinIO**: `s3://bucket/path/plex/` (NAS MinIO)
3. **Cloudflare R2**: `s3://r2-bucket/path/plex/` (cloud storage)

## Configuration Variables

Common variables used across all configurations:

| Variable | Default | Description | Recommended |
|----------|---------|-------------|-------------|
| `VOLSYNC_COPYMETHOD` | `Snapshot` | How data is copied (Snapshot/Clone) | `Snapshot` |
| `VOLSYNC_SNAPSHOTCLASS` | `csi-ceph-blockpool` | Volume snapshot class | `csi-ceph-blockpool` |
| `VOLSYNC_STORAGECLASS` | `ceph-block` | Storage class for volumes | `ceph-block` |
| `VOLSYNC_CACHE_CAPACITY` | `2Gi` on sources, `1Gi` on the destination | Cache volume size | **50% of PVC size**; dest default is smaller than sources |
| `VOLSYNC_CACHE_SNAPSHOTCLASS` | `ceph-block` | StorageClass for the cache PVC (name says SnapshotClass, value is a StorageClass) | Set this separately from `VOLSYNC_STORAGECLASS` |
| `VOLSYNC_CAPACITY` | `5Gi` | Restored volume size | **Same as source PVC** |
| `VOLSYNC_PUID` | `1000` | User ID for mover security context | `1000` |
| `VOLSYNC_PGID` | `1000` | Group ID for mover security context | `1000` |
| `VOLSYNC_SCHEDULE_CEPH` | `0 */4 * * *` | Ceph backup schedule (cron) | Stagger per-app |
| `VOLSYNC_SCHEDULE_MINIO` | `30 */6 * * *` | MinIO backup schedule (cron) | Stagger per-app |
| `VOLSYNC_SCHEDULE_R2` | `0 2 * * *` | R2 backup schedule (cron) | Stagger per-app |

### Customizing Backup Schedules Per-App

To spread backup times and reduce IOPS contention, override schedules in the app's Flux Kustomization:

```yaml
# kubernetes/apps/main/media/plex.yaml
postBuild:
  substituteFrom:
    - name: cluster-secrets
      kind: Secret
  substitute:
    APP: *app
    VOLSYNC_CAPACITY: 10Gi
    VOLSYNC_SCHEDULE_CEPH: "15 */4 * * *"    # Offset by 15 minutes
    VOLSYNC_SCHEDULE_MINIO: "45 */6 * * *"   # Offset by 15 minutes
    VOLSYNC_SCHEDULE_R2: "15 2 * * *"        # Offset by 15 minutes
```

You can override one, two, or all three schedules independently. Apps without overrides use the defaults.

### Cache Sizing Considerations

The cache size should be appropriately sized relative to your PVC capacity:

```yaml
# Potentially problematic
cacheCapacity: "2Gi"    # Cache
capacity: "100Gi"       # PVC - Cache is only 2% of PVC size

# Better sizing
cacheCapacity: "20Gi"   # Cache
capacity: "100Gi"       # PVC - Cache is 20% of PVC size

# Optimal for small PVCs
cacheCapacity: "4Gi"    # Cache
capacity: "5Gi"         # PVC - Cache is 80% of PVC size
```

**Guidelines:**
- **Small PVCs (< 10Gi)**: Cache should be 50-100% of PVC size
- **Medium PVCs (10-50Gi)**: Cache should be 20-50% of PVC size
- **Large PVCs (> 50Gi)**: Cache should be 10-20% of PVC size
- **Minimum cache**: Never less than 1Gi for any backup operation

## Important Considerations

### Resource Usage
- **Backup frequency**: 6 Ceph + 4 MinIO + 1 R2 = **11 backup operations per day** per app
- **Storage usage**: 3 different destinations = **3x storage consumption**
- **Network traffic**: Distributed schedules reduce peak bandwidth usage

### Security
- **Credentials**: Stored in Kubernetes secrets via External Secrets Operator
- **Repository encryption**: Restic provides client-side encryption

### Reliability
- **Triple redundancy**: Data backed up to 3 different locations
- **Snapshot-based**: Uses Ceph snapshots for consistency
- **Retention policies**: Automatic cleanup of old backups

## Performance Optimization

Consider reducing backup frequency for less critical applications:

```yaml
# Suggested optimized schedules:
# Local Ceph: Every 4 hours (default)
schedule: "0 */4 * * *"

# NAS MinIO: Every 6 hours (default)
schedule: "30 */6 * * *"

# Cloudflare R2: Keep daily (default)
schedule: "0 2 * * *"
```

## Application Schedule Distribution

**22** Flux Kustomizations protect a claim with this component - **20** listing it under
`components:` (the table below) plus `selfhosted/paperless-ngx-media` and
`selfhosted/syncthing-data` via `path: ./kubernetes/components/volsync/backup`, which is why the
table has 20 rows and not 22. That is **66** `ReplicationSource`s, down from 30/90 before Stage 5,
26/78 after the 2026-09-01 pilot (`ai/repo-wiki`, `downloads/recyclarr`, `downloads/sabnzbd` and
`media/seerr`), then 22/66 after the 2026-09-02 wave two (`downloads/prowlarr`, `selfhosted/ntfy`,
`downloads/autobrr` and `selfhosted/obsidian-livesync`); all eight rows were removed here and the
rest renumbered. They are kopiur-only now, and their old restic repositories were **not** deleted.
Schedules are staggered but **not unique** (several apps share the same minute).
Do not assume a 2-3 app cap on simultaneous Ceph backups. Regenerated from
`rg 'components/volsync' kubernetes/apps` plus each overlay yaml's `VOLSYNC_SCHEDULE_*`
(paperless-ngx uses component defaults). `litellm` is not a VolSync client
(Postgres-backed governance layer, no app PVC - `docs/ai-system/litellm/README.md`).
`open-webui`, `qdrant`, `open-notebook` and `perplexica` were retired
2026-08-22, see `docs/ai-system/retired-2026-08-22.md`. `immich` was retired
2026-08-30 (never initialized - 0 users, 0 assets), and `jellyfin` the same
day (the captain uses Plex; 0 playback in 24h of logs). `rsshub` dropped this
component 2026-08-30: its HelmRelease declares no `persistence:` at all (it is
stateless and caches to Dragonfly), so the only reason a `rsshub` claim existed
was `pvc.yaml` below creating one unconditionally. It measured 0 B at the block
level after 222 days and no pod ever mounted it. **Do not re-add the component
to an app that declares no persistence** - it manufactures a claim and three
mover jobs that protect nothing. `cross-seed`, `qbittorrent`
and `calibre-web` were removed as dead, unreferenced app directories - see
`docs/media-stack.md`. `rsshub-playwright` dropped VolSync when its `/config`
mount became an `emptyDir` (stateless browserless; captain decision 2026-08-30).

| # | Namespace | Application | Ceph Schedule | MinIO Schedule | R2 Schedule | Priority |
|---|-----------|-------------|---------------|----------------|-------------|----------|
| 1 | database | pgadmin | `0 */4 * * *` | `0 */6 * * *` | `0 1 * * *` | Critical |
| 2 | home-automation | home-assistant | `5 */4 * * *` | `0 */6 * * *` | `5 1 * * *` | Critical |
| 3 | home-automation | zigbee2mqtt | `10 */4 * * *` | `15 */6 * * *` | `10 1 * * *` | Critical |
| 4 | home-automation | esphome | `15 */4 * * *` | `15 */6 * * *` | `15 1 * * *` | High |
| 5 | home-automation | matter-server | `20 */4 * * *` | `15 */6 * * *` | `20 1 * * *` | High |
| 6 | ai | hermes | `35 */4 * * *` | `35 */6 * * *` | `10 2 * * *` | Medium |
| 7 | ai | opencode | `45 */4 * * *` | `45 */6 * * *` | `30 2 * * *` | Medium |
| 8 | downloads | sonarr | `0 */4 * * *` | `45 */6 * * *` | `0 3 * * *` | Medium |
| 9 | downloads | radarr | `5 */4 * * *` | `45 */6 * * *` | `5 3 * * *` | Medium |
| 10 | downloads | lidarr | `10 */4 * * *` | `45 */6 * * *` | `10 3 * * *` | Medium |
| 11 | downloads | readarr | `15 */4 * * *` | `0 */6 * * *` | `15 3 * * *` | Medium |
| 12 | downloads | bazarr | `20 */4 * * *` | `0 */6 * * *` | `20 3 * * *` | Medium |
| 13 | media | calibre | `0 */4 * * *` | `45 */6 * * *` | `5 4 * * *` | Low |
| 14 | media | plex | `35 */4 * * *` | `50 */6 * * *` | `35 3 * * *` | High |
| 15 | media | tdarr | `50 */4 * * *` | `20 */6 * * *` | `50 3 * * *` | Medium |
| 16 | selfhosted | paperless-ngx | `0 */4 * * *` | `30 */6 * * *` | `0 2 * * *` | High |
| 17 | selfhosted | n8n | `10 */4 * * *` | `45 */6 * * *` | `15 4 * * *` | Medium |
| 18 | selfhosted | syncthing | `10 */4 * * *` | `45 */6 * * *` | `15 4 * * *` | Medium |
| 19 | selfhosted | linkwarden | `10 */4 * * *` | `45 */6 * * *` | `15 4 * * *` | Medium |
| 20 | selfhosted | changedetection | `15 */4 * * *` | `0 */6 * * *` | `20 4 * * *` | Low |

### Distribution Strategy

- **Ceph (every 4 hours)**: 5-minute slots from :00 to :55, but several apps share a slot (e.g. `:00` and `:10`)
- **MinIO (every 6 hours)**: mostly :00/:15/:30/:45, with extra :20/:35/:50/:55 offsets
- **R2 (daily)**: spread from 01:00 to 04:50, not a hard 5-hour unique grid

Regenerate this table from git before trusting a "max N simultaneous" claim.

## Troubleshooting

### Check Backup Status
```bash
# Check ReplicationSource status
kubectl get replicationsource ${APP}-ceph -o yaml

# Check recent backup jobs
kubectl get jobs -l volsync.backube/replication-source=${APP}-ceph

# Check logs
kubectl logs -l volsync.backube/replication-source=${APP}-ceph
```

### Verify Repository Access
```bash
# Check secret contents
kubectl get secret ${APP}-volsync-ceph-secret -o yaml
kubectl get secret ${APP}-volsync-minio-secret -o yaml
kubectl get secret ${APP}-volsync-r2-secret -o yaml
```

### Common Issues
1. **Snapshot class not found**: Ensure `csi-ceph-blockpool` VolumeSnapshotClass exists
2. **Storage class missing**: Verify `ceph-block` StorageClass is available
3. **Permission errors**: Check `runAsUser`/`runAsGroup` settings match PVC requirements
4. **Network issues**: Verify connectivity to backup destinations
5. **Stuck mid-sync looking healthy**: `Synchronizing=True`/`SyncInProgress` is
   indistinguishable from a legitimate mid-sync. `VolSyncVolumeOutOfSync` does not
   cover a mover that never finishes; alert owner is
   `kubernetes/apps/base/system/volsync/app/prometheusrule.yaml`
   (`VolSyncSyncStalled{Ceph,Minio,R2}` - no completed sync in 1.5x that destination's
   schedule, joined to a live cache PVC so deleted-RS metric leaks do not page).

## References

- [Volsync Documentation](https://volsync.readthedocs.io/)
- [Restic Documentation](https://restic.readthedocs.io/)
- [Rook Ceph Snapshots](https://rook.io/docs/rook/latest/Storage-Configuration/Ceph-CSI/ceph-csi-snapshot/)
- [Cron Expression Generator](https://crontab.guru/)
