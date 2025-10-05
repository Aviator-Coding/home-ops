# Rook-Ceph Recovery Procedures

## Emergency Recovery Steps

### 1. If Cluster Authentication is Corrupted (but monitors are accessible)

```bash
# DO NOT DELETE ANYTHING - Try recovery first

# 1. Check if monitors are accessible
kubectl exec -n rook-ceph rook-ceph-tools-xxx -- ceph status

# 2. If monitors respond but OSDs are down due to auth issues:
kubectl exec -n rook-ceph rook-ceph-tools-xxx -- ceph auth list

# 3. Restart operator to regenerate auth
kubectl delete pod -n rook-ceph -l app=rook-ceph-operator

# 4. Restart all OSD pods
kubectl delete pods -n rook-ceph -l app=rook-ceph-osd
```

### 2. If Monitor Database is Corrupted (CRITICAL RECOVERY)

```bash
# STOP! Do not proceed without backups

# 1. Find latest backup
kubectl get configmaps -n rook-ceph -l backup-type=ceph-metadata --sort-by=.metadata.creationTimestamp

# 2. Access backup data
kubectl exec -it deployment/backup-pod -n rook-ceph -- ls -la /backup/

# 3. Identify backup with correct FSID
# Check each backup folder for cluster-fsid.txt

# 4. If you have a backup with the original FSID:
# Suspend HelmReleases
flux suspend hr rook-ceph-cluster -n rook-ceph
flux suspend hr rook-ceph -n rook-ceph

# 5. Delete cluster but preserve secrets
kubectl delete cephcluster rook-ceph -n rook-ceph
# Wait for deletion, remove finalizers if stuck

# 6. Restore secrets from backup
kubectl delete secret rook-ceph-mon -n rook-ceph
kubectl apply -f /backup/BACKUP_DATE/rook-ceph-mon.yaml

kubectl delete secret rook-ceph-admin-keyring -n rook-ceph
kubectl apply -f /backup/BACKUP_DATE/rook-ceph-admin-keyring.yaml

# 7. Resume HelmReleases
flux resume hr rook-ceph -n rook-ceph
# Wait for operator to be ready
flux resume hr rook-ceph-cluster -n rook-ceph
```

### 3. Complete Cluster Loss (Last Resort)

```bash
# Only if no backups exist or backups are unusable

# 1. Accept data loss and create new cluster
flux suspend hr rook-ceph-cluster -n rook-ceph
flux suspend hr rook-ceph -n rook-ceph

# 2. Clean everything
kubectl delete cephcluster rook-ceph -n rook-ceph
# Remove finalizers if stuck
kubectl patch cephcluster rook-ceph -n rook-ceph --type='merge' -p='{"metadata":{"finalizers":null}}'

# 3. Clean host data
kubectl apply -f cleanup-all-nodes.yaml
# Wait for completion

# 4. Resume HelmReleases
flux resume hr rook-ceph -n rook-ceph
flux resume hr rook-ceph-cluster -n rook-ceph

# 5. Run initial backup immediately
kubectl apply -f initial-backup-job.yaml
```

## Important Notes

1. **FSID is critical** - Without the original FSID, OSDs with data cannot be recovered
2. **Monitor secrets contain FSID** - These are the most critical backup files
3. **Never clean host data without confirmed backups**
4. **Test recovery procedures regularly in staging**

## Backup Verification

```bash
# Check backup status
kubectl get configmaps -n rook-ceph -l backup-type=ceph-metadata

# Verify backup contents
kubectl exec -it deployment/backup-pod -n rook-ceph -- find /backup -name "cluster-fsid.txt" -exec cat {} \;

# Check current cluster FSID
kubectl get secret rook-ceph-mon -n rook-ceph -o jsonpath='{.data.fsid}' | base64 -d
```

## Prevention Checklist

- [ ] Daily backups are running successfully
- [ ] Backup storage has sufficient space
- [ ] Recovery procedures tested in staging
- [ ] Team knows emergency procedures
- [ ] Monitoring alerts are configured
- [ ] Documentation is up to date
