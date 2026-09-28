---
name: rook-ceph
description: "Read before editing kubernetes/apps/base/rook-ceph/**, a Ceph tunable, CephX keys, RGW/S3 settings, a Rook or Ceph version bump, or a storage class choice, and when triaging Ceph health, slow ops or an OSD that will not start. Covers GitOps-only config, the temporary rgw_sigv4_insecure flag, csi keys staying aes, the broken csi CephFS group, and rbd du --exact."
---

# Rook-Ceph: cluster config, CephX, RGW and OSD health

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`kubernetes/apps/base/rook-ceph/rook-ceph/Readme.md`](../../../kubernetes/apps/base/rook-ceph/rook-ceph/Readme.md) - cluster overview and RGW realm steps
- [`docs/ceph-cluster-changelog.md`](../../../docs/ceph-cluster-changelog.md) - change history and decisions
- [`docs/ceph-performance-review.md`](../../../docs/ceph-performance-review.md) - performance review
- [`docs/ceph/toolbox.md`](../../../docs/ceph/toolbox.md) - toolbox usage
- [`docs/ceph/pg.md`](../../../docs/ceph/pg.md) - placement groups
- [`docs/ceph/osd-device-path-recovery.md`](../../../docs/ceph/osd-device-path-recovery.md) - OSD device-path recovery runbook
- [`docs/ceph/osd-store-corruption-recovery.md`](../../../docs/ceph/osd-store-corruption-recovery.md) - OSD store corruption runbook
- [`docs/ceph/backup-recovery-strategy.md`](../../../docs/ceph/backup-recovery-strategy.md) - metadata backup strategy
- [`kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md`](../../../kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md) - emergency recovery
- [`.taskfiles/rook/README.md`](../../../.taskfiles/rook/README.md) - task rook:* recipes

`AGENTS.md` entries (search for the opening words):

- `rbd du` silently under-reports reclaimed space
- CephX key rotation is GitOps-only
- `cephConfig.client.rgw.rgw_sigv4_insecure: "true"` is load-bearing
- Ceph metadata CronJob

## Related skills

- `talos-nodes`
- `cilium-host-policy`
