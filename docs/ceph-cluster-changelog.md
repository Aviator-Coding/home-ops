# Ceph Cluster Change Log

Track record of **deliberate configuration and topology changes** to the Rook-Ceph
cluster — what changed, why, the evidence behind it, and how to roll it back. Mirrors
[`hardware-incidents.md`](./hardware-incidents.md) but for *changes we make on purpose*
rather than hardware failures. The goal is that when something breaks we can answer
"what did we change recently, and why?" without spelunking git.

- **Hardware failures** → [`hardware-incidents.md`](./hardware-incidents.md)
- **The 2026-06-01 deep performance review & rationale** → [`ceph-performance-review.md`](./ceph-performance-review.md)
- **This file** → chronological log of applied changes + a verified backlog of proposed ones

> ⚠️ All Ceph config is GitOps. Changes go through
> `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml` (or the operator chart),
> **never** ad-hoc `ceph config set` — Rook reverts drift on reconcile. Record every change
> here when you merge it.

---

## Current baseline (2026-06-14)

| Layer | Value |
|-------|-------|
| **Rook** | v1.20.0 (operator + cluster chart) — latest upstream, released 2026-06-02 |
| **Ceph** | v20.2.1 **Tentacle** — latest published point release (v20.2.2+ not yet on quay) |
| **Talos** | v1.13.2 (kernel has `CONFIG_CEPH_FS=y`, `CONFIG_BLK_DEV_RBD=y`, `CONFIG_CEPH_LIB=y` — CephFS + krbd **built-in**) |
| **Kubernetes** | v1.36.1 |
| **Cluster FSID** | `6562d9b0-883a-4e55-8b5d-899eaa7e0d10` |
| **Topology** | 3 nodes (talos-1/2/3), 6 OSDs, all NVMe, `failureDomain: host`, size=3 |
| **Network** | `provider: host`, `requireMsgr2: true`, no dedicated cluster/replication network |

### Key tunables in effect (and why)

| Setting | Value | Rationale |
|---------|-------|-----------|
| `osd_memory_target` | 10 GiB | read-cache headroom (pod limit 14Gi); replaced a hard `bluestore_cache_size=2GB` that disabled autotuning |
| `osd_mclock_profile` | `high_client_ops` | prioritise client IO over background work on latency-sensitive consumer NVMe |
| `osd_max_scrubs` / scrub window | `1` / 01:00–07:00 | gentle, off-peak deep-scrubs (was ~23 concurrent cluster-wide) |
| `osd_recovery_max_active` | `3` | don't swamp homelab hardware during recovery |
| `mds_cache_memory_limit` | 8 GiB | metadata-heavy CephFS ops (pod limit 10Gi) |
| compression (all pools) | `none` | data is already-compressed media + RBD; zstd burned CPU + write latency for ~nothing |
| `bulk: true` (block + cephfs-data0) | — | let the PG autoscaler provision a full PG complement and grow gradually |
| **CephFS mounter** | **kernel client** | ceph-csi-operator `Driver` CR `cephFsClientType: autodetect` → kernel on Talos 6.x (OperatorConfig default is `kernel`); verified live (`/proc/mounts` shows `type ceph`, msgr2/3300). The legacy chart `forceCephFSKernelClient` value is **inert** under Rook v1.20 csi-operator mode (dead line removed 2026-06-14) |
| `kernelMountOptions` | `ms_mode=prefer-crc` | set via `cephClusterSpec.csi.cephfs.kernelMountOptions` (CephCluster CR); makes the kernel client negotiate msgr2 (cluster has `requireMsgr2: true`) — **in effect now** (visible in `/proc/mounts`) |

### OSD / drive inventory

| OSD | Node | Model | Size | DRAM | PLP |
|-----|------|-------|------|------|-----|
| osd.0 | talos-1 | Samsung 980 PRO | 2 TB | yes | no |
| osd.3 | talos-1 | Lexar NM790 | 4 TB | **no (HMB)** | no |
| osd.6 | talos-2 | Samsung 990 PRO | 2 TB | yes | no |
| osd.5 | talos-2 | Lexar NM790 | 4 TB | **no (HMB)** | no |
| osd.2 | talos-3 | Samsung 980 PRO | 2 TB | yes | no |
| osd.4 | talos-3 | Lexar NM790 | 4 TB | **no (HMB)** | no |

**Mon stores** live on `openebs-hostpath` on each node's WD_BLACK SN770M system disk —
now on firmware **731150WD** (the 731100WD HMB bug that corrupted mon RocksDB is *resolved*;
see [hardware-incidents.md 2026-06-14](./hardware-incidents.md)). No drive has power-loss
protection; the three Lexar NM790 are DRAM-less and remain the fragile OSDs.

---

## How to add an entry

When you merge a Ceph config/topology change, prepend an entry (newest first):

```markdown
## [YYYY-MM-DD] Short title  (PR #NNN)

| Field | Value |
|-------|-------|
| **Change** | what was changed (file + key) |
| **Why** | trigger / goal |
| **Risk** | what could break, blast radius |
| **Rollback** | exact revert |
| **Verify** | command(s) proving it worked |

Notes / evidence / sources.
```

---

## Change log

### [2026-06-14] P2: pinned realistic per-OSD mClock IOPS (override inflated auto-bench)  (PR #981)

| Field | Value |
|-------|-------|
| **Change** | Set `osd_mclock_max_capacity_iops_ssd` per-OSD via `cephClusterSpec.cephConfig` per-daemon sections (`"osd.N":`): Lexar NM790 **osd.3/4/5 → `7000`**, Samsung 980/990 PRO **osd.0/2/6 → `15000`**. |
| **Why** | mClock startup auto-bench reported **49k–61k IOPS for every OSD** (config source `basic`) — ~3–4× too high for the no-PLP Samsungs, **~8–12× too high for the DRAM-less NM790s** — on the 4K sync-write path Ceph uses. Inflated capacity mis-allocates client vs background IO and is a plausible contributor to the slow-ops/laggy-PG cascade. |
| **Risk** | Low — scheduler tuning only; no data-path/peering/availability impact. Values sit well above the `1000` low-guard so client IO is protected, not starved. Picked up **live, no OSD restart** (restarting the fragile NM790s is itself a slow-ops risk, so avoided). |
| **Rollback** | `git revert`; then (Rook doesn't auto-`rm` unmanaged keys) in toolbox: `for n in 0 2 3 4 5 6; do ceph config rm osd.$n osd_mclock_max_capacity_iops_ssd; done` + restart OSDs to re-enable auto-bench. |
| **Verify** | `ceph config dump \| grep mclock_max_capacity` → 6 keys, source `advanced`; `ceph config get osd.N osd_mclock_max_capacity_iops_ssd` → `7000`/`15000`. |

Values are conservative literature/community estimates (Proxmox false-`osd_mclock_max_capacity_iops_ssd` thread; consumer-NVMe 4K sync-write reviews), **NOT fio-measured** — fio on a live BlueStore device is destructive, so deferred (measure only on a drained + stopped OSD, one at a time). The three *identical* NM790s auto-benched 52.7k/55.4k/60.9k (16% spread) — the classic "bench caught SLC-cache burst, not steady state" symptom. OSD→model mapping verified by drive **serial** via `ceph osd metadata` (the `/dev/nvmeXn1` names differ between Rook's namespace view and Talos enumeration). Once a value is set the OSD logs `Skip OSD benchmark test` and never re-benches until the key is removed.

**Cleanup note (ad-hoc toolbox op, NOT GitOps):** a stale `osd.1` key `osd_mclock_max_capacity_iops_ssd=41589.54` lingers in the mon config DB for a non-existent OSD (`ceph osd find 1` → `ENOENT`; not in the CRUSH tree). Harmless (no daemon reads it) but untidy — remove it with:
```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph config rm osd.1 osd_mclock_max_capacity_iops_ssd
```
It lives only in the mon DB (not the helmrelease), so it can't be expressed as a GitOps revert — hence this runbook note.

### [2026-06-14] P1 verified: CephFS already on kernel client — removed dead `forceCephFSKernelClient` config  (PR #981)

| Field | Value |
|-------|-------|
| **Change** | Removed two **no-op** CSI config lines: `csi.cephfs.forceCephFSKernelClient: false` from `cluster/helmrelease.yaml` and `csi.cephFSKernelMountOptions` from `operator/helmrelease.yaml`. Kept the effective `cephClusterSpec.csi.cephfs.kernelMountOptions: ms_mode=prefer-crc`. Docs truth-up (this entry + baseline table + backlog P1). |
| **Why** | Backlog P1 assumed CephFS RWX was on slow `ceph-fuse` and a flip to the kernel client was the biggest perf lever. **Live verification disproved this:** CephFS is already on the kernel client. Under Rook v1.20 (ceph-csi-operator mode) the mounter is the ceph-csi `Driver` CR `cephFsClientType` (=`autodetect` → kernel on Talos 6.x; OperatorConfig default `kernel`). The chart `forceCephFSKernelClient` value path doesn't exist in the cluster chart, and `cephFSKernelMountOptions` was dropped from the v1.20 operator chart — both silently ignored. |
| **Risk** | None functional — removing inert values doesn't change rendered manifests, so Helm sees no Driver/CephCluster diff. A Helm upgrade may bounce the operator pod, but existing kernel CephFS mounts are host mounts and persist independently. |
| **Rollback** | `git revert`; the deleted lines were inert, so they return as inert. |
| **Verify** | `/proc/mounts` on each node: CephFS mounts are `type ceph` + `ms_mode=prefer-crc` + mon port 3300, **no `ceph-fuse`** (talos-1: 11, talos-3: 4); `kubectl -n rook-ceph get drivers.csi.ceph.io rook-ceph.cephfs.csi.ceph.com -o jsonpath='{.spec.cephFsClientType}'` → `autodetect`. |

Evidence captured 2026-06-14 (read-only): all CephFS mounts kernel-client; ceph-csi `Driver` `cephFsClientType: autodetect`; `OperatorConfig` default `kernel`; operator ConfigMap has no `CSI_FORCE_CEPHFS_KERNEL_CLIENT` / `CSI_CEPHFS_KERNEL_MOUNT_OPTIONS`; CephCluster CRD `spec.csi.cephfs` exposes only `fuseMountOptions` + `kernelMountOptions` (no mounter-type knob). The related [2026-06-12 "CephFS CSI forced to autodetect → ceph-fuse"](#2026-06-12-cephfs-csi-forced-to-autodetect--ceph-fuse--pr--commit-17c7a9df-ae44b3ac) change never actually forced fuse — at most it produced `autodetect`, which still mounts via the kernel on this kernel version.

### [2026-06-13] CephFS RWX moved to fresh `csi-rwx` subvolumegroup  (PR #977)

| Field | Value |
|-------|-------|
| **Change** | New subvolumegroup `csi-rwx` (clusterID `eb9cbc3c…`) + StorageClass `ceph-filesystem-rwx`; migrated `downloads/shared-downloads` (2Ti) and `media/tdarr-temp` onto it |
| **Why** | Ceph v20.2.1 corrupted the default `csi` subvolumegroup → all RWX provision/expand/snapshot/delete failed `EINVAL invalid value specified for ceph.dir.subvolume` |
| **Risk** | data migration; full-throttle rsync (310 MB/s) **wedges** the small OSDs (osd.0/osd.6) — use `--bwlimit=80M` |
| **Rollback** | old PVs kept as `Retain` safety-net (delete when confident) |
| **Verify** | apps healthy on `ceph-filesystem-rwx`, Ceph back to baseline |

Cross-ref: [CephFS Tentacle subvolumegroup bug](../) (memory). Retire `csi-rwx` when Ceph v20.2.2+ ships and the default group is fixed.

### [2026-06-12] CephFS CSI forced to autodetect → `ceph-fuse`  (PR — commit 17c7a9df, ae44b3ac)

| Field | Value |
|-------|-------|
| **Change** | `csi.cephfs.forceCephFSKernelClient: false` (default is `true`); added `kernelMountOptions: ms_mode=prefer-crc` |
| **Why** | ceph-csi default `cephFsClientType=kernel` reportedly failed with **"Module ceph not found"**; switched to autodetect so CSI falls back to `ceph-fuse` |
| **Risk** | ⚠️ **`ceph-fuse` is materially slower than the kernel client** — this trades performance for "it works" |
| **Rollback** | set `forceCephFSKernelClient: true`, restart CSI cephfs nodeplugin + remount |
| **Verify** | CephFS volumes mount; (today) they mount via fuse |

> **⚠️ This change is now in doubt.** The stated reason — *"Talos does not ship ceph.ko"* — is
> **contradicted** by the Talos kernel config: `CONFIG_CEPH_FS=y` on the `release-1.11/1.12/1.13`
> branches means the CephFS client is **compiled into the kernel** (built-in), so there is no
> loadable `ceph.ko` to be "missing", and `mount -t ceph` should work. The "Module ceph not found"
> error is the classic **built-in-vs-loadable gotcha** (a `=y` feature has no `.ko`, so a module
> presence check fails even though the FS works). ceph-csi's own mounter
> (`internal/cephfs/mounter/volumemounter.go`) detects kernel support by probing the `mount.ceph`
> **binary + kernel version**, not by modprobe — so the real failure on 2026-06-12 is **unexplained
> by kernel capability** and must be re-tested live. See [backlog P1](#p1--re-test-the-cephfs-kernel-client-biggest-perf-lever).

### [2026-06-12] Rook v1.19.6 → v1.20.0  (PR #934)

| Field | Value |
|-------|-------|
| **Change** | operator + cluster chart bumped to v1.20.0 |
| **Why** | stay current; v1.20.0 is the latest release |
| **Risk** | v1.20.0 ships the CSI driver RBAC outside the operator chart → needed a manual workaround (`operator/csi-driver-rbac.yaml`, [rook#17644](https://github.com/rook/rook/issues/17644)) — remove once v1.20.1+ ships it |
| **Rollback** | pin `ocirepository.yaml` tag back to v1.19.6 |
| **Verify** | `kubectl -n rook-ceph get deploy rook-ceph-operator -o jsonpath='{.spec.template.spec.containers[0].image}'` |

### [2026-06-03] Raised OSD/MDS memory targets  (PR #908)

| Field | Value |
|-------|-------|
| **Change** | `osd_memory_target` → 10 GiB (OSD pod limit → 14Gi); `mds_cache_memory_limit` → 8 GiB (MDS limit → 10Gi) |
| **Why** | read-cache headroom; more onode/metadata hits → fewer reads hit the slow drives. **Read optimisation, NOT a fix for the write-latency slow-ops storm.** |
| **Rollback** | revert the two values + pod limits |
| **Verify** | `ceph config get osd osd_memory_target`; OSD RSS under limit |

### [2026-06-03] Gentler off-peak scrubs + `high_client_ops` mClock  (PR #905)

| Field | Value |
|-------|-------|
| **Change** | `osd_max_scrubs: 1`, scrub window 01:00–07:00, `osd_mclock_profile: high_client_ops` |
| **Why** | reduce background-IO contention on consumer NVMe; prioritise client IO |
| **Rollback** | remove the three keys (reverts to mClock `balanced` default + 24/7 scrubs) |
| **Verify** | `ceph config get osd osd_mclock_profile` → `high_client_ops` |

### [2026-06-01 → 06-02] PG autoscaler + compression + roots fixes  (PRs #900, #902, #903, #904)

| Field | Value |
|-------|-------|
| **Change** | compression `none` on all pools + freed OSD cache (#900); `bulk: true` on block + cephfs-data0 (#902); restored immutable ceph-block StorageClass params to unstall the HR (#903); dropped `deviceClass: nvme` from cephfs-data0 to fix "overlapping roots" that had stalled the autoscaler on **all** pools (#904) |
| **Why** | aftermath of the 2026-06-01 slow-ops incident — see [`ceph-performance-review.md`](./ceph-performance-review.md) |
| **Rollback** | per-PR `git revert` |
| **Verify** | `ceph osd pool ls detail` (compression none, pg_num growing); `ceph osd pool autoscale-status` (no overlapping-roots, all pools listed) |

### [2026-04-11] Ceph v20.2.0 → v20.2.1  (PR #681)

| Field | Value |
|-------|-------|
| **Change** | `cephImage.tag: v20.2.1` |
| **Why** | Tentacle point release |
| **Note** | v20.2.1 corrupted the default `csi` subvolumegroup (later worked around — see 2026-06-13) |

---

## Backlog — proposed changes (researched, NOT yet applied)

Ranked by impact. Each is verify-then-apply; treat as plan-mode work, not a blind merge.
Sources are from the 2026-06-14 deep-research pass (Ceph Squid/Tentacle-era docs).

### P1 — ✅ RESOLVED (2026-06-14): CephFS kernel client was already active

> **Resolution:** No flip was needed. Live verification showed CephFS RWX is **already on the
> kernel client** (`/proc/mounts` → `type ceph`, `ms_mode=prefer-crc`, mon port 3300 msgr2; **no
> `ceph-fuse`** on any node). Under Rook v1.20 (ceph-csi-operator mode) the mounter is the ceph-csi
> `Driver` CR `cephFsClientType` (=`autodetect` → kernel on Talos 6.x; `OperatorConfig` default
> `kernel`), **not** the chart `forceCephFSKernelClient` value (which is inert here). The dead
> `forceCephFSKernelClient` / `cephFSKernelMountOptions` lines were removed; the effective option
> `cephClusterSpec.csi.cephfs.kernelMountOptions` was kept. See the [2026-06-14 change-log entry](#2026-06-14-p1-verified-cephfs-already-on-kernel-client--removed-dead-forcecephfskernelclient-config--pr-981).
> The original analysis below is retained as the read-only evidence procedure.

- **What (original):** flip `csi.cephfs.forceCephFSKernelClient` back to `true` so CephFS RWX uses the
  Linux kernel client (`mount -t ceph`) instead of `ceph-fuse`.
- **Why:** the kernel CephFS client is **materially faster** than `ceph-fuse` (userspace,
  per-op context switches), and Talos v1.13 has `CONFIG_CEPH_FS=y` **built-in** — the
  "Talos lacks ceph.ko" premise behind the current fuse fallback is wrong. RBD (`ceph-block`)
  already uses the kernel `krbd` mounter by default, so only CephFS is on the slow path.
- **Verify FIRST (live, read-only):**
  ```bash
  # 1. Confirm the running kernel exposes ceph as a filesystem (built-in => listed, no module needed)
  talosctl -n <node-ip> read /proc/filesystems | grep ceph        # expect: "  ceph"
  talosctl -n <node-ip> read /proc/modules    | grep -E 'ceph|rbd' # built-in => may show NOTHING (that's fine)
  # 2. Which mounter is each PV using right now?
  kubectl get pv -o json | jq -r '.items[]|select(.spec.csi.driver|test("cephfs"))|.spec.csi.volumeHandle' # cephfs PVs
  #   on the node, a kernel cephfs mount shows "type ceph"; fuse shows "ceph-fuse" in `mount`
  ```
- **Then:** in a maintenance window, set `forceCephFSKernelClient: true`, let Flux reconcile,
  restart the `csi-cephfsplugin` nodeplugin DaemonSet, and **remount** (existing fuse mounts
  don't auto-switch — bounce one low-risk consumer pod and confirm it comes up as `type ceph`).
- **Gotcha (msgr2):** the cluster has `requireMsgr2: true`. Keep `kernelMountOptions:
  ms_mode=prefer-crc` so the kernel client negotiates msgr2 on mon port 3300. Modern kernels
  (Talos 1.13 is 6.x) support msgr2, so this should connect.
- **Risk:** if the kernel mount genuinely fails (re-creating the original "Module ceph not
  found" / a real msgr2 issue), revert to `false` — fuse is the known-good fallback. Blast
  radius of krbd/kernel-cephfs is node-wide on a kernel bug, vs userspace for fuse, but the
  kernel client is the battle-tested production default.
- **Note:** `# CONFIG_FSCACHE is not set` on Talos → the kernel CephFS client's optional
  fscache-backed page caching is unavailable, but that's not required for normal operation.

### P2 — ✅ RESOLVED (2026-06-14): pinned per-OSD mClock IOPS

> **Resolution:** Confirmed live the auto-bench inflated every OSD to 49k–61k IOPS (NM790s ~8–12×
> over realistic 4K sync-write). Pinned conservative per-OSD `osd_mclock_max_capacity_iops_ssd`
> (NM790 osd.3/4/5 → 7000; Samsung osd.0/2/6 → 15000) via GitOps `cephConfig` per-daemon sections —
> see the [2026-06-14 change-log entry](#2026-06-14-p2-pinned-realistic-per-osd-mclock-iops-override-inflated-auto-bench--pr-981).
> `fio` measurement deferred (destructive on live OSDs); values are conservative estimates, easily
> iterated. The original analysis below is retained as the rationale + (deferred) fio methodology.

- **What (original):** measure real 4 KiB random-write IOPS with `fio` on each OSD device, then set
  `osd_mclock_max_capacity_iops_ssd` per-OSD instead of trusting the startup auto-bench.
- **Why:** mClock auto-benchmarks each OSD's IOPS at startup; **on fast/DRAM-less NVMe this
  result is frequently inflated/unrealistic**, which mis-allocates client vs background IO and
  is a plausible contributor to the laggy/slow-ops cascade. Threshold guards exist
  (`osd_mclock_iops_capacity_threshold_ssd` 80000 high / `…_low_threshold_ssd` 1000 low).
- **Verify:** `ceph config show osd.N osd_mclock_max_capacity_iops_ssd` per OSD; compare to a
  real fio run; set manually where the auto value is implausible (esp. the three NM790).
- **Source:** docs.ceph.com `/rados/configuration/mclock-config-ref/`;
  Proxmox forum thread on false `osd_mclock_max_capacity_iops_ssd` values.

### P3 — Tighten `BLUESTORE_SLOW_OP_ALERT` as an early-warning tripwire

- **What:** lower `bluestore_slow_ops_warn_lifetime` (default 86400s) and keep
  `bluestore_slow_ops_warn_threshold` low so a struggling drive surfaces *fast* (e.g.
  lifetime 300, threshold 5 — tune to taste), giving time to `ceph osd out`/replace before a
  laggy cascade. `osd_op_complaint_time` default is 30s.
- **Why:** the slow-op alert is the earliest signal of a DRAM-less OSD going bad; a tighter
  window catches it before clients wedge.
- **Source:** docs.ceph.com `/rados/operations/health-checks/`; rook#15403.

### P4 — Verify PG counts land near target (~100/OSD), keep autoscaler honest

- **What:** confirm the `bulk: true` pools actually grew toward `mon_target_pg_per_osd` (100).
  Docs recommend ~200 PGs/OSD for all but the smallest clusters; >500 risks peering/RAM.
  With 6 OSDs, expect ~50–70 PG replicas/OSD initially with the balancer on.
- **Verify:** `ceph osd pool autoscale-status`; `ceph osd df tree` (PGs column). If a pool is
  stuck low, consider `pg_autoscale_mode: warn` + a manual `pg_num` bump.
- **Source:** docs.ceph.com `/rados/operations/placement-groups/`.

### P5 — (Optional, lower ROI) dedicated cluster/replication network

- **What:** split 3× replication/recovery traffic onto a dedicated VLAN/CIDR (nodes already
  have VLAN 3/90).
- **Why / caveat:** helps *during* recovery/backfill, but if the bottleneck is purely
  consumer-NVMe commit latency (likely here), network separation gives little benefit. Lower
  priority than P1–P2.

### P0 (standing) — the real durable fix: PLP datacenter NVMe

Retiring the three DRAM-less Lexar NM790 for enterprise NVMe with power-loss protection
(Samsung PM9A3, Micron 7450, Kioxia CD/CM, Solidigm D7) remains the single biggest reliability
win — it removes the sync-write latency cliff that triggers the lease cascade at the source.
See [`ceph-performance-review.md`](./ceph-performance-review.md) §F1.

---

## Operational runbooks (for "when something breaks")

### Slow-ops / `waiting for readable` laggy-PG cascade

The PG read lease = `osd_pool_default_read_lease_ratio` (0.8) × `osd_heartbeat_grace`. A slow
OSD that can't renew leases in time flips its PGs to `LAGGY` and **blocks reads** (this is a
*correctness* feature, not a bug — the `recheck_readable` defect [#53806] was already fixed in
Reef v18.2.0, so v20.2.1 has it). The cause here is genuine slow-OSD latency.

```bash
ceph -s ; ceph health detail                    # find the slow OSD + blocked-op count
ceph tell osd.<N> dump_blocked_ops              # flag "waiting for readable", which pool
ceph osd down osd.<N>                            # re-peer the stuck OSD (client io ~0 => low risk)
#   or: kubectl -n rook-ceph delete pod -l ceph-osd-id=<N>   (restart the OSD pod)
```
Then remove the sustained write trigger (e.g. pause the sabnzbd queue). Longer-term: P2 (mClock
IOPS) + P0 (PLP drives). Source: docs.ceph.com `/dev/osd_internals/stale_read/`.

### Mon RocksDB store corruption (the SN770M failure mode)

Corruption presents as ceph-mon crash-looping with RocksDB `Corruption: error in middle of
record` / `missing files …/store.db/NNN.ldb` / `block checksum mismatch`. **Single mon:** delete
its deployment + PVC; Rook recreates it and re-syncs from quorum (see
[hardware-incidents.md](./hardware-incidents.md) and the mon-failover-deadlock runbook).

**All mons corrupted** (catastrophic) — the **only** supported recovery is to rebuild from
current OSD state; **never** restore from an old backup:
```bash
# on each OSD host:
ceph-objectstore-tool --data-path /var/lib/ceph/osd/ceph-<id> \
  --op update-mon-db --mon-store-path /tmp/mon-store
# then rebuild:
ceph-monstore-tool /tmp/mon-store rebuild -- \
  --keyring /etc/ceph/ceph.client.admin.keyring --mon-ids a b c
```
Caveat: the rebuild does **not** recover the CephFS FSMap or non-OSD keyrings — CephFS needs
separate recovery (recreate FS with the recovery flag, set joinable, reapply
`standby_count_wanted`). Since this cluster uses CephFS RWX, pre-stage that runbook.
Sources: docs.ceph.com `/rados/troubleshooting/troubleshooting-mon/` +
`/cephfs/recover-fs-after-mon-store-loss/`.

---

## References

- [`ceph-performance-review.md`](./ceph-performance-review.md) — 2026-06-01 deep review & rationale
- [`hardware-incidents.md`](./hardware-incidents.md) — hardware failure log (incl. SN770M firmware fix)
- [`ceph/`](./ceph/) — toolbox, PG, backup-recovery notes
- Deep-research pass (2026-06-14): ceph-csi mounter docs, Ceph mClock/PG/lease/mon docs,
  Talos `siderolabs/pkgs` kernel config. Talos blog (oneuptime 2026-03-03) loads only the
  `rbd` module + a `/var/lib/rook` rshared bind-mount — note `rbd` is **built-in (`=y`) on
  Talos 1.13**, so that `machine.kernel.modules` entry is unnecessary on this version.
