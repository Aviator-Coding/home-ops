# Hardware Incident Log

Tracked hardware and infrastructure incidents across cluster nodes. Each entry documents root cause, evidence, and resolution for future reference.

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
