# Volsync Backup Configuration

This document explains the backup and restore strategy implemented using Volsync in the home-ops Kubernetes cluster.

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

## Backup Schedules & Execution

### 1. Local Ceph (`ceph/`)
- **Schedule**: `0 */4 * * *` (Every 4 hours at minute 0)
- **Frequency**: **EVERY 4 HOURS** (00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
- **Process**:
  - Takes snapshot of `${APP}` PVC
  - Uploads to local Ceph S3 bucket
  - Retention: **6 hourly + 14 daily** + 10 weekly + 6 monthly backups (see `ceph/replicationsource.yaml`; reduced from 24/30)
  - Prunes old backups every 14 days

### 2. Remote NAS MinIO (`minio/`)
- **Schedule**: `30 */6 * * *` (Every 6 hours at minute 30)
- **Frequency**: **EVERY 6 HOURS** (00:30, 06:30, 12:30, 18:30)
- **Process**:
  - Takes snapshot of `${APP}` PVC
  - Uploads to MinIO S3 bucket at `s3://bucket/path/${APP}/`
  - Retention: 14 daily + 8 weekly + 6 monthly backups
  - Prunes old backups every 14 days

### 3. Cloudflare R2 (`r2/`)
- **Schedule**: `0 2 * * *` (Daily at 2:00 AM)
- **Frequency**: **DAILY at 02:00**
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

#### Media Applications (Plex, Jellyfin)
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

### All ReplicationDestinations (Manual Only)
```yaml
trigger:
  manual: restore-once
```

**Behavior**:
- **NEVER runs automatically**
- **Only when manually triggered** by changing the trigger value
- Downloads from respective backup repository
- Creates new PVC in your Ceph cluster
- Used for disaster recovery

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

## Daily Timeline Example

With per-app schedules (35 Flux Kustomizations include this component):

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
# kubernetes/apps/main/media/jellyfin.yaml
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

35 Flux Kustomizations include `components/volsync`. Schedules are staggered
but **not unique** (several apps share the same minute). Do not assume a
2-3 app cap on simultaneous Ceph backups. Regenerated from
`rg 'components/volsync' kubernetes/apps` plus each overlay yaml's `VOLSYNC_SCHEDULE_*`
(paperless-ngx uses component defaults). `litellm` is gone, and so are
`open-webui`, `qdrant`, `open-notebook` and `perplexica` - retired
2026-08-22, see `docs/ai-system/retired-2026-08-22.md`. `cross-seed`, `qbittorrent`
and `calibre-web` were removed as dead, unreferenced app directories - see
`docs/media-stack.md`.

| # | Namespace | Application | Ceph Schedule | MinIO Schedule | R2 Schedule | Priority |
|---|-----------|-------------|---------------|----------------|-------------|----------|
| 1 | database | pgadmin | `0 */4 * * *` | `0 */6 * * *` | `0 1 * * *` | Critical |
| 2 | home-automation | home-assistant | `5 */4 * * *` | `0 */6 * * *` | `5 1 * * *` | Critical |
| 3 | home-automation | zigbee2mqtt | `10 */4 * * *` | `15 */6 * * *` | `10 1 * * *` | Critical |
| 4 | home-automation | esphome | `15 */4 * * *` | `15 */6 * * *` | `15 1 * * *` | High |
| 5 | home-automation | matter-server | `20 */4 * * *` | `15 */6 * * *` | `20 1 * * *` | High |
| 6 | ai | hermes | `35 */4 * * *` | `35 */6 * * *` | `10 2 * * *` | Medium |
| 7 | ai | agentmemory | `20 */4 * * *` | `20 */6 * * *` | `40 2 * * *` | Medium |
| 8 | downloads | sonarr | `0 */4 * * *` | `45 */6 * * *` | `0 3 * * *` | Medium |
| 9 | downloads | radarr | `5 */4 * * *` | `45 */6 * * *` | `5 3 * * *` | Medium |
| 10 | downloads | lidarr | `10 */4 * * *` | `45 */6 * * *` | `10 3 * * *` | Medium |
| 11 | downloads | readarr | `15 */4 * * *` | `0 */6 * * *` | `15 3 * * *` | Medium |
| 12 | downloads | bazarr | `20 */4 * * *` | `0 */6 * * *` | `20 3 * * *` | Medium |
| 13 | downloads | prowlarr | `25 */4 * * *` | `0 */6 * * *` | `25 3 * * *` | Medium |
| 14 | downloads | sabnzbd | `30 */4 * * *` | `15 */6 * * *` | `30 3 * * *` | Medium |
| 15 | downloads | autobrr | `45 */4 * * *` | `30 */6 * * *` | `45 3 * * *` | Low |
| 16 | downloads | recyclarr | `50 */4 * * *` | `30 */6 * * *` | `50 3 * * *` | Low |
| 17 | media | jellyfin | `55 */4 * * *` | `30 */6 * * *` | `0 4 * * *` | Medium |
| 18 | media | calibre | `0 */4 * * *` | `45 */6 * * *` | `5 4 * * *` | Low |
| 19 | media | plex | `35 */4 * * *` | `50 */6 * * *` | `35 3 * * *` | High |
| 20 | media | seerr | `40 */4 * * *` | `55 */6 * * *` | `40 3 * * *` | Medium |
| 21 | media | tdarr | `50 */4 * * *` | `20 */6 * * *` | `50 3 * * *` | Medium |
| 22 | media | immich | `15 */4 * * *` | `45 */6 * * *` | `30 4 * * *` | High |
| 23 | selfhosted | paperless-ngx | `0 */4 * * *` | `30 */6 * * *` | `0 2 * * *` | High |
| 24 | selfhosted | n8n | `10 */4 * * *` | `45 */6 * * *` | `15 4 * * *` | Medium |
| 25 | selfhosted | syncthing | `10 */4 * * *` | `45 */6 * * *` | `15 4 * * *` | Medium |
| 26 | selfhosted | obsidian-livesync | `10 */4 * * *` | `45 */6 * * *` | `15 4 * * *` | Medium |
| 27 | selfhosted | linkwarden | `10 */4 * * *` | `45 */6 * * *` | `15 4 * * *` | Medium |
| 28 | selfhosted | changedetection | `15 */4 * * *` | `0 */6 * * *` | `20 4 * * *` | Low |
| 29 | selfhosted | ntfy | `20 */4 * * *` | `50 */6 * * *` | `25 4 * * *` | Medium |
| 30 | selfhosted | rsshub | `20 */4 * * *` | `0 */6 * * *` | `25 4 * * *` | Low |
| 31 | selfhosted | rsshub-playwright | `25 */4 * * *` | `0 */6 * * *` | `30 4 * * *` | Low |

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

## References

- [Volsync Documentation](https://volsync.readthedocs.io/)
- [Restic Documentation](https://restic.readthedocs.io/)
- [Rook Ceph Snapshots](https://rook.io/docs/rook/latest/Storage-Configuration/Ceph-CSI/ceph-csi-snapshot/)
- [Cron Expression Generator](https://crontab.guru/)
