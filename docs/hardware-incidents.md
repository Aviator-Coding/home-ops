# Hardware incident log

Procedures: [`runbooks/power-down-up.md`](runbooks/power-down-up.md), [`runbooks/talos-3-b70-reboot.md`](runbooks/talos-3-b70-reboot.md). Node apply and upgrade: skill `talos-nodes`. Git history is the incident archive.

| What is still true | Where |
|---|---|
| Planned power-off uses plain `talosctl shutdown`. `just talos shutdown-node` is `--force` and skips drain. Shut talos-3 down last. Dock PSU first on the way up. | [power-down-up](runbooks/power-down-up.md) |
| talos-3 B70 dock PSU is not cycled by a host powercycle. `pcie_port_pm=off` prevents the root-port D3hot race and does not replace that order. | [#1487](https://github.com/Aviator-Coding/home-ops/pull/1487), [B70 reboot](runbooks/talos-3-b70-reboot.md) |
| `pcie_aspm=off` does not cover port runtime PM. | schematic `talos/schematic.yaml.j2` |
| Do not re-add the `siderolabs/thunderbolt` extension. | schematic |
| `pci=assign-busses` stays. Bonds use `LinkAliasConfig`, not `ethN`. | `talos/nodes/*.yaml.j2` |
| OOMController trigger is the machine-config `OOMConfig`. A live `talosctl patch` does not survive re-render. Do not retune it from a desktop PSI default. | `talos/machineconfig.yaml.j2` |
| cilium-agent stays Guaranteed so it is not the OOM victim. | cilium HelmRelease |
| talos-1 `NotIn` affinities were reverted after the RAM RMA. Do not re-add them. | [#1393](https://github.com/Aviator-Coding/home-ops/pull/1393) |
| OSDs stay Burstable. Request 12Gi, limit 14Gi. | [#1736](https://github.com/Aviator-Coding/home-ops/pull/1736), skill `rook-ceph` |
| Mon store disks are SN770M on firmware that closed the HMB bug. All-mon rebuild is not a backup restore. | `kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md` section 6 |
| A brief blockpool `min_size=1` window was used twice and restored to 2 both times. | same file, section 7 |
| Node memory alerts live in `kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts/node-memory-pressure.yaml`. | monitoring |
