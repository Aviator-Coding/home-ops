# Ceph Performance Review & Improvement Plan

**Date:** 2026-06-01
**Cluster:** `6562d9b0-883a-4e55-8b5d-899eaa7e0d10` — Rook-Ceph v1.19.6, Ceph **v20.2.1 (tentacle)**, 3 nodes (talos-1/2/3), 6 OSDs, all NVMe.
**Trigger for this review:** a multi-hour `HEALTH_WARN` slow-ops / `laggy` PG incident that wedged CephFS and RBD clients (sabnzbd stuck unkillable in `Terminating`).

> Scope note: settings referenced below live in
> `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml`. Changes must go
> through that file (GitOps), not ad-hoc `ceph config set`, or Rook will revert them.

---

## 1. Executive summary

The cluster is **all-NVMe but built on consumer SSDs with no power-loss protection (PLP)**, and several config choices actively work against Ceph's write path on such drives. The acute incident was **not** a hardware failure (SMART clean, no PCIe/NVMe kernel errors) and **not** resource starvation (all daemons well under limits, no OOM). It is a **storage-latency/lease cascade**: one DRAM-less OSD (osd.4) got slow at the BlueStore layer, could not renew PG read leases, PGs went `laggy`, and ops piled up "waiting for readable" — 607 ops blocked >60 min with essentially **zero client I/O**.

The five highest-impact levers, in order:

1. **Consumer NVMe without PLP** (fundamental — hardware).
2. **`bluestore_compression_mode: aggressive` (zstd) on every pool** — CPU + write-latency tax, near-useless on already-compressed media.
3. **`bluestore_cache_size: 2 GB` hard-set** — silently disables `osd_memory_target` autotuning; OSDs run a small fixed cache despite an 8 GiB limit.
4. **Severely low PG counts** (CephFS data = 16 PGs, block pool = 8) — poor write parallelism, hotspots, lease contention.
5. **No dedicated cluster/replication network** — 3× replication traffic shares the client path.

---

## 2. Hardware / drive inventory

| OSD | Node | Device | Model | Size | DRAM | PLP |
|-----|------|--------|-------|------|------|-----|
| osd.0 | talos-1 | nvme1n1 | Samsung 980 PRO | 2 TB | yes | **no** |
| osd.3 | talos-1 | nvme0n1 | Lexar NM790 | 4 TB | **no (HMB)** | **no** |
| osd.5 | talos-2 | nvme0n1 | Lexar NM790 | 4 TB | **no (HMB)** | **no** |
| osd.6 | talos-2 | nvme1n1 | Samsung 990 PRO | 2 TB | yes | **no** |
| osd.2 | talos-3 | nvme0n1 | Samsung 980 PRO | 2 TB | yes | **no** |
| osd.4 | talos-3 | nvme1n1 | Lexar NM790 | 4 TB | **no (HMB)** | **no** |

All six are **client/consumer** drives. None have power-loss protection. The three Lexar NM790 are **DRAM-less** (rely on Host Memory Buffer) and, being the largest, carry the most PGs (76/72/69 vs 33/37/40 on the Samsungs). Capacity is balanced and low (≈14% used, 2.3 TiB / 17 TiB) — this is **not** a fullness problem.

---

## 3. "Where is RocksDB stored?" (the question raised)

**RocksDB (BlueStore's metadata DB) and the WAL are colocated on the same NVMe as the OSD data, on every OSD.** From `ceph osd metadata`:

```
bluefs_dedicated_db  = 0      # no separate DB device
bluefs_dedicated_wal = 0      # no separate WAL device
```

There is **no slower secondary device** in play, and there is **no `BLUEFS_SPILLOVER`**. For an all-NVMe cluster this colocated layout is the **correct, recommended** design — a dedicated DB/WAL device only helps when the data device is much slower (HDD/SATA). So RocksDB placement is *not* a bug here.

The relevant nuance is different: BlueStore's RocksDB **WAL commit** (`_kv_sync_thread → _txc_apply_kv`) issues **synchronous/flushed writes**. On a drive **without PLP**, a sync write cannot be acknowledged from the drive's volatile cache; it must reach NAND first. That makes RocksDB-commit latency the choke point under load — and it's exactly the path that asserted in the osd.0 crash (`ceph_assert(r == 0)` in `_txc_apply_kv`). So the issue isn't *where* RocksDB lives, it's *what kind of drive* it commits to.

---

## 4. Acute incident — root cause chain

Evidence gathered live:

- `ceph -s`: `HEALTH_WARN`, 607 slow ops, oldest blocked **3806 s (~63 min)**, daemons `[osd.0, osd.4, osd.5, mon.h]`; **client io ≈ 8.5 KiB/s read, 0 write**; 11 PGs `active+clean+laggy`.
- `ceph health detail`: `BLUESTORE_SLOW_OP_ALERT: osd.4`.
- `ceph tell osd.4 dump_blocked_ops`: **94 blocked ops, flag `waiting for readable`**, almost all on **pool 8 (`ceph-objectstore.rgw.log`)** — `fifo.get_meta`, `fifo.create_meta`, `rgw.user_usage_log_add`.
- Kernel logs (all 3 nodes): cascading `rbd: rbdN: encountered watch error: -107` (ENOTCONN) → `failed to unwatch: -110` (ETIMEDOUT) from ~03:41 onward; `libceph: osd0 down → up` at 23:52 (the earlier transient).
- No scrub/recovery running; SMART clean; no PCIe/NVMe kernel faults.

**Chain:** osd.4 (DRAM-less Lexar) slows at the BlueStore commit layer → it can't renew **PG read leases** in time → its PGs flip to `laggy` → all ops on those PGs block **"waiting for readable"** → RGW's continuous log/usage FIFO writes (pool 8) pile up and never drain → the backlog grows even though real client I/O is ~0 → kernel RBD clients across all nodes lose their watches (`-107/-110`) → dependent pods (sabnzbd) stall in uninterruptible I/O and can't even terminate.

It is a **latency-induced lease deadlock**, amplified by one slow consumer drive — not a failing disk.

---

## 5. Systemic findings (ranked, with fixes)

### F1 — Consumer NVMe without PLP *(fundamental; hardware)*
**Evidence:** drive table §2; BlueStore assert in `_kv_sync_thread`; `BLUESTORE_SLOW_OP_ALERT` on the DRAM-less Lexar; slow ops with zero client load.
**Impact:** Ceph commits RocksDB WAL with O_DSYNC. Without PLP the drive flushes to NAND on every commit → high sync-write latency. DRAM-less drives (Lexar NM790) are worst: no on-board mapping cache + an SLC-cache cliff under sustained writes → latency collapses, which is what tips an OSD into lease failure.
**Fix:** The single biggest lever is **enterprise/datacenter NVMe with PLP** (e.g. Samsung PM9A3, Micron 7450, Kioxia CD/CM, Intel/Solidigm D7). Priority: **retire the three DRAM-less Lexar NM790 first**. Until then, the config changes below reduce how often the drives are pushed into the bad regime.

### F2 — Aggressive zstd compression on every pool
**Evidence:** `helmrelease.yaml:55-57` (global) **and** per-pool `compression_mode: aggressive` on `ceph-blockpool`, `ceph-filesystem-data0`, and all RGW pools (lines 179-291); confirmed in `ceph osd pool ls detail`.
**Impact:** Every write is run through zstd before storage. The dominant data here is **already-compressed media** (x265/H.264 video) and RBD images; it rarely meets `compression_required_ratio 0.875`, so it's stored uncompressed anyway — the CPU and added write-path latency are spent for ~nothing. On consumer drives that extra latency matters.
**Fix:** Set `compression_mode: none` (or at most `passive`) on `ceph-filesystem-data0` and `ceph-blockpool`. Keep/treat RGW pools separately. Remove the global `bluestore_compression_*` and rely on per-pool settings.

### F3 — `bluestore_cache_size` overrides `osd_memory_target`
**Evidence:** `helmrelease.yaml:60` `bluestore_cache_size: "2147483648"`; `ceph config get osd osd_memory_target` = `4294967296` (4 GB) but **a non-zero `bluestore_cache_size` disables target-based autotuning**, so each OSD runs a fixed 2 GB cache. Pod limit is 8 GiB.
**Impact:** Smaller BlueStore cache → lower metadata/onode cache hit rate → more reads hit the slow drives; wasted headroom (≈6 GiB free per OSD).
**Fix:** **Remove** `bluestore_cache_size` and set `osd_memory_target` to ~6 GiB (within the 8 GiB limit). Let autotuning balance cache vs. heap.

### F4 — PG counts far too low / imbalanced
**Evidence:** `ceph osd pool ls detail` — `ceph-filesystem-data0` **pg_num 16**, `ceph-blockpool` **pg_num 8**; total **109 PGs across 16 pools** on 6 OSDs; `read_balance_score` up to 6.0 on small pools; PGs/OSD range 33-76.
**Impact:** Few PGs = limited write concurrency, large PGs, and lease/peering contention concentrated on a handful of PGs — exactly what made one slow OSD able to wedge whole pools. Target is ~100 PGs/OSD (~600 here).
**Fix:** Raise `pg_num`: `ceph-blockpool` → 128, `ceph-filesystem-data0` → 128, `ceph-filesystem-metadata` → 32. Either bump the autoscaler bias / `pg_num_min` or set explicitly. Apply gradually (autoscaler will split in steps).

### F5 — No dedicated cluster (replication) network
**Evidence:** `network.provider: host` (`helmrelease.yaml:107`); `public_network` / `cluster_network` both empty.
**Impact:** Client traffic and 3× replication/recovery traffic share one path. Tolerable on bonded LACP + MTU 9000, but during backfill/recovery the client path contends with replication — worsening latency exactly when the cluster is already stressed.
**Fix:** Optional/medium term — split replication onto a dedicated VLAN/CIDR (the nodes already have VLAN 3/90). Lower effort than hardware, real benefit during recovery.

### F6 — mClock scheduler + lease behavior
**Evidence:** Squid defaults to mClock; `osd_mclock_max_capacity_iops_ssd` auto-benchmarked at 49k-60k per OSD; the incident manifested as `laggy` PGs + `waiting for readable`.
**Impact:** mClock's auto-IOPS calibration on consumer drives can be optimistic; under a slow OSD the lease cascade is not throttled gracefully.
**Fix:** Review the mClock profile (`osd_mclock_profile`). For an interactive homelab, `high_client_ops` favors client latency over background work. Re-run/verify the IOPS calibration on the Lexar drives (their real sustained sync IOPS is far below the benchmarked burst number).

### F7 — RGW background load on the slow pools
**Evidence:** The wedged ops were RGW `rgw.log`/`usage` FIFO writes (pool 8); RGW runs 2 daemons.
**Impact:** RGW continuously writes log/usage objects even when idle. Those pools have only 8 PGs and landed on the slow OSD's acting set, so RGW's own bookkeeping became the visible victim and kept the backlog growing.
**Fix:** If the S3/object store isn't actively used, consider disabling RGW (removes constant background writes). Otherwise raise its log-pool PGs and trim usage logging.

### F8 — osd.0 transient abort (watch item)
**Evidence:** `ceph_assert(r == 0)` in `BlueStore::_txc_apply_kv` at 23:52; `osd0 down→up` in 14 s; SMART clean; not in `ceph crash ls` (crashcollector hadn't posted).
**Assessment:** A transient sync-write failure on a no-PLP drive under pressure, self-recovered. Not currently a fault, but a **recurrence risk** under the same load. Watch; it disappears once F1-F4 reduce sync-write pressure.

---

## 6. Prioritized action plan

### A. Immediate — clear the wedge (operational, not GitOps)
1. Break the lease deadlock by re-peering the stuck OSD: `ceph osd down osd.4` (it will re-peer; client io is ~0 so risk is low), or restart the osd.4 pod. Watch `ceph -s` for slow ops to drain and `laggy` to clear.
2. Pause/trim the sabnzbd queue to remove the sustained CephFS write trigger; once Ceph is responsive the stuck `Terminating` pod will reap.

### B. Short term — config via GitOps (`cluster/helmrelease.yaml`)
3. **Compression:** `compression_mode: none` on `ceph-filesystem-data0` + `ceph-blockpool`; drop the global `bluestore_compression_*`. *(Existing data stays as-is; only new writes change. Optional later: rewrite to decompress.)*
4. **Cache:** remove `bluestore_cache_size`; set `osd_memory_target: "6442450944"` (6 GiB).
5. **PGs:** raise `pg_num` (block 128, cephfs-data 128, cephfs-metadata 32).
6. **mClock:** set `osd_mclock_profile: high_client_ops` and re-validate IOPS calibration.

> After B, **benchmark before/after** (e.g. `rados bench` on a test pool, and `ceph osd perf` sampled over time) so the gains are measured, not assumed.

### C. Medium term — hardware & topology
7. **Replace the DRAM-less Lexar NM790 drives with PLP datacenter NVMe** (biggest single win). Do it one OSD at a time (`ceph osd out` → drain → replace → re-add) so redundancy is preserved.
8. Consider a dedicated replication network (VLAN 3/90).

---

## 7. Appendix — key evidence commands

```bash
# DB/WAL placement (RocksDB question)
ceph osd metadata <id> | grep -E 'bluefs_dedicated_(db|wal)|bdev_dev_node|device_ids'
# Live state / wedge
ceph -s ; ceph health detail
ceph tell osd.4 dump_blocked_ops            # flag: "waiting for readable", pool 8 rgw.log
# Tunables in effect
ceph config dump | grep -Ei 'compression|cache_size|memory_target|mclock'
ceph osd pool ls detail                     # pg_num, compression, read_balance_score
ceph osd df tree                            # PG distribution, util, omap/meta
# Drive health (clean — rules out failure)
ceph device ls ; ceph device get-health-metrics <DEVID>
# Kernel (no PCIe/NVMe faults; rbd watch timeouts -107/-110)
talosctl --talosconfig talos/talosconfig -n <ip> dmesg
```

---

### TL;DR
All-NVMe, but **consumer/no-PLP** drives + **aggressive compression** + a **2 GB cache cap** + **too few PGs** make the write path fragile. RocksDB is correctly colocated on the data NVMe — not the problem. Fix the config now (compression off on media/RBD, free the cache, raise PGs), then replace the DRAM-less Lexar drives with PLP datacenter NVMe for the durable win.
