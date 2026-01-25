# Volsync Backup Configuration

This document explains the backup and restore strategy implemented using Volsync in the home-ops Kubernetes cluster.

## Directory Structure

```
kubernetes/components/volsync/
├── ceph/                    # Local Ceph cluster backups (every 4 hours)
├── minio/                   # MinIO S3-compatible storage backups (every 6 hours)
├── r2/                      # Cloudflare R2 storage backups (daily)
└── kustomization.yaml       # Combines all components
```

## Backup Schedules & Execution

### 1. Local Ceph (`ceph/`)
- **Schedule**: `0 */4 * * *` (Every 4 hours at minute 0)
- **Frequency**: **EVERY 4 HOURS** (00:00, 04:00, 08:00, 12:00, 16:00, 20:00)
- **Process**:
  - Takes snapshot of `${APP}` PVC
  - Uploads to local Ceph S3 bucket
  - Retention: 24 hourly + 30 daily + 10 weekly + 6 monthly backups
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
# Manually trigger a backup to test
kubectl create job test-backup-$(date +%s) \
  --from=cronjob/volsync-${APP}-ceph

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
```bash
# Trigger a restore by updating the trigger value
kubectl patch replicationdestination ${APP}-dst \
  --type merge \
  --patch '{"spec":{"trigger":{"manual":"restore-$(date +%s)"}}}'
```

## Daily Timeline Example

With schedule distribution across 27 applications:

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
# In your app's kustomization.yaml
components:
  - ../../../components/volsync
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
| `VOLSYNC_CACHE_CAPACITY` | `2Gi` | Cache volume size | **50% of PVC size** |
| `VOLSYNC_CAPACITY` | `5Gi` | Restored volume size | **Same as source PVC** |
| `VOLSYNC_PUID` | `1000` | User ID for mover security context | `1000` |
| `VOLSYNC_PGID` | `1000` | Group ID for mover security context | `1000` |
| `VOLSYNC_SCHEDULE_CEPH` | `0 */4 * * *` | Ceph backup schedule (cron) | Stagger per-app |
| `VOLSYNC_SCHEDULE_MINIO` | `30 */6 * * *` | MinIO backup schedule (cron) | Stagger per-app |
| `VOLSYNC_SCHEDULE_R2` | `0 2 * * *` | R2 backup schedule (cron) | Stagger per-app |

### Customizing Backup Schedules Per-App

To spread backup times and reduce IOPS contention, override schedules in your app's `ks.yaml`:

```yaml
# kubernetes/apps/media/jellyfin/ks.yaml
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

To reduce IOPS contention and spread backup operations evenly, all 27 volsync-enabled applications have unique staggered schedules:

| # | Namespace | Application | Ceph Schedule | MinIO Schedule | R2 Schedule | Priority |
|---|-----------|-------------|---------------|----------------|-------------|----------|
| 1 | database | pgadmin | `0 */4 * * *` | `0 */6 * * *` | `0 1 * * *` | Critical |
| 2 | home-automation | home-assistant | `5 */4 * * *` | `0 */6 * * *` | `5 1 * * *` | Critical |
| 3 | home-automation | zigbee2mqtt | `10 */4 * * *` | `15 */6 * * *` | `10 1 * * *` | Critical |
| 4 | home-automation | esphome | `15 */4 * * *` | `15 */6 * * *` | `15 1 * * *` | High |
| 5 | home-automation | matter-server | `20 */4 * * *` | `15 */6 * * *` | `20 1 * * *` | High |
| 6 | ai | open-webui | `25 */4 * * *` | `30 */6 * * *` | `0 2 * * *` | High |
| 7 | ai | qdrant | `30 */4 * * *` | `30 */6 * * *` | `5 2 * * *` | High |
| 8 | ai | litellm | `35 */4 * * *` | `30 */6 * * *` | `10 2 * * *` | Medium |
| 9 | ai | open-notebook | `40 */4 * * *` | `30 */6 * * *` | `15 2 * * *` | Medium |
| 10 | ai | perplexica | `45 */4 * * *` | `45 */6 * * *` | `20 2 * * *` | Medium |
| 11 | downloads | sonarr | `0 */4 * * *` | `45 */6 * * *` | `0 3 * * *` | Medium |
| 12 | downloads | radarr | `5 */4 * * *` | `45 */6 * * *` | `5 3 * * *` | Medium |
| 13 | downloads | lidarr | `10 */4 * * *` | `45 */6 * * *` | `10 3 * * *` | Medium |
| 14 | downloads | readarr | `15 */4 * * *` | `0 */6 * * *` | `15 3 * * *` | Medium |
| 15 | downloads | bazarr | `20 */4 * * *` | `0 */6 * * *` | `20 3 * * *` | Medium |
| 16 | downloads | prowlarr | `25 */4 * * *` | `0 */6 * * *` | `25 3 * * *` | Medium |
| 17 | downloads | sabnzbd | `30 */4 * * *` | `15 */6 * * *` | `30 3 * * *` | Medium |
| 18 | downloads | qbittorrent | `35 */4 * * *` | `15 */6 * * *` | `35 3 * * *` | Medium |
| 19 | downloads | cross-seed | `40 */4 * * *` | `15 */6 * * *` | `40 3 * * *` | Low |
| 20 | downloads | autobrr | `45 */4 * * *` | `30 */6 * * *` | `45 3 * * *` | Low |
| 21 | downloads | recyclarr | `50 */4 * * *` | `30 */6 * * *` | `50 3 * * *` | Low |
| 22 | media | jellyfin | `55 */4 * * *` | `30 */6 * * *` | `0 4 * * *` | Medium |
| 23 | media | calibre | `0 */4 * * *` | `45 */6 * * *` | `5 4 * * *` | Low |
| 24 | media | calibre-web | `5 */4 * * *` | `45 */6 * * *` | `10 4 * * *` | Low |
| 25 | selfhosted | n8n | `10 */4 * * *` | `45 */6 * * *` | `15 4 * * *` | Medium |
| 26 | selfhosted | changedetection | `15 */4 * * *` | `0 */6 * * *` | `20 4 * * *` | Low |
| 27 | selfhosted | rsshub | `20 */4 * * *` | `0 */6 * * *` | `25 4 * * *` | Low |

### Distribution Strategy

- **Ceph (every 4 hours)**: Apps distributed across 12 time slots (5-minute intervals from :00 to :55)
- **MinIO (every 6 hours)**: Apps distributed across 4 time slots (15-minute intervals: :00, :15, :30, :45)
- **R2 (daily)**: Apps distributed across 5 hours (1:00 AM - 5:00 AM) in 5-minute intervals

This distribution ensures:
- Maximum 2-3 apps backing up simultaneously to Ceph
- Maximum 6-7 apps per MinIO window
- Maximum 5-6 apps per R2 hour slot
- Critical applications (databases, home-assistant) run earliest in backup windows

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
