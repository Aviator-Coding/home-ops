# Ceph change log

Current config: [baseline.md](baseline.md). Emergency steps, RGW realm/zone, slow ops, all-mon rebuild: `kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md`. Git history is the incident archive.

| Change | PR |
|---|---|
| `csi-rwx` subvolumegroup and StorageClass. New RWX uses this, not the default `csi` group. | [#977](https://github.com/Aviator-Coding/home-ops/pull/977) |
| Per-OSD mClock IOPS caps. Samsung 15000, Lexar 7000. No `osd.1` row. | [#981](https://github.com/Aviator-Coding/home-ops/pull/981) |
| `osdMaxUpdatesInParallel: 1` under `spec.storage`. A spec-root key is pruned. | [#986](https://github.com/Aviator-Coding/home-ops/pull/986) |
| Off-peak scrub window and `high_client_ops` mclock profile. | [#905](https://github.com/Aviator-Coding/home-ops/pull/905) |
| Rook mgr module disabled. It crash-looped and blocked node upgrades. | [#1384](https://github.com/Aviator-Coding/home-ops/pull/1384) |
| Mgr dashboard removed. | [#1394](https://github.com/Aviator-Coding/home-ops/pull/1394) |
| Daemon CephX keys on `aes256k`, generation 2. `csi` stays `aes`. | [#1398](https://github.com/Aviator-Coding/home-ops/pull/1398) |
| `rgw_sigv4_insecure: "true"` restored S3 PUTs on v20.2.4. Remove only with an image that contains ceph/ceph#71192. | [#1401](https://github.com/Aviator-Coding/home-ops/pull/1401) |
| talos-1 `NotIn` affinities reverted after the RAM RMA. | [#1393](https://github.com/Aviator-Coding/home-ops/pull/1393) |
| Default `csi` subvolumegroup still `EINVAL` after v20.2.3. Do not delete the `csi-rwx` CR. | [#1349](https://github.com/Aviator-Coding/home-ops/pull/1349) |
| fstrim selects kubelet mounts. `nfs` mgr module disabled. | [#1609](https://github.com/Aviator-Coding/home-ops/pull/1609) |
| Active CephFS MDS ranks prefer different nodes. | [#1650](https://github.com/Aviator-Coding/home-ops/pull/1650) |
| Slow-op warn threshold raised so one stall cannot latch `HEALTH_WARN` and block the reboot gate. | [#1665](https://github.com/Aviator-Coding/home-ops/pull/1665) |
| OSD memory request 14Gi to 12Gi. Limit stays 14Gi. OSDs stay Burstable. | [#1736](https://github.com/Aviator-Coding/home-ops/pull/1736) |
| Operator memory limit set explicitly. `limits: {}` does not clear the chart's 512Mi. | [#1751](https://github.com/Aviator-Coding/home-ops/pull/1751) |
| Fourth RWX claim is `ai/pvc` `shared-files`, beside shared-downloads, tdarr and shared-xml. | [#1720](https://github.com/Aviator-Coding/home-ops/pull/1720) |

Mon-DB drift that is live and not in Git: `osd_max_backfills=1`, `osd_mclock_override_recovery_settings=true`, `osd_recovery_sleep_ssd=0.05`. A destroyed OSD can leave a `who=osd.N` key; `ceph config get` on a missing id returns the built-in default. The historical `osd.1` mclock override is not in the dump.
