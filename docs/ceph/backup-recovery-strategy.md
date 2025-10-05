# Ceph Backup and Recovery Strategy

## Problem Statement
Rook-Ceph cluster failures have resulted in complete data loss 3 times in 5 weeks due to:
1. Authentication corruption after node reboots
2. No automated backup of monitor keyring and cluster metadata
3. Inability to restore original FSID when monitors are lost
4. Manual cleanup operations that destroy recovery options

## Critical Backup Components

### 1. Monitor Keyring and Cluster Metadata
**Location**: `/var/lib/rook/rook-ceph/`
**Critical Files**:
- `mon-*/keyring` - Monitor authentication keys
- `rook-ceph.config` - Cluster configuration
- Kubernetes secrets: `rook-ceph-mon`, `rook-ceph-admin-keyring`

### 2. Automated Daily Backups

Create a CronJob to backup critical Rook-Ceph metadata:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: rook-ceph-backup
  namespace: rook-ceph
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: backup
            image: busybox
            command:
            - /bin/sh
            - -c
            - |
              # Backup Kubernetes secrets
              kubectl get secret rook-ceph-mon -o yaml > /backup/rook-ceph-mon-$(date +%Y%m%d).yaml
              kubectl get secret rook-ceph-admin-keyring -o yaml > /backup/rook-ceph-admin-keyring-$(date +%Y%m%d).yaml

              # Backup monitor data from each node
              for node in talos-1 talos-2 talos-3; do
                kubectl exec -n rook-ceph $(kubectl get pods -n rook-ceph -l app=rook-ceph-mon --field-selector spec.nodeName=$node -o name | head -1) -- tar czf - /var/lib/rook/rook-ceph/ > /backup/mon-$node-$(date +%Y%m%d).tar.gz
              done

              # Keep only last 7 days
              find /backup -name "*.yaml" -mtime +7 -delete
              find /backup -name "*.tar.gz" -mtime +7 -delete
            volumeMounts:
            - name: backup-storage
              mountPath: /backup
          volumes:
          - name: backup-storage
            persistentVolumeClaim:
              claimName: ceph-backup-pvc
          restartPolicy: OnFailure
```

### 3. Ceph Cluster Health Monitoring

Add comprehensive monitoring to detect issues early:

```yaml
# Prometheus alerts for Ceph cluster health
- alert: CephMonitorDown
  expr: up{job="rook-ceph-mgr"} == 0
  for: 5m
  annotations:
    summary: "Ceph monitor is down"

- alert: CephAuthenticationError
  expr: increase(ceph_monitor_election_call_total[5m]) > 10
  annotations:
    summary: "Ceph authentication issues detected"

- alert: CephOSDDown
  expr: ceph_osd_up == 0
  for: 5m
  annotations:
    summary: "Ceph OSD {{ $labels.ceph_daemon }} is down"
```

### 4. Recovery Procedures

#### Quick Recovery (if monitors are healthy):
1. Restart affected OSD pods
2. Check authentication keys
3. Verify cluster health

#### Full Recovery (if monitors are corrupted):
1. **Stop all deletions immediately**
2. Restore monitor secrets from backup:
   ```bash
   kubectl apply -f /backup/rook-ceph-mon-YYYYMMDD.yaml
   kubectl apply -f /backup/rook-ceph-admin-keyring-YYYYMMDD.yaml
   ```
3. Restore monitor data to nodes:
   ```bash
   # For each node
   kubectl exec -n rook-ceph $CLEANUP_POD -- tar xzf /backup/mon-$NODE-YYYYMMDD.tar.gz -C /var/lib/rook/
   ```
4. Restart rook-ceph-operator
5. Wait for automatic OSD discovery

### 5. Prevention Measures

#### Immediate Actions:
1. **Never run cleanup operations without confirmed backups**
2. **Always check for existing data before FSID changes**
3. **Implement backup verification tests**

#### Long-term Improvements:
1. **External Ceph cluster** - Move critical data to managed Ceph service
2. **Multi-cluster setup** - Replicate critical data across clusters
3. **Immutable backups** - Store backups in object storage outside the cluster

### 6. Emergency Contacts and Procedures

#### Before any major changes:
1. Verify recent backups exist and are valid
2. Document current cluster state
3. Have rollback plan ready
4. Test recovery procedure in staging

#### If cluster fails:
1. **DO NOT DELETE ANYTHING** until backup status is confirmed
2. Check if monitors are still accessible
3. Attempt authentication repair first
4. Only clean host data as last resort with confirmed backups

## Implementation Priority

1. **Immediate (Today)**:
   - Create backup CronJob
   - Set up basic monitoring alerts
   - Document current cluster FSID and secrets

2. **This Week**:
   - Test backup/restore procedure
   - Implement automated health checks
   - Create emergency runbook

3. **This Month**:
   - Evaluate external Ceph options
   - Implement immutable backup storage
   - Set up staging environment for testing

## Cost of Current Approach vs Alternatives

**Current Losses**: 3 complete cluster rebuilds = ~40+ hours of downtime
**Backup Solution Cost**: ~2 hours setup + 1 hour/month maintenance
**Managed Ceph Service**: Higher cost but eliminates cluster management overhead

## Next Steps

1. Let's implement the backup CronJob immediately after cluster recreation
2. Set up monitoring alerts
3. Create a staging environment to test disaster recovery
4. Evaluate managed Ceph services for critical workloads
