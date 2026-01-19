# Volsync Backup Configuration

This document explains the backup and restore strategy implemented using Volsync in the home-ops Kubernetes cluster.

## Directory Structure

```
kubernetes/components/volsync/
├── ceph/                    # Local Ceph cluster backups (every 30 min)
├── minio/                   # MinIO S3-compatible storage backups (every 3 hrs)
├── r2/                      # Cloudflare R2 storage backups (daily)
└── kustomization.yaml       # Combines all components
```

## Backup Schedules & Execution

### 1. Local Ceph (`ceph/`)
- **Schedule**: `*/30 * * * *` (Every 30 minutes)
- **Frequency**: **EVERY 30 MINUTES** (00:00, 00:30, 01:00, 01:30, etc.)
- **Process**:
  - Takes snapshot of `${APP}` PVC
  - Uploads to local Ceph S3 bucket
  - Retention: 24 hourly + 30 daily + 10 weekly + 6 monthly backups
  - Prunes old backups every 14 days

### 2. Remote NAS MinIO (`minio/`)
- **Schedule**: `0 */3 * * *` (Every 3 hours at minute 0)
- **Frequency**: **EVERY 3 HOURS** (00:00, 03:00, 06:00, 09:00, etc.)
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
schedule: "*/30 * * * *"  # Local Ceph (every 30 min)
schedule: "0 */3 * * *"   # NAS MinIO (every 3 hours)
schedule: "0 2 * * *"     # Cloud R2 (daily)
```

#### Media Applications (Plex, Jellyfin)
```yaml
# Every 30 min local + 3 hour NAS + daily off-site
schedule: "*/30 * * * *"  # Local Ceph
schedule: "0 */3 * * *"   # NAS MinIO
schedule: "0 2 * * *"     # Cloud R2
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
   schedule: "*/30 * * * *"   # Every 30 minutes

   # App 1: NAS MinIO
   schedule: "0 */3 * * *"    # Every 3 hours starting at 00:00

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

```
00:00 ──── [Ceph] 30-min backup ──── [MinIO] 3-hour backup ─────────────
00:30 ──── [Ceph] 30-min backup ────────────────────────────────────────
01:00 ──── [Ceph] 30-min backup ────────────────────────────────────────
01:30 ──── [Ceph] 30-min backup ────────────────────────────────────────
02:00 ──── [Ceph] 30-min backup ──── [R2] Daily backup ─────────────────
02:30 ──── [Ceph] 30-min backup ────────────────────────────────────────
03:00 ──── [Ceph] 30-min backup ──── [MinIO] 3-hour backup ─────────────
...
06:00 ──── [Ceph] 30-min backup ──── [MinIO] 3-hour backup ─────────────
...
09:00 ──── [Ceph] 30-min backup ──── [MinIO] 3-hour backup ─────────────
...
12:00 ──── [Ceph] 30-min backup ──── [MinIO] 3-hour backup ─────────────
...
```

## Per-Application Usage

When you include these components in an application (e.g., `plex`):

```yaml
# In your app's kustomization.yaml
components:
  - ../../../components/volsync
```

**What gets created**:
- `plex-ceph` ReplicationSource (every 30 minutes to local Ceph)
- `plex-minio` ReplicationSource (every 3 hours to NAS MinIO)
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
- **Backup frequency**: 48 Ceph + 8 MinIO + 1 R2 = **57 backup operations per day** per app
- **Storage usage**: 3 different destinations = **3x storage consumption**
- **Network traffic**: High frequency backups = significant bandwidth usage

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
# Local Ceph: Every 30 minutes (default)
schedule: "*/30 * * * *"

# NAS MinIO: Every 3 hours (default)
schedule: "0 */3 * * *"

# Cloudflare R2: Keep daily (default)
schedule: "0 2 * * *"
```

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
