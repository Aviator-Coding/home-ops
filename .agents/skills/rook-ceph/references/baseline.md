# Ceph baseline

Re-read the HelmRelease and `ceph config dump` before quoting a number. The old changelog's version table (Rook 1.20.6, Talos 1.13.9, Kubernetes 1.36.3) is stale.

## Versions (live-checked against Git and the cluster)

| Layer | Value | Where |
|---|---|---|
| Rook operator | `ghcr.io/rook/ceph:v1.20.7` | operator deploy image; chart tag in `operator/ocirepository.yaml` |
| Ceph | `quay.io/ceph/ceph:v20.2.4` (20.2.4 tentacle) | `cephImage.tag`; toolbox image matches |
| Talos | v1.14.1, kernel `6.18.51-talos` | `kubectl get nodes`; tuppr `TalosUpgrade` and `machineconfig.yaml.j2` installer pin match |
| Kubernetes | v1.36.5 | kubelet and control-plane images in `machineconfig.yaml.j2` |

`CONFIG_CEPH_FS`, `CONFIG_BLK_DEV_RBD` and `CONFIG_CEPH_LIB` are built-in on this kernel. Do not add a `machine.kernel.modules` entry for `rbd`.

Topology: 3 nodes, 6 OSDs, `failureDomain: host`, pool size 3, `provider: host`, `requireMsgr2: true`, no separate cluster network. FSID is in Secret `rook-ceph-mon` key `fsid` and in the metadata backup. Do not paste it into a new manifest.

## OSD map and mclock caps

Caps are GitOps `cephConfig` per-daemon keys. Live `ceph config dump` matches. Samsung 980/990 PRO `15000`, Lexar NM790 `7000`. Values are literature estimates, not fio on a live BlueStore device (that test is destructive).

| OSD | Node | Drive | `osd_mclock_max_capacity_iops_ssd` |
|---|---|---|---|
| osd.0 | talos-1 | Samsung 980 PRO 2 TB | 15000 |
| osd.3 | talos-1 | Lexar NM790 4 TB, DRAM-less HMB | 7000 |
| osd.6 | talos-2 | Samsung 990 PRO 2 TB | 15000 |
| osd.5 | talos-2 | Lexar NM790 4 TB, DRAM-less HMB | 7000 |
| osd.2 | talos-3 | Samsung 980 PRO 2 TB | 15000 |
| osd.4 | talos-3 | Lexar NM790 4 TB, DRAM-less HMB | 7000 |

There is no osd.1 (`ceph osd find 1` is `ENOENT`). No drive has power-loss protection. Mon stores sit on each node's SN770M system disk (firmware 731150WD; the 731100WD HMB corruption is closed). The three Lexar OSDs are the fragile ones.

Once a cap is set the OSD logs `Skip OSD benchmark test` and will not re-bench until the key is removed. Rook does not `ceph config rm` a key you delete from Git. Removal is a toolbox `ceph config rm osd.N osd_mclock_max_capacity_iops_ssd` plus an OSD restart.

## Mon-DB drift

`ceph config dump` (who column), not `ceph config get`.

In Git and live: `osd_mclock_profile=high_client_ops`, `osd_recovery_max_active=3`, the six per-OSD caps, `rgw_sigv4_insecure=true`, `osd_memory_target=10737418240`.

Live, not in Git (left by old `ceph config set`; Rook will not delete them):

- `osd` `osd_max_backfills=1`
- `osd` `osd_mclock_override_recovery_settings=true`
- `osd` `osd_recovery_sleep_ssd=0.05`

Do not assume recovery is capped at 1 or at 3 without this dump. `osd_recovery_max_active=3` is the Git value; backfills are a different key.

Historical trap, currently clear: a stored `who=osd.1` `osd_mclock_max_capacity_iops_ssd=41589.54` used to linger after osd.1 was destroyed. The dump has no `osd.1` row now. `ceph config get osd.1 osd_mclock_max_capacity_iops_ssd` still prints `21500`, the Ceph default, which is not a stored who. If a who-row for a missing OSD comes back, `ceph config rm` it. That is an operator command, not a manifest.

## Other tunables still in effect

- `osd_max_scrubs: 1`, scrub window 01:00-07:00.
- `mds_cache_memory_limit` 8 GiB (pod limit 10 Gi).
- Pool compression `none`. `bulk: true` on the block pool and `cephfs-data0`.
- CephFS mounter is the kernel client (`cephFsClientType: autodetect` on Talos 6.x). The old chart key `forceCephFSKernelClient` is inert under the CSI operator and must stay deleted.
- `kernelMountOptions: ms_mode=prefer-crc` so the kernel client negotiates msgr2.
- `bluestore_slow_ops_warn_threshold` / `_warn_lifetime` are `5` / `3600` (Ceph defaults `1` / `86400`) so one transient stall cannot latch `HEALTH_WARN` long enough to block `task rook:check-osd-device-paths`.

## Storage classes

| Class | Use |
|---|---|
| `ceph-block` | RWO. No `mountOptions: [discard]`. |
| `ceph-filesystem-rwx` | RWX. Group `csi-rwx`. The four Git PVCs are `downloads/shared-downloads`, `media/tdarr`, `ai/shared-xml`, `ai/shared-files`. |
| `ceph-filesystem` | Default group `csi`. Still broken. Do not use for a new claim. |
| `openebs-hostpath` | Local. The metadata backup PVC uses this. |
| `csi-ceph-blockpool` | VolumeSnapshotClass. The pool parameter on that class is load-bearing. |

`ceph-filesystem` (group `csi`) returns `EINVAL: invalid value specified for ceph.dir.subvolume` on create/getpath/info/rm. A fresh group is fine, which is why `csi-rwx` exists. Re-test create/getpath against group `csi` before any move back, and do not delete `CephFilesystemSubVolumeGroup/ceph-filesystem-csi-rwx` while the image is past v20.2.2.
