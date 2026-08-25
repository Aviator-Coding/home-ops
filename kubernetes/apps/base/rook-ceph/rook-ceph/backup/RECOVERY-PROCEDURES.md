# Rook-Ceph Recovery Procedures

These steps match the live backup in
[`backup-system.yaml`](./backup-system.yaml) and the device-path runbook
[`docs/ceph/osd-device-path-recovery.md`](../../../../../../docs/ceph/osd-device-path-recovery.md).
Do not invent missing objects. There is no `backup-pod` Deployment, no
`cleanup-all-nodes.yaml`, and no `initial-backup-job.yaml`.

Toolbox is a Deployment (`toolbox.enabled: true` in the cluster HelmRelease).
Always exec that, never a guessed pod name:

```bash
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph status
```

---

## What the in-cluster backup actually is

CronJob `rook-ceph-backup` (`schedule: "0 3 * * *"`) writes dated directories
onto PVC `ceph-backup-pvc` (50Gi, StorageClass `openebs-hostpath`, namespace
`rook-ceph`). Each run dumps:

- Secret `rook-ceph-mon`
- Secret `rook-ceph-admin-keyring`
- `CephCluster` YAML
- ConfigMap `rook-ceph-mon-endpoints`
- `cluster-fsid.txt` (from the mon secret)
- a ConfigMap labeled `backup-type=ceph-metadata`

It does **not** tar `/var/lib/rook/rook-ceph/` from mon pods. Those tarballs
do not exist and cannot be restored.

List what exists:

```bash
kubectl get cronjob,pvc -n rook-ceph rook-ceph-backup ceph-backup-pvc
kubectl get cm -n rook-ceph -l backup-type=ceph-metadata --sort-by=.metadata.creationTimestamp
```

Read the files by mounting the PVC (the CronJob is a writer, not a reader).
The PVC is RWO, so wait until no `rook-ceph-backup-*` Job pod is Running:

```bash
kubectl -n rook-ceph apply -f - <<'EOF'
apiVersion: v1
kind: Pod
metadata:
  name: ceph-backup-reader
  namespace: rook-ceph
spec:
  restartPolicy: Never
  containers:
    - name: reader
      image: busybox:1.37
      command: ["sleep", "3600"]
      volumeMounts:
        - name: backup
          mountPath: /backup
  volumes:
    - name: backup
      persistentVolumeClaim:
        claimName: ceph-backup-pvc
EOF

kubectl -n rook-ceph exec ceph-backup-reader -- ls -la /backup
kubectl -n rook-ceph exec ceph-backup-reader -- find /backup -name cluster-fsid.txt -exec cat {} \;
# Copy a dated dir off the cluster before any wipe:
# kubectl -n rook-ceph cp ceph-backup-reader:/backup/BACKUP_DATE ./ceph-backup-BACKUP_DATE
kubectl -n rook-ceph delete pod ceph-backup-reader
```

Check the live FSID:

```bash
kubectl get secret rook-ceph-mon -n rook-ceph -o jsonpath='{.data.fsid}' | base64 -d; echo
```

### This is NOT a disaster-recovery backup of last resort

`ceph-backup-pvc` lives on the same cluster, on a node's OpenEBS hostpath
volume. Complete cluster loss, node rebuild, PVC delete, or a host wipe
(`task rook:wipe-talos-N`, `task rook:reset-all-data`,
`task rook:wipe-all-with-progress`) destroys that copy along with the OSD
disks. After a host wipe there is nothing left to `kubectl apply`.

Off-cluster copies of these YAML/FSID files were never implemented.
`task rook:backup-rook-config` only dumps Ceph CRs to `/tmp` on the
workstation; it does not save mon secrets or the FSID, and `/tmp` is not
durable off-cluster storage.

If the cluster and its nodes are gone, OSD data is unrecoverable and a
new FSID is required. All-mon-store loss also cannot be restored from an
old backup; see `docs/ceph-cluster-changelog.md` (mon RocksDB store
corruption). Copy the backup directory off-cluster **before** any wipe if
you still need the original FSID.

---

## 1. Authentication looks corrupted, monitors still respond

Do **not** restart all OSD pods. GitOps sets
`cephClusterSpec.storage.osdMaxUpdatesInParallel: 1` so operator-driven
OSD rolls stay serial. Deleting every OSD at once
(`kubectl delete pods -n rook-ceph -l app=rook-ceph-osd`) is the blast
radius that runbook forbids: on a degraded cluster Rook #17224 relocate
fallback returns empty and pods stick `Init:0/5`. See
[`docs/ceph/osd-device-path-recovery.md`](../../../../../../docs/ceph/osd-device-path-recovery.md).

```bash
# 1. Confirm mons answer
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph status

# 2. Inspect auth
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph auth list

# 3. Restart the operator only
kubectl delete pod -n rook-ceph -l app=rook-ceph-operator

# 4. If a single OSD is still stuck, audit paths first
task rook:check-osd-device-paths
```

If `check-osd-device-paths` is green and one OSD still looks auth-stuck,
bounce **that one** OSD. If paths are stale, use Case A in the device-path
runbook (patch one `ROOK_BLOCK_PATH`, bounce one pod). Never bounce all
six.

---

## 2. Monitor database corrupted (Kubernetes objects and the backup PVC still exist)

Stop. Do not wipe host data. This path only works while `ceph-backup-pvc`
still exists. If you already wiped nodes, go to section 3 and accept a
new FSID.

```bash
# 1. Confirm the PVC and a dated backup exist (mount the PVC as above)
kubectl get cronjob,pvc -n rook-ceph rook-ceph-backup ceph-backup-pvc
kubectl get cm -n rook-ceph -l backup-type=ceph-metadata --sort-by=.metadata.creationTimestamp

# 2. Copy the backup dir off-cluster, then identify the FSID you need
#    (cluster-fsid.txt in that dated directory)

# 3. Suspend HelmReleases
flux suspend hr rook-ceph-cluster -n rook-ceph
flux suspend hr rook-ceph -n rook-ceph

# 4. Delete the cluster object but keep the restored secrets
kubectl delete cephcluster rook-ceph -n rook-ceph
# If it sticks: kubectl patch cephcluster rook-ceph -n rook-ceph --type=merge -p '{"metadata":{"finalizers":null}}'

# 5. Restore secrets from the copied YAML (not a path inside a missing pod)
kubectl delete secret rook-ceph-mon rook-ceph-admin-keyring -n rook-ceph --ignore-not-found
kubectl apply -f ./ceph-backup-BACKUP_DATE/rook-ceph-mon.yaml
kubectl apply -f ./ceph-backup-BACKUP_DATE/rook-ceph-admin-keyring.yaml
# If apply rejects resourceVersion/uid, strip those fields and retry.

# 6. Resume operator first, then the cluster
flux resume hr rook-ceph -n rook-ceph
# wait until the operator is Ready
flux resume hr rook-ceph-cluster -n rook-ceph
```

Do not restart all OSDs afterward. Let the operator bring them up one at
a time. If an OSD sticks in `Init`, use the device-path runbook Case A.

---

## 3. Complete cluster loss (last resort: new FSID, OSD data gone)

Use this only when there is no usable backup **or** host data has already
been wiped (which also destroyed the in-cluster backup). This creates a
**new** cluster. Original OSD contents cannot be recovered without the
original FSID and the original BlueStore data still on disk. Wiping disks
throws that data away.

Copy anything still on `ceph-backup-pvc` off-cluster **before** the wipe.
Once hostpath volumes are gone, the metadata backup is gone too.

```bash
# 1. Accept data loss and a new FSID
flux suspend hr rook-ceph-cluster -n rook-ceph
flux suspend hr rook-ceph -n rook-ceph

# 2. Delete the CephCluster
kubectl delete cephcluster rook-ceph -n rook-ceph
# If it sticks:
kubectl patch cephcluster rook-ceph -n rook-ceph --type=merge -p '{"metadata":{"finalizers":null}}'

# 3. Wipe OSD disks and /var/lib/rook via the Taskfile, not a missing YAML.
#    Per node:  task rook:wipe-talos-1  (and talos-2, talos-3)
#    All nodes + host rook dir:
task rook:wipe-all-with-progress
# Interactive prompts: the skip flag is global `task --yes rook:...`, not `--yes` after the task name.

# 4. Resume HelmReleases; Flux recreates an empty ceph-backup-pvc
flux resume hr rook-ceph -n rook-ceph
flux resume hr rook-ceph-cluster -n rook-ceph
```

Do not apply `initial-backup-job.yaml`. The CronJob already exists
(`0 3 * * *`). After the **new** cluster is healthy, optionally snapshot
the new FSID:

```bash
kubectl -n rook-ceph create job --from=cronjob/rook-ceph-backup manual-backup-$(date +%s)
```

That Job writes to the same in-cluster PVC. It is still not an
off-cluster disaster-recovery copy.

---

## Important notes

1. **FSID is critical.** Without the original FSID, OSDs with data cannot
   be recovered.
2. **Monitor secrets contain the FSID.** Those YAML files are the useful
   part of this backup, and only while the PVC still exists.
3. **Never wipe host data until the backup directory is copied off-cluster.**
   The in-cluster copy does not survive the wipe.
4. **Never restart all OSD pods** while the cluster is unhealthy. See
   the #17224 runbook.
5. Test recovery in staging if you have one. This cluster's metadata
   backup is not a last-resort DR copy.

## Prevention checklist

- [ ] Daily CronJob `rook-ceph-backup` is succeeding
- [ ] `ceph-backup-pvc` has space (50Gi, 7-day retention)
- [ ] A recent dated directory has been copied **off-cluster** if you
      care about FSID recovery after a wipe
- [ ] `task rook:check-osd-device-paths` is green before any node reboot
- [ ] This file still matches `backup-system.yaml`
