---
name: rook-ceph
description: "Read before editing kubernetes/apps/base/rook-ceph/**, a Ceph tunable, CephX keys, RGW/S3 settings, a Rook or Ceph version bump, or a storage class choice, and when triaging Ceph health, slow ops or an OSD that will not start. Covers GitOps-only config, rgw_sigv4_insecure staying false, csi keys staying aes, the broken csi CephFS group, and rbd du --exact."
---

# Rook-Ceph: cluster config, CephX, RGW and OSD health

Rook is the only writer of Ceph config. A green `ceph status` does not mean RGW writes work, and a toolbox `ceph config set` survives as mon-DB drift that Git will not reconcile away.

## Tripwires

1. **GitOps only.** Change tunables in `cluster/helmrelease.yaml` `cephConfig`. Never `ceph config set` or `ceph auth` by hand. Before trusting a number, diff `ceph config dump` against that file. Three live keys are mon-DB drift, not Git: `osd_max_backfills=1`, `osd_mclock_override_recovery_settings=true`, `osd_recovery_sleep_ssd=0.05`. A destroyed OSD can also leave a `who=osd.N` row; `ceph config get` on a missing id returns the built-in default and looks like a stored key. Use the dump's who column. [baseline.md](references/baseline.md)
2. **`rgw_sigv4_insecure` stays explicitly `"false"`.** `"true"` disables the CVE-2026-54330 `x-amz-*` check. It was set only for v20.2.4, which rejected minio-go PUTs (unsigned `content-type`) while GET/HEAD still succeeded, so `HEALTH_OK` does not clear RGW. Do not delete the key: Rook leaves a removed `cephConfig` key in the mon DB. After any Ceph bump or flag change, prove both directions: an unsigned `content-type` PUT returns 200 and an unsigned `x-amz-*` header returns 403. PUT 200 alone also passes with the flag at `"true"`. [decisions.md](references/decisions.md)
3. **`security.cephx.csi` stays `aes`.** Kernel clients need Linux >= 7.0; these nodes run 6.18.x. Only `daemon` is on `aes256k`. `keyGeneration` only increases. A CRD older than Rook 1.20.5 prunes `keyType` and burns a generation on an `aes` rotation.
4. **New RWX claims use `ceph-filesystem-rwx` (group `csi-rwx`).** The default `csi` subvolumegroup still returns `EINVAL` on create/getpath. Do not delete the `csi-rwx` CR because the image is past v20.2.2.
5. **Judge reclaim with `rbd du --exact`.** Plain `rbd du` counts 4 MiB objects that still exist after 64 KiB discards. `mountOptions: [discard]` on `ceph-block` stays off. Weekly `fstrim` reaches mounted claims only.
6. **`osdMaxUpdatesInParallel: 1` lives under `spec.storage`.** The CRD default is 20. The spec-level key is pruned and does nothing. Never restart all OSDs. Before a node reboot: `HEALTH_OK` and `task rook:check-osd-device-paths` (Rook #17224). Skill `talos-nodes`.
7. **Do not delete `csi-driver-rbac.yaml` or `csi-driver-tolerations.yaml`.** Both are partial manifests for the CSI Driver resources Rook creates itself (no `ceph-csi-drivers` Helm release exists here). The tolerations one sets `prune: disabled`; SSA merges `spec.nodePlugin.tolerations` only. The RBAC is hand-copied, so it does not follow a Rook bump: on every Rook minor (new `ceph-csi-operator`), `helm template` the matching `ceph-csi-drivers` chart (`oci://ghcr.io/home-operations/charts-mirror/ceph-csi-drivers`) and add any rule it grants that this file lacks. Rook 1.21 (operator v1.1.0) added `volumesnapshots` update/watch and PVC `patch`; kopiur backups depend on the snapshot path.
8. **OSD memory request stays 12Gi, limit 14Gi.** `osd_memory_target` (10 GiB) sizes the BlueStore cache, not RSS. The highest observed OSD working set is 9317 Mi. 10 Gi is not a free cut. Mon and logcollector stay Guaranteed (`requests == limits`). An empty `limits: {}` does not clear the operator chart's 512Mi default; the operator limit is an explicit 1Gi.
9. **`rook` and `nfs` mgr modules stay off.** The rook module crash-looped and blocked node upgrades. `nfs` is unused and was evaluated with the discard work.
10. **StorageClass parameters are immutable.** A change is a new class, not an edit. No `deviceClass` on pool `data0`. MDS anti-affinity stays preferred, not required. Ceph alert counters go through `increase()`, never a raw `> N` compare.
11. **Every node taint needs a `placement.all` toleration, `PreferNoSchedule` included.** Rook counts a node with an untolerated taint as not a storage node. The operator then logs `0 of the 3 storage nodes are valid` and `not updating OSD N`, and the OSDs keep the old image while the CephCluster is `Ready` and `HEALTH_OK`. tuppr's `tuppr.home-operations.com/outdated` taint caused this after the v20.2.5 bump (#2240); a `Failed` TalosUpgrade keeps that taint on every node.
12. **The metadata CronJob is not last-resort DR.** `ceph-backup-pvc` is OpenEBS hostpath on this cluster. A host wipe destroys it. Steps: `backup/RECOVERY-PROCEDURES.md`.

## Where things live

| What | Path |
|---|---|
| Cluster HelmRelease (image, cephConfig, CephX, pools, placement, resources) | `kubernetes/apps/base/rook-ceph/rook-ceph/cluster/helmrelease.yaml` |
| Operator chart, explicit memory limit | `kubernetes/apps/base/rook-ceph/rook-ceph/operator/helmrelease.yaml` |
| Partial CSI Driver tolerations (`prune: disabled`) | `operator/csi-driver-tolerations.yaml` |
| Partial CSI RBAC (do not delete) | `operator/csi-driver-rbac.yaml` |
| `csi-rwx` subvolumegroup | `cluster/cephfs-rwx-subvolumegroup.yaml` |
| PrometheusRules | `cluster/prometheusrules.yaml` |
| Metadata backup + emergency steps | `backup/backup-system.yaml`, `backup/RECOVERY-PROCEDURES.md` |
| Rook task recipes | `.taskfiles/rook/Taskfile.yaml` (`task rook:check-osd-device-paths`) |
| Host firewall for Ceph ports | skill `cilium-host-policy` |

## Procedures

- Current versions, OSD map, mclock caps, mon-DB drift: [baseline.md](references/baseline.md).
- CephX, SigV4, reclaim, QoS, modules, pools: [decisions.md](references/decisions.md).
- RGW realm/zone after a rebuild, and how to see a 403: [rgw.md](references/rgw.md).
- Toolbox, slow ops, PG reads: [diagnostics.md](references/diagnostics.md).
- OSD stuck `Init:0/5` after a reboot (device-path drift, Rook #17224): [osd-device-path-recovery.md](references/osd-device-path-recovery.md).
- OSD crash-looping in `load_pgs` (BlueStore OMAP corruption, destroy and recreate): [osd-store-corruption-recovery.md](references/osd-store-corruption-recovery.md).
- One-screen change log, one line per change with PR: [changelog.md](references/changelog.md).
- Emergency recover and post-rebuild RGW steps: `backup/RECOVERY-PROCEDURES.md`.

## Verify

- Render: `task flux:test:all`.
- Live (read-only): `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status` and `ceph config dump`. CephX: `kubectl -n rook-ceph get cephcluster rook-ceph -o jsonpath='{.status.cephx}'`.
- RGW writes: `kubectl -n rook-ceph logs -l app=rook-ceph-rgw -c rgw --since=20m | grep -oE '"(GET|PUT|HEAD) [^"]*" [0-9]{3}'`. A `PUT` 403 with user `-` is auth, not a bucket policy.
- Device paths before any reboot: `task rook:check-osd-device-paths`.
