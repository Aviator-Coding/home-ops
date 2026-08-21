# Ceph Backup and Recovery Strategy

This document describes the **live** metadata backup, not a proposed
CronJob. The implementation is
[`kubernetes/apps/rook-ceph/rook-ceph/backup/backup-system.yaml`](../../kubernetes/apps/rook-ceph/rook-ceph/backup/backup-system.yaml),
wired into the cluster Kustomization as `../backup`. Emergency steps live
in
[`kubernetes/apps/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md`](../../kubernetes/apps/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md).

## Problem this backup exists to solve

Rook-Ceph failures here have destroyed clusters when monitor keyrings and
the cluster FSID were not recoverable. The CronJob snapshots those
Kubernetes objects daily so a **still-running cluster** can restore the
original FSID after the CephCluster object or mon secrets were deleted.

It does **not** snapshot mon host-path directories (`/var/lib/rook/rook-ceph/`
tarballs are never written). Restore paths that extract
`mon-$NODE-YYYYMMDD.tar.gz` are fiction and will fail.

## This is NOT a disaster-recovery backup of last resort

PVC `ceph-backup-pvc` is 50Gi `openebs-hostpath` in namespace `rook-ceph`.
It lives on the same cluster as the OSDs. Complete cluster loss, node
rebuild, PVC delete, or a host wipe (`task rook:wipe-talos-N` /
`task rook:reset-all-data` / `task rook:wipe-all-with-progress`) destroys
the only copy. After that wipe there is nothing to restore. Off-cluster
immutable copies (NAS MinIO / R2) of these YAML/FSID files were never
implemented.

If the cluster and its nodes are gone, OSD data is unrecoverable and a
new FSID is required. All-mon-store loss also cannot be restored from an
old backup; see `docs/ceph-cluster-changelog.md`. Copy a dated backup
directory off-cluster **before** any wipe if you still need the original
FSID.

## What the CronJob writes

| Item | On disk under `/backup/<date>/` |
|------|----------------------------------|
| Secret `rook-ceph-mon` | `rook-ceph-mon.yaml` |
| Secret `rook-ceph-admin-keyring` | `rook-ceph-admin-keyring.yaml` |
| `CephCluster` list YAML | `cephcluster.yaml` |
| ConfigMap `rook-ceph-mon-endpoints` | `mon-endpoints.yaml` |
| FSID text | `cluster-fsid.txt` |
| Status ConfigMap | applied with label `backup-type=ceph-metadata` |

Schedule: `0 3 * * *` (daily 03:00, offset from R2 at 02:00). Retention:
7 dated directories + 7 metadata ConfigMaps. Image: `alpine/k8s:1.36.2`
(has `kubectl`). ServiceAccount `rook-ceph-backup` with a Role limited to
secrets/configmaps/pods/cephclusters in `rook-ceph`.

There is no `backup-pod` Deployment.

## Inspecting backups

```bash
kubectl get cronjob,pvc -n rook-ceph rook-ceph-backup ceph-backup-pvc
kubectl get cm -n rook-ceph -l backup-type=ceph-metadata --sort-by=.metadata.creationTimestamp
```

Mount the PVC in a debug pod to read files (RWO: no concurrent backup Job).
Do not treat `kubectl create job --from=cronjob/rook-ceph-backup` as a
reader; that Job only writes another dated dump. Exact reader YAML is in
`RECOVERY-PROCEDURES.md`.

## Restore

Follow `RECOVERY-PROCEDURES.md`:

1. **Mons still answer, auth looks wrong:** restart the operator only.
   Never `kubectl delete pods -n rook-ceph -l app=rook-ceph-osd`. That
   contradicts [`osd-device-path-recovery.md`](./osd-device-path-recovery.md)
   (Rook #17224).
2. **Mon DB / k8s objects lost, backup PVC still present:** copy the dated
   directory off-cluster, restore the two secrets, recreate the
   CephCluster via Flux. Do not extract tarballs; they are not produced.
3. **Complete loss / host wipe:** new FSID, OSD data gone. The in-cluster
   backup is already destroyed by the wipe.

## Monitoring

Do not add the sample `CephMonitorDown` / `CephAuthenticationError` alerts
that used to live in this file. They were wrong (`up{job="rook-ceph-mgr"}`
is the mgr, not mons; `ceph_monitor_election_call_total` is not an auth
signal).

Live rules:

- Custom: [`cluster/prometheusrules.yaml`](../../kubernetes/apps/rook-ceph/rook-ceph/cluster/prometheusrules.yaml)
  (`CephOSDDown`, quorum, PG, disk prediction, and related).
- Chart rules: `monitoring.createPrometheusRules: true` on the cluster
  HelmRelease.

## Prevention

1. Never run cleanup / wipe tasks without a copy of the backup directory
   **outside** the cluster.
2. Always check the live FSID before changing it.
3. Confirm `task rook:check-osd-device-paths` is green before rebooting a
   node or bouncing an OSD.

## Gaps (not implemented)

- Off-cluster (R2 / NAS MinIO) copies of the YAML/FSID files
- Mon store snapshots from `/var/lib/rook/` (this cluster's mons use
  `openebs-hostpath` PVCs; a host-path tar is a different design)
- Staging recovery drills

Until those exist, treat this CronJob as an in-cluster convenience for
secret/FSID recovery while the nodes are still up, not as last-resort DR.
