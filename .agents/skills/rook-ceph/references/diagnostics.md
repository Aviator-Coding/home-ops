# Toolbox, slow ops, PG

Always exec the toolbox Deployment. There is no stable pod name.

```bash
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph status
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph health detail
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph config dump
```

`ceph -s` must show `HEALTH_OK` and 6/6 OSDs up before a node reboot or an operator-driven OSD roll. Pair it with `task rook:check-osd-device-paths`. An OSD stuck `Init` after a reboot is the device-path bug (Rook #17224): [osd-device-path-recovery.md](osd-device-path-recovery.md). Store corruption on one OSD: [osd-store-corruption-recovery.md](osd-store-corruption-recovery.md).

## Slow ops and laggy PGs

The read lease is `osd_pool_default_read_lease_ratio` (0.8) times `osd_heartbeat_grace`. A slow OSD that misses the lease flips its PGs to `LAGGY` and blocks reads. That is the current lease behavior (the old `recheck_readable` defect was fixed before Tentacle). Find the OSD, then re-peer it. Do not mark the whole cluster down.

```bash
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph health detail
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph tell osd.N dump_blocked_ops
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- ceph osd down osd.N
# or: kubectl -n rook-ceph delete pod -l ceph-osd-id=N
```

`waiting for readable` in the blocked-op dump is the laggy-PG lease, not a client bug. Remove the sustained writer (a backup or an unpack queue) before declaring the OSD bad.

## Mon store

One mon crash-looping on RocksDB `Corruption` / `missing files` / `block checksum mismatch`: delete that mon's deployment and its PVC. Rook recreates it from quorum. Do not restore an old mon store over a live quorum.

All three mons corrupted: the supported recovery rebuilds the mon store from current OSD data. Never restore an old backup over that. Commands and the CephFS FSMap caveat are in `backup/RECOVERY-PROCEDURES.md`. The metadata CronJob cannot help here: it snapshots Kubernetes objects, not `/var/lib/rook`.

## RBD deadlock trick

A block-pool `min_size=1` window has been used, twice, to break an RBD-activate deadlock when PGs were inactive. It is blockpool only, brief, and both times it was put back to `min_size=2` before the cluster was called healthy. It is not a standing setting. `size` stays 3.

## Disk prediction

`device_failure_prediction_mode` is `local` and SMART samples exist, but `diskprediction_local` has no NVMe model. `ceph device predict-life-expectancy` returns unknown for every OSD, and no `ceph_device_life_expectancy_*` series is emitted. Do not re-add a life-expectancy PrometheusRule. Wear percentage is not in Prometheus. A SMART exporter would be new machinery.

## What not to use

- Plain `rbd du` for free-space or fstrim proof. Use `rbd du --exact`.
- Hand-setting `pg_num` because a decrease looks unsupported. The autoscaler can decrease. Read `ceph osd pool autoscale-status`.
- A full `task rook:wipe-*` as recovery. Those tasks destroy OSD disks and the hostpath metadata backup. Read `RECOVERY-PROCEDURES.md` section 3 first.
