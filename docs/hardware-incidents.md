# Hardware Incident Log

Tracked hardware and infrastructure incidents across cluster nodes. Each entry documents root cause, evidence, and resolution for future reference.

---

## [2026-06-30] talos-1 + talos-2 — OOMController kill storm → 4-OSD outage / 100% PG inactive

| Field | Value |
|-------|-------|
| **Node** | talos-1 (10.10.10.11) + talos-2 (10.10.10.12) at onset; the 2026-07-03 recovery-day kill storms hit **ALL 3 nodes** |
| **Component** | **Talos v1.12 userspace OOM controller (`runtime.OOMController`)** — default PSI trigger far too aggressive for Ceph-hosting nodes. **NOT hardware**: RAM/disks/stores exonerated (zero corruption signatures through ~6 kill/restart cycles + reboots) |
| **Affected service** | 4/6 OSDs down (osd.0 + osd.3 on talos-1; osd.5 + osd.6 on talos-2) → **100% PGs inactive** (393/393, 66.667% objects degraded) → ALL RBD/CephFS client IO frozen ~3 days; MDS metadata IO blocked >37 h; collateral kills of cilium-agent, kube-apiserver, radarr; cluster-wide kubelet/D-state wedge |
| **Severity** | **critical** — total storage outage for ~3 days, ran **UNDETECTED** (no alerts fired: node-exporter was disabled and VictoriaMetrics not deployed — monitoring was blind to node memory/PSI and to the OSD pod deaths) |

### Timeline (UTC)

- **2026-06-30 ~17:07** — simultaneous OOMController kill activity on talos-1 + talos-2. osd.0 and osd.3 (talos-1) exit 137 at 17:08:26–28Z; talos-2 destabilized (osd.6 fast-shutdown on 07-01; osd.5 wedged `Init:0/5` for 2d21h).
- **06-30 → 07-03** — **100% PGs inactive** (347 undersized+degraded+peered + 46 undersized+peered), MDS slow metadata IO blocked >134,000 s (~37 h). Nobody noticed: no node metrics, no alerts, VictoriaMetrics scrapes died with the storage.
- **07-03 ~13:30–13:38** — recovery starts: `noout` set; talos-1 emergency load shed (crash-loopers scaled to 0, dead pods purged — see `talos1-shed-load` record); last pre-shed OOMController kill 13:36:13Z (cilium-agent ×4 → ~40 s VIP outage).
- **07-03 afternoon** — osd.3/osd.0 pod restarts hit the **RBD/activate circular deadlock**: ceph-volume device scan parked in kernel D-state on frozen `/dev/rbdX` devices (kernel-proven fd/stack evidence) — OSDs can't start because storage is dead, storage is dead because OSDs can't start. A **brief blockpool `min_size=1`** window broke the loop; 4 OSDs came up briefly.
- **07-03 14:46–15:03** — **OOMController kill storms on ALL 3 nodes** (trigger fired ~every 1 s): victims = cilium-agent (talos-1/2), radarr (talos-3), kube-apiserver-talos-2; collateral = all 6 OSD pods crashed, EPERM device-cgroup errors + runc sandbox-race containerd damage.
- **07-03 15:04** — live `OOMConfig` patch (trigger `memory_full_avg10 > 50.0`, holdoff `30s`) applied to all 3 nodes via `talosctl patch mc` → **zero OOMController events from then on**.
- **07-03 15:04–~19:50** — cluster-wide kubelet wedge remained: talos-2 kubelet D-state in `Stopping` >1 h, SIGKILL-immune; even force-unmap of rbd devices blocked in-kernel waiting on the dead OSDs.
- **07-03 ~19:50** — **user-initiated full reboot of all 3 nodes** cleared all D-state/debris: **all 6 OSDs up in ~60 s** (on their baked device paths — no rook#17224 drift), all PGs active, `noout` unset ~20:15, **HEALTH_OK**; backfill of the 4.3% degraded remainder completed normally.
- **Aftermath** — external-secrets 2.7.0 + cert-manager v1.20.3 upgrades (frozen mid-outage) completed after the unfreeze; Flux fully reconciled: **122/122 Kustomizations + 103/103 HelmReleases Ready**.

### Root cause

**The Talos v1.12 OOMController default PSI trigger (`memory_full_avg10 > 12.0 && d_memory_full_avg10 > 0.0 && time_since_trigger > 500ms`) is far too aggressive for Ceph nodes.** Memory-PSI spikes are *normal* here (OSD peering/backfill, BlueStore cache churn, recovery IO); the controller answered them by SIGKILLing the heaviest **Burstable** cgroups. The victim-ranking expression never selects Guaranteed cgroups — so cilium-agent, the OSD pods, and kube-apiserver (all Burstable) were the perpetual victims, and every kill made the pressure worse.

Two amplifiers made the PSI spikes pathological:

1. **Dead-container memory debris** — after the 06-30 kills, ~24 GiB (talos-1) and ~70 GiB (talos-2) of reclaimable slab + reparented page cache from dead OSD containers stayed pinned in `kubepods/burstable`. MemFree sat at the watermark (~0.4–2 GiB) while MemAvailable showed 26–72 GiB; every allocation burst forced slow direct reclaim through the debris → PSI `full avg10` 18–46% → trigger fired ~every second (the 07-03 storms).
2. **talos-1's reduced-RAM window** (48 GB single stick until the RAM RMA, ~2026-08): least headroom, first to trip; its kube-apiserver (646 restarts) and cilium-agent (503 restarts) were chronic victims.

**RULED OUT:** MGLRU regression (`/sys/kernel/mm/lru_gen/enabled = 0x0000` verified on all 3 nodes — PR #1094 held); rook#17224 device-path drift (never materialized — every OSD re-activated on its baked `ROOK_BLOCK_PATH`, including after the full reboots); store corruption / bad hardware (zero corruption signatures across all 6 OSDs through ~6 kill cycles + reboots; SMART/dmesg clean).

### Evidence

```
Default trigger:  memory_full_avg10 > 12.0 && d_memory_full_avg10 > 0.0 && time_since_trigger > 500ms
Victim ranking:   memory_max.hasValue() ? 0.0
                  : {Besteffort:1.0, Burstable:0.5, Guaranteed:0.0, Podruntime:0.0, System:0.0}[class]
                    * memory_current
                  -> Guaranteed cgroups are NEVER selected; big Burstable ones always are

Pre-recovery snapshot (2026-07-03 13:32 UTC):
  osd: 6 osds: 2 up (since 37h), 4 in — hosts talos-1 + talos-2 down
  pgs: 100.000% pgs not active; 393 inactive (347 undersized+degraded+peered)
  2060444/3090666 objects degraded (66.667%)
  MDS: slow metadata IOs blocked >30s, oldest blocked for 134362 secs (~37h)

talos-1 memory anatomy (07-03 13:35 UTC): TOTAL 47.9Gi, FREE ~500Mi, AVAILABLE 26Gi
  Inactive(file) 24.9Gi vs Cached 1.06Gi -> ~24Gi reparented dead-container cache pinned in
  kubepods/burstable (cgroup MemCurrent 30Gi vs live children ~6Gi); containerd RSS 9.5Gi
07-03 kill storms 14:46–15:03 (all 3 nodes): PSI full avg10 18–46% -> kills ~every 1s
  victims: cilium-agent (talos-1/2), radarr (talos-3), kube-apiserver-talos-2;
  collateral: all 6 OSD pods crashed (EPERM device-cgroup + runc sandbox-race damage)
Chronic talos-1 victims: kube-apiserver 646 restarts, cilium-agent 503 restarts
After OOMConfig patch (15:04, trigger 50%/30s): ZERO OOMController events through
  full recovery + backfill on all 3 nodes
```

### Impact

- **Total storage outage ~3 days** — 100% PGs inactive, every RBD/CephFS consumer frozen (dozens of pods in Init/Error/CrashLoop cluster-wide); MDS blocked >37 h.
- **No data loss, no corruption** — replica-3 held; backfill (4.3% degraded at reboot) completed normally to HEALTH_OK.
- **D-state avalanche**: kernel Ceph clients + dead cluster parked processes in uninterruptible D-state (ceph-volume scans, kubelet, sync); SIGKILL cannot reap D-state — talos-2's kubelet wedged in `Stopping` >1 h and even rbd force-unmap blocked in-kernel.
- Secondary damage from the kill storms: containerd state damage (EPERM device-cgroup, runc sandbox races), a ~40 s control-plane VIP outage (cilium kill), restart-counter churn in the hundreds.
- **Detection failure**: with node-exporter disabled and VictoriaMetrics not deployed, a total-storage outage produced zero alerts for ~3 days.

### Resolution

**Recovery (2026-07-03, executed):** talos-1 load shed → RBD/activate circular deadlock broken with a **brief blockpool `min_size=1`** window (used twice, blockpool only, **restored to `min_size=2`** both times) → live `OOMConfig` patch (trigger `memory_full_avg10 > 50.0 && d_memory_full_avg10 > 0.0 && time_since_trigger > duration("30s")`) applied 15:04 UTC to all 3 nodes → storms stopped → remaining D-state/kubelet wedge cleared by **user-initiated full reboot of all 3 nodes (~19:50 UTC)** → all 6 OSDs up in ~60 s, all PGs active, HEALTH_OK, backfill completed. `noout` set 13:30 → unset ~20:15 UTC (approx).

**Durable fixes (in GitOps):**

- **OOMConfig codified in `talos/machineconfig.yaml.j2`** (the live `talosctl patch mc` survives reboot but NOT a config re-render — codifying it was mandatory).
- **cilium-agent → Guaranteed QoS** (requests == limits) — ranking weight 0.0, network plane can no longer be the OOM victim.
- **`NotIn [talos-1]` node affinities on 7 heavy burstables** (coder, changedetection, rsshub, readarr, seerr, flaresolverr, emqx-exporter) — **TODO 2026-08: revert after the talos-1 RAM RMA restores dual-channel 96 GB**.
- **node-exporter re-enabled + PrometheusRule alerts**: `Talos1MemoryPressure`, `NodeMemoryPSIHigh`, `CephOsdPodTerminalError`, `CephPodCrashLooping` — closes the detection gap.
- **ceph-mon → Guaranteed QoS** (mon + logcollector requests == limits, 2Gi/500m) — mon quorum can no longer be the OOM victim. OSDs stay Burstable for now: Guaranteed would reserve 14Gi×2 on talos-1's 48 GB. **TODO 2026-08 (post-RMA): promote `resources.osd` to 14Gi==14Gi Guaranteed; evaluate MDS (blocked on `mds_cache_memory_limit` 8Gi — Guaranteed at 10Gi×4 pods is unaffordable, and a tighter limit risks cgroup OOM against the cache; standby-replay makes MDS kills tolerable meanwhile).**

Cross-reference: [`ceph-cluster-changelog.md` [2026-07-03]](./ceph-cluster-changelog.md) for the OOMConfig change record, the min_size=1 windows, and the noout window.

### Lessons

- **PSI-based userspace OOM killers must be tuned for storage nodes** — Ceph's normal peering/backfill/cache churn looks like "memory stall" to a 12% PSI trigger; defaults tuned for desktops/generic workloads will kill the storage daemons that are the *cause and cure* of the pressure.
- **Dead-container debris after mass kills poisons reclaim** — reparented page cache + slab pinned in `kubepods/burstable` keeps MemFree at the watermark, making every subsequent allocation a slow direct-reclaim → PSI spiral → more kills. Kill storms are self-amplifying.
- **Kernel Ceph clients + a dead cluster = D-state avalanche** — even force-unmap blocks in-kernel waiting on dead OSDs; SIGKILL is useless. Blocklisting or a node reboot are the only fences.
- **A monitoring gap let a total-storage outage run undetected for ~2+ days** — node metrics and OSD-pod-state alerts are not optional on a storage cluster.
- **Reboot-phobia proved unfounded** — the rook#17224 device-path fear had frozen node reboots for weeks, but fresh boots re-activated every OSD cleanly on the first try. A timely reboot would have shortened this incident dramatically.

---

## [2026-06-19] talos-1 — faulty DDR5 SODIMM (stuck data bit) → silent Ceph store corruption

| Field | Value |
|-------|-------|
| **Node** | talos-1 (10.10.10.11) — Meigao Venus, Raptor Lake i9-13900H, 96 GB (2× Crucial **CT48G56C46S5.M16B** 48 GB DDR5 SODIMM @ 5186 MT/s, x2 channel) |
| **Component** | **System memory** — one faulty 48 GB DDR5 SODIMM ("Stick B"; defective module, stuck bits 2 and 17 under different test patterns). **✅ ISOLATED 2026-06-20** by same-slot A/B swap — IMC + slot + the other stick ("Stick A") all cleared |
| **Affected service** | osd.0 (BlueStore store corruption, repeated) + mon.l (RocksDB store corruption) — any talos-1 Ceph daemon buffering through the bad region |
| **Severity** | **high** — confirmed hardware fault driving repeated silent corruption on talos-1; data contained by replica-3 + csum (no PG damage), but it triggered a mon-quorum scare and forces osd.0 held out |

### Root cause

**MemTest86 confirms a genuinely faulty DDR5 SODIMM on talos-1**, not a failing disk. Test 7 (Moving inversions, 32-bit pattern) failed with a **stuck data bit (bit 2 / `0x4`)** localized to a narrow physical band (~71 GB, `0x11C0`–`0x11DD`). A tight address range + single stuck bit = a defective cell/data line on **one specific stick**, not a scattered memory-controller fault. DDR5-5186 is *below* the 13900H's DDR5-5600 spec, so this is **not** an EXPO/overclock instability — it's a real defect.

This **resolves the osd.0 "bad RAM OR lying 980 PRO" dichotomy in favour of bad RAM**, and explains why the rebuilt osd.0 BlueStore re-corrupted under backfill writes (`Compaction sees out-of-order keys` → `BlueStore.cc:14648 r==0`) with **clean SMART and clean dmesg**: bad RAM flips bits in BlueStore/RocksDB buffers *before* they're written, so the SSD faithfully persists already-corrupt data — clean SMART, corrupt store. The same mechanism corrupted the talos-1 mon.l RocksDB store (rebuilt → mon.m on 2026-06-17). The 980 PRO (FW 5B2QGXA7) and in-band IBECC (ce/ue_count=0) reported clean precisely because the fault is in a region/path neither covers — IBECC counted 0 because the flipping bit was in a stick/region it doesn't scrub under that load.

### Evidence

```
PassMark MemTest86 V11.6 — 13th Gen Intel Core i9-13900H
Memory: 95.7 GB DDR5 5186 MT/s x2 Channel — Crucial CT48G56C46S5.M16B   RAM Temp 70°C
Test 7 [Moving inversions, 32-bit pattern] — FAIL, aborted at error cap, Errors: 10000

Test 7 Addr: 11DD0D80C0  Expected: 00040000  Actual: 00040004  CPU: 0    (bit 2: 0→1)
Test 7 Addr: 11C06D80C0  Expected: FFFBFFFF  Actual: FFFBFFFB  CPU: 13   (bit 2: 1→0)
Test 7 Addr: 11C05D07C0  Expected: FFFBFFFF  Actual: FFFBFFFB  CPU: 13
Test 7 Addr: 11C04D08C0  Expected: FFFBFFFF  Actual: FFFBFFFB  CPU: 13
```

Prior corruption signatures now attributed to this RAM (all SMART/dmesg-clean at the time):
```
osd.0 rebuild:  rocksdb Background IO error Corruption: Compaction sees out-of-order keys
                -> BlueStore.cc:14648 FAILED ceph_assert(r == 0)  (_txc_apply_kv)
mon.l:          rocksdb block checksum mismatch -> "failed to write to db" (rebuilt -> mon.m 2026-06-17)
```

### Impact

- **No PG/data damage** — every osd.0 corruption crash self-recovered with 0 inconsistent PGs (replica-3 + per-object csum contained it; the fault crashes osd.0's local KV, it never commits bad replicas).
- Triggered the 2026-06-17 cascade tail: talos-1 mon.l RocksDB corruption → fragile 2/3 quorum (recovered by rebuilding mon.l → mon.m).
- osd.0 must stay out / runs in a crash→recover stopgap; with osd.0 absent, talos-1's lone Lexar (osd.3) wedges the CephFS metadata pool (relief = `ceph osd down osd.3`).
- Repeated wasted rebuild/backfill churn on already-fragile consumer NVMe.

### Resolution

**✅ ISOLATED 2026-06-20 — confirmed single bad module ("Stick B"), board/slot/IMC clear.** Controlled same-slot A/B swap: **Stick A** ran 4 full passes, 0 errors in the reference slot (proven good); **Stick B** in that *same* slot failed Test 6 (Block move) in 7 min with stuck **bit 17** (`0x00020000`) across 6 threads at ~36 GB. Only the stick changed → Stick B is defective; the IMC and socket are exonerated (Stick A passed in that exact slot). The earlier differing signature (bit 2 @ ~71 GB dual-stick) was the same Stick B surfacing via channel interleave.

**Real fix = RMA Stick B** (Crucial CT48G56C46S5.M16B, lifetime warranty; advance/cross-ship if offered) and **run talos-1 on Stick A** (the 4-pass-clean module) in the meantime — 48 GB single-channel is ample for talos-1's storage/mon role (the big-RAM AI workloads are pinned to talos-3). Reinstall for dual-channel 96 GB when the replacement arrives. Node must be drained for the swap: `cordon` + `ceph osd set noout` → power off → reseat/replace → power on → uncordon → `unset noout` (one node, mind the [#17224](https://github.com/rook/rook/issues/17224) OSD device-path caveat on restart).

After the swap: **re-run MemTest86 to confirm clean**, then rebuild osd.0 on clean RAM (zap + uncomment disk in the CephCluster HR + provision) and run `ceph osd deep-scrub` across talos-1 OSDs to confirm no latent inconsistency. Until then: **keep osd.0 in the contained stopgap** (or fully out) and treat talos-1 as do-not-trust for write-heavy Ceph work.

**Interim (already in place):** mon.l rebuilt → mon.m; throttled-recovery mClock drift set live; MGLRU disabled cluster-wide (PR #997). These are mitigations for the *cascade*, not for the RAM — the RAM swap is the only durable fix for talos-1's corruption root.

---

## [2026-06-14] ALL 3 nodes — WD SN770M firmware (HMB) bug → silent Ceph mon RocksDB corruption

| Field | Value |
|-------|-------|
| **Node** | **ALL 3** (talos-1/2/3) — each has a WD_BLACK SN770M 1TB FW **731100WD** as its system/mon disk (talos-1 `nvme2n1`, talos-2 `nvme0n1`, talos-3 `nvme0n1`). talos-1 corrupted first. |
| **Component** | **WD_BLACK SN770M 1TB, FW 731100WD** — DRAM-less (uses HMB); holds `/var` + the openebs-hostpath Ceph mon stores on every node. (OSD disks are separate: Samsung 980/990 PRO, Lexar NM790 — not affected.) |
| **Affected service** | mon.k, mon.l on talos-1 (corrupted); mon.h (talos-3) + mon.i (talos-2) at equal risk |
| **Severity** | **high** → effectively critical: all 3 mon stores share the buggy firmware; a 2nd mon corruption = quorum loss = cluster down |

### Root cause

**Known WD_BLACK SN770/SN770M firmware bug (FW 731100WD), NOT a failing disk.** The SN770M is
DRAM-less and uses **HMB (Host Memory Buffer)** — host RAM for its flash-translation/mapping
tables. FW 731100WD has a documented HMB data-corruption bug (same family that caused Win11
24H2 BSODs; DRAM-less HMB drives are well-known to be unsuitable for DB/FS workloads like
RocksDB/ZFS). It returns corrupted data **silently under load** — which manifests as RocksDB
block checksum mismatches with **completely clean SMART** (NAND is fine; corruption is in the
HMB/FTL layer). Third occurrence on this node (see [2026-03-21] mon.j below, mis-logged as a
one-off "bit-flip"): mon.j → mon.k → mon.l, the last a fresh replacement on a fresh PVC that
corrupted within minutes. **Heavy IO triggers it**: the full-throttle CephFS migration rsync
wedged osd.0, and the kubelet imageGC change (PR #979) deleting ~370 GB off `/var` corrupted
mon.l. (osd.0 also had a BlueStore assert `_txc_apply_kv r==0` on 2026-06-02.)

**SMART (2026-06-14): CLEAN** — overall PASSED, Critical Warning 0x00, Media & Data Integrity
Errors **0**, Available Spare 100%, Percentage Used **3%**, temp 58°C (sensor1 72°C). No MCE /
EDAC / thermal / NVMe-reset events in dmesg. The clean SMART is what re-pointed the diagnosis
from "failing disk" to "firmware HMB bug." Refs: theregister.com/2024/10/17/western_digital_releases_a_firmware,
support-en.wd.com SN770M, github.com/openzfs/zfs/discussions/14793.

### Evidence

```
mon.l (fresh replacement, fresh PVC) crash-loop:
rocksdb: submit_common error: Corruption: block checksum mismatch:
  stored = 3754013901, computed = 4071487067, type = 4
  in /var/lib/ceph/mon/ceph-l/store.db/000245.sst offset 55621975 size 103686
MonitorDBStore::apply_transaction() -> ceph_abort_msg("failed to write to db")

mon.k earlier: ceph_abort_msg("failed to write to db") (MonitorDBStore.h:356), 49 restarts
osd.0 2026-06-02: BlueStore::_txc_apply_kv FAILED ceph_assert(r == 0) (bstore_kv_sync)
```

### Impact

- Ceph repeatedly degraded to `HEALTH_WARN`, 1/3 mons down, quorum held by mon.h (talos-3) + mon.i (talos-2)
- osd.0/4/5 stuck slow ops (768) wedged client/MDS IO 3× — cleared each time by `ceph osd set noout` + restart osd.0
- **No data loss** — all PGs `active+clean`, 3× replication intact, volumes healthy
- Reduced fault tolerance — only 2 working mons; a third mon cannot survive on talos-1

### Resolution

**✅ RESOLVED 2026-06-14** — all 3 nodes' SN770M flashed `731100WD` → **`731150WD`**, rolling one at a time (cordon → `ceph osd set noout` → reset-to-BIOS + flash → power on → uncordon → OSDs/mon rejoin → `unset noout` → recover). Confirmed working: mon.l ran 9 h stable on the patched firmware under real recovery load (it previously corrupted within minutes). All mon stores now on safe firmware; cluster can take heavy IO again.

Immediate (done): cleared slow ops (restart osd.0); reclaimed ~370 GB on `/var` (imageGC PR #979). Removed corrupt mon.k (`ceph mon remove` + delete PVC + patch `rook-ceph-mon-endpoints`); mon.l left crash-looping (recreating on the same buggy firmware is futile). **Real fix: update the SN770M firmware on ALL 3 NODES** 731100WD → latest (≥731120WD), **rolling — one node at a time** to preserve mon quorum (via WD Dashboard on Windows, or `nvme fw-download`/`fw-commit` from a Linux live USB; each node briefly offline). Interim mitigation: disable HMB for the drive. Until patched: **avoid heavy-IO operations on every node** (not just talos-1) — all 3 mon stores are on the buggy firmware and a 2nd mon corruption breaks quorum. After firmware is patched on a node, restore its mon via the clean recreate runbook. **Disk does NOT need replacing — SMART is healthy on all units.** (osd.0's slow-op wedges are on a different disk — likely a separate Ceph/BlueStore-under-load issue.)

---

## [2026-04-15] NAS (nas.sklab.dev) — failing DAC cable/PHY on Intel 82599ES port 2

| Field | Value |
|-------|-------|
| **Node** | NAS — `nas.sklab.dev` (10.10.0.40) — TrueNAS SCALE 25.04.2.6 |
| **Component** | Intel 82599ES 10G NIC (Huawei OEM, PCI `8086:10fb` subsys `19e5:d111`), port `enp1s0f1` (SFP+: 4) in LACP bond0 |
| **Affected service** | All NFS clients (3x talos nodes); entire media pipeline (Sonarr/Radarr/Tdarr/Jellyfin) |
| **Severity** | **high** — full NAS hangs; forced reboot required; K8s NFS mounts stall |

### Root cause

The DAC (Direct Attach Copper, twinax) cable on **port 2** of the NAS's dual-port 82599ES NIC is physically failing. The port flaps between up/down constantly, triggering `ixgbe` driver TX queue deadlocks (`Detected Tx Unit Hang`). Under heavy NFS load, the reset loop becomes so aggressive (hundreds of resets per minute on both bond members because the LACP driver shares TX across the aggregator) that the kernel can't make progress, `nfsd` threads pile up in D-state, memory pressure builds, and eventually systemd initiates a shutdown that fails to unmount the ZFS pools cleanly.

Initially suspected as a generic 82599ES + TSO driver bug (well-documented class of issue). Disabling TSO/GSO/GRO on both ports via `ethtool -K ... off` did **not** stop the tx_hangs, which pointed to a physical-layer cause rather than offload bug.

### Evidence

Port statistics showed massively asymmetric link stability:

| Metric | `enp1s0f0` (port 1, good) | `enp1s0f1` (port 2, bad) |
|--------|---------------------------|---------------------------|
| `lsc_int` (link state change interrupts) | 8 | **680** |
| Bond `Link Failure Count` | 3 | **437** |
| `tx_carrier_errors` counter exposed | no | yes |
| Flow control disable persisted | yes | no (port resets too fast) |

Kernel log pattern (characteristic signature):
```
ixgbe 0000:01:00.1 enp1s0f1: Detected Tx Unit Hang
  Tx Queue <N>  TDH, TDT <0>, <1>  next_to_use <1>  next_to_clean <0>
ixgbe 0000:01:00.1 enp1s0f1: tx hang N detected on queue N, resetting adapter
ixgbe 0000:01:00.1 enp1s0f1: initiating reset due to tx timeout
ixgbe 0000:01:00.1 enp1s0f1: primary disable timed out
bond0: (slave enp1s0f1): link status definitely down, disabling slave
```

This ran continuously from 18:20 to 19:16 on Apr 15 before the forced shutdown.

SFP+ module identification: both ports show `Copper pigtail / Passive Cable / Twin Axial Pair` — DAC cables, not optical modules. Same vendor/type in both ports.

### Impact

- Reboot loop: crashed Apr 14, crashed again Apr 15 (~36h apart, both during active Tdarr transcoding load)
- Prior 86 days uptime (Jan 18 → Apr 14), so this is a new failure — likely the cable or port physically degraded
- NFS stalls cascade into the K8s cluster: Tdarr copy-failed errors, Radarr import failures, Jellyfin playback issues

### Resolution (immediate, applied)

**1. Took failing port administratively down:**
```bash
ip link set enp1s0f1 down
```
Bond continues via `enp1s0f0` alone (10 Gbps instead of 20 Gbps aggregate — media-serving doesn't need more).

**2. Disabled TSO/GSO/GRO on both ports** (kept as belt-and-braces even after port issue identified — doesn't hurt):
```bash
ethtool -K enp1s0f0 tso off gso off gro off
ethtool -K enp1s0f1 tso off gso off gro off
```

**3. Disabled flow control on both ports** (known UniFi interaction):
```bash
ethtool -A enp1s0f0 autoneg off rx off tx off
ethtool -A enp1s0f1 autoneg off rx off tx off
```

**4. Made ethtool changes persistent via TrueNAS Init/Shutdown Scripts** (System Settings → Advanced → Init/Shutdown Scripts, `POSTINIT`, id=1 ethtool, id=2 swapon).

**5. Enabled 16 GiB swap on previously-unused `/dev/sda4`** — TrueNAS SCALE doesn't configure swap by default; this is an emergency pressure valve to prevent hard crashes under similar incidents.

### Resolution (follow-up, required)

- **Physically swap DAC cables between port 1 and port 2** to determine whether it's the cable or the NIC port itself that's failing:
  - If after swap, port 2 (same cable) goes clean and port 1 (now with ex-port 2 cable) starts flapping → cable is bad, replace the DAC cable
  - If port 2 keeps flapping with the good cable → NIC PHY/SFP+ cage is bad; permanently run single-link or replace the NIC
- **Replace bad component** (cable ~$10, NIC replacement options: Mellanox ConnectX-3/4 ~$25-50 used on eBay, or genuine Intel X520/X540 not Huawei OEM)
- **Re-enable the bond member** after physical repair: `ip link set enp1s0f1 up` and remove the port-down workaround

### Pattern observation

Unlike the Meigao Venus incidents (recurring firmware/PM races on identical hardware), this is a **discrete physical failure** on a specific port/cable. Not a pattern issue — replacement fixes it permanently. But it does highlight:

1. TrueNAS SCALE has no default swap — should add 16 GB swap on `/dev/sda4` on every install as standard practice (now done)
2. The 82599ES family has a deservedly poor reputation; the Huawei OEM rebrand is the worst variant. Prefer Mellanox for future NAS NICs
3. Heavy NFS load from the K8s cluster (Tdarr + media ingest) is the type of workload that now reliably exposes flaky NICs — consider `nconnect=2` NFS mount option to reduce per-socket pressure

---

## [2026-04-14] Intel iGPU GuC firmware init race — Meigao Venus PM instability (3rd subsystem)

| Field | Value |
|-------|-------|
| **Node** | talos-1 (10.10.10.11) initially, fix applied to all 3 nodes |
| **Component** | Intel UHD/Iris Xe iGPU (Raptor Lake i9-13900H, device id `0xa7a0`) |
| **Affected service** | Tdarr workers (HEVC transcoding), Jellyfin (hardware transcoding if scheduled to talos-1) |
| **Severity** | medium — feature degraded, no data risk |

### Root cause

Same Meigao Venus (AHWSA) board-level power management instability that previously caused the **NVMe APST/PCIe ASPM Ceph OSD crashes** (see project memory `project_talos_nvme_pcie_fix.md`, fixed 2026-03-16). This time the racy subsystem was the **Intel iGPU's display power gating** racing with **GuC (Graphics microController) firmware handshake** during boot.

When the i915 driver loaded, GuC firmware (`i915/adlp_guc_70.bin`, version 70.49.4) loaded but the microkernel never reached the `0xf0` (running) state — got stuck at `0x0`. Any subsequent attempt to use QSV or VAAPI hardware encoding via `iHD_drv_video.so` segfaulted deterministically at offset `0x1c87bc`.

The pattern matches the established Meigao board flakiness: aggressive default PM races with device init. We had already disabled PM for NVMe and external PCIe (`nvme_core.default_ps_max_latency_us=0`, `pcie_aspm=off`); this fix extends the same workaround to the iGPU's display power controller.

### Evidence

GuC status comparison across nodes (BEFORE fix):
```
talos-1: GuC status 0x800300ec   uKernel status = 0x0    ← stuck
talos-2: GuC status 0x8003f0ec   uKernel status = 0xf0   ← healthy
talos-3: GuC status 0x8005f0ec   uKernel status = 0xf0   ← healthy
```

ffmpeg QSV test on talos-1 → exit code 139 (SIGSEGV).

Kernel trap log full of identical-offset segfaults from every attempt to use the GPU:
```
traps: HandBrakeCLI[XXXX] general protection fault ip:XXXXXXXX7bc sp:XXXXXXXX
  error:0 in iHD_drv_video.so[1c87bc,XXXXXXXX+a11000]
```
Same offset (`0x1c87bc`) every time — deterministic software fault, not hardware corruption.

Tdarr encoder probe before/after on talos-1:
```
Before: h264_qsv-true-false,  hevc_qsv-true-false,  hevc_vaapi-true-false
After:  h264_qsv-true-true,   hevc_qsv-true-true,   hevc_vaapi-true-true
```

### Impact

- Tdarr DaemonSet running on talos-1 was useless for GPU work (driver crashed on every encode)
- Files transcoded successfully on talos-2/talos-3 but couldn't use the third GPU
- Jellyfin would have lost hardware transcoding if rescheduled to talos-1
- No data loss
- The earlier Tdarr V8 `VerifyChecksum(blob)` crash on talos-1 was a separate, one-off container layer corruption (resolved by pod delete) — not connected to this issue. Initially looked like generic hardware failure but ruled out by confirming no MCE/ECC events in dmesg, healthy temps (51°C), and identical hardware/firmware across nodes

### Resolution

Added kernel arg `i915.enable_dc=0` to `talos/schematic.yaml` (disables iGPU display controller power gating). New factory schematic ID: `ac2b7006014bfd57ed2ee6bce766bfe1d3a18f02e2a5f3a6fc4f5265c77e99ee`.

Rolled out via `task talos:upgrade-node` to all 3 nodes one at a time, waiting for Ceph to rebalance between each (kept it from going degraded). After the upgrade:
- talos-1 GuC reached `0xf0`, all encoders show `true-true`
- The pre-existing slow-OSD warning on talos-3's `osd.4` cleared after that node's reboot — likely the same BlueStore PM stall pattern
- All 3 nodes now have all known PM races disabled

```
NVMe:  nvme_core.default_ps_max_latency_us=0    (fixed 2026-03-16)
PCIe:  pcie_aspm=off                             (fixed 2026-03-16)
iGPU:  i915.enable_dc=0                          (fixed 2026-04-14)
```

### Pattern observation

Meigao Venus boards have aggressive default BIOS power management across **every** PM-managed subsystem. Each subsystem's driver has to win a race against PM kicking in. Linux often loses when probing fast. **Expect more surprises from this hardware over time** (USB controllers, display, audio) all fixable by similar `disable PM for X` kernel args. Consider proactively auditing other subsystems' PM behavior before they cause incidents.

---

## [2026-03-21] Ceph monitor mon.j crash loop — RocksDB store corruption on talos-1

| Field | Value |
|-------|-------|
| **Node** | talos-1 (10.10.10.11) |
| **Component** | Local storage (openebs-hostpath) |
| **Affected service** | mon.j (rook-ceph-mon-j) |
| **Severity** | high |

### Root cause

RocksDB SST file `154904.sst` in mon-j's store developed a block checksum mismatch, indicating silent data corruption on the underlying openebs-hostpath volume (`/var/openebs/local/pvc-df451fee-f19a-4568-9e50-891988442cab`). The corruption was detected during a compaction of L0 into L6. Once RocksDB flagged the background error, all subsequent writes were rejected, causing mon-j to abort on every sync attempt. Suspected cause is either an unclean node shutdown or a silent bit-flip on the local disk.

### Evidence

```
rocksdb: Corruption: block checksum mismatch: stored = 467716038, computed = 938189546, type = 4
  in /var/lib/ceph/mon/ceph-j/store.db/154904.sst offset 13748504 size 103348

rocksdb: submit_common error: Corruption: block checksum mismatch (same as above)
  Rocksdb transaction rejected — MonitorDBStore::apply_transaction() -> ceph_abort_msg("failed to write to db")

Crash backtrace: Monitor::sync_start -> apply_transaction -> failed to write to db -> abort
55+ restarts in CrashLoopBackOff over ~4.5 hours
```

### Impact

- Ceph cluster degraded to `HEALTH_WARN` — 1/3 mons down, quorum maintained by mon.h and mon.i
- Rook operator unable to schedule replacement mon-k due to host port conflicts (3300/6789 held by crashing mon-j pod)
- No data loss — all 6 OSDs healthy, all PGs active+clean
- Reduced fault tolerance — loss of one more mon would break quorum

### Resolution

Delete mon-j deployment and PVC to free host ports and remove corrupted store. The Rook operator will automatically create a replacement monitor that syncs a fresh monstore from the quorum.

```bash
kubectl -n rook-ceph delete deployment rook-ceph-mon-j
kubectl -n rook-ceph delete pvc rook-ceph-mon-j
```

---
