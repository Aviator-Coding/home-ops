# Hardware facts still in force

The incident narratives live in git history. Procedures a human runs: [power-down-up.md](power-down-up.md) and [talos-3-b70-reboot.md](talos-3-b70-reboot.md).

## Schematic (cluster-wide)

`talos/schematic.yaml.j2` kernel args that must stay:

- `pcie_port_pm=off`. Disables PCIe port runtime PM so root port `00:01.0` cannot sit in D3hot while the B70 trains late. `pcie_aspm=off` is a different switch (link ASPM) and does not do this. The arg is not retroactive: the running boot keeps the old behavior until `upgrade-node`.
- `pci=assign-busses`. The B70's `e2ff` switch enumerates with no spare bus numbers behind `00:01.0`. NIC renames are safe because bonds select members via `LinkAliasConfig`.
- `xe.force_probe=a7a0` and `i915.force_probe=!a7a0`. The Raptor Lake iGPU (`8086:a7a0`) must be on xe. `siderolabs/i915` still ships `i915.ko`; without the negative probe, i915 grabs the iGPU.
- `siderolabs/thunderbolt` stays out of the extension list. It broke the early-boot NIC path. Do not re-add it.

Only talos-3 has the B70. The schematic is cluster-wide, so all three nodes get `pcie_port_pm=off`. Cost is a bit more idle power from ports staying in D0.

`scripts/ci/schematic-pcie-port-pm-test.py` pins `pcie_port_pm=off` in the schematic. Do not drop the arg to satisfy a tidy-up.

## B70 dock

OCuLink dock PSU is independent of the host (`SLTCAP PowerController=0`, `HotPlugCapable=0`). Host powercycle does not power-cycle the card. If the card is missing after a reboot: host fully off, dock PSU on, wait until fans spin and the LED is lit, then host on. Never host-first, never both at once.

After any talos-3 power cycle, confirm:

```bash
kubectl get nodes -o custom-columns='NAME:.metadata.name,B70:.status.allocatable.devic\.es/b70'
```

Expect 99 on talos-3 and `<none>` or 0 on talos-1 and talos-2. Device-plugin details: skill `intel-gpu`.

## What the incident index is for

| Entry | Still-true consequence |
|---|---|
| Undrained three-node power cut | `shutdown --force` skips drain. Prometheus and in-flight kopiur movers were the casualties. Use [power-down-up.md](power-down-up.md). |
| B70 missing after a talos-3 power cycle | Dock-PSU-first. `pcie_port_pm=off` is the preventive arg, already booted once successfully. The runbook is still the recovery if the card is absent. |
| OOMController kill storm | `OOMConfig` in the machine config is the codified trigger. Do not retune it from a desktop PSI default. OSD QoS stayed Burstable on purpose (skill `rook-ceph`). |
| talos-1 faulty SODIMM | RAM RMA is done (96 GB dual-channel on all three). Do not re-add the old `NotIn talos-1` affinities. |
| SN770M firmware HMB bug | Mon RocksDB corruption. Firmware 731150WD is the fixed revision. All-mon rebuild is in `RECOVERY-PROCEDURES.md`, not a backup restore. |
| B70 install bus exhaustion | `pci=assign-busses` is the fix. Leave it. |
| NAS DAC / iGPU GuC | Historical. Not a Talos template knob. |

Also still true:

- The OOMController trigger is the machine-config `OOMConfig`; a live `talosctl patch` does not survive re-render (`talos/machineconfig.yaml.j2`).
- cilium-agent stays Guaranteed so it is not the OOM victim (cilium HelmRelease). OSDs stay Burstable, request 12Gi, limit 14Gi (PR #1736).
- A brief blockpool `min_size=1` window was used twice during mon recovery and restored to 2 both times (`RECOVERY-PROCEDURES.md` section 7). Mon store disks are SN770M on firmware that closed the HMB bug (section 6).
- Node memory alerts: `kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts/node-memory-pressure.yaml`.

## Nodes

Three control-plane nodes, bonded 802.3ad, MTU 9000, VLANs 3 and 90. Each node has two NVMe disks for Ceph OSDs plus the SN770M system disk for the mon hostpath. Control-plane VIP `10.10.10.10` is not a `talosctl -n` target.
