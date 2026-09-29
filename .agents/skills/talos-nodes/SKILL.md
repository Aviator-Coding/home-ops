---
name: talos-nodes
description: "Read before editing talos/*.j2, running any just talos recipe, changing kernel args, extensions or Talos/Kubernetes versions, touching tuppr TalosUpgrade or KubernetesUpgrade CRs, rebooting a node, or planning power work. Covers apply-node vs upgrade-node, template pin drift that downgrades nodes, the pre-reboot Ceph gate, never shutdown-node --force, and the talos-3 B70 dock order."
---

# Talos nodes: machine config, upgrades and planned power work

Talos files are not applied by Flux. Merging a template change runs a schema-only CI check. An operator with a live `talosconfig` still has to apply or upgrade each node. `talosctl validate` does not catch bad enums, bad CIDRs, or a missing install disk.

## Tripwires

1. **`apply-node` is not `upgrade-node`.** `apply-node` stages machine config. That is enough for `machineconfig.yaml.j2` and `nodes/*.yaml.j2`. Kernel args and system extensions live in the Image Factory schematic, so `schematic.yaml.j2` changes boot only via `upgrade-node` (`talosctl upgrade -i <image> -m powercycle`). An apply-only schematic edit does nothing.
2. **The six image pins in `machineconfig.yaml.j2` are a second copy of the tuppr versions.** Installer, kubelet, apiserver, controller-manager, proxy, scheduler. A Renovate bump of the template does not upgrade the cluster. A tuppr upgrade does not edit the template. Applying a stale template downgrades a node. Before `apply-node`, match the template to `kubectl get nodes -o wide` and the tuppr CRs. `scripts/ci/version-consistency.sh` counts exactly six image pins: never put an image reference in a comment.
3. **A merged bump of `system-upgrade/tuppr/upgrades/talosupgrade.yaml` is an unattended powercycle roll.** tuppr consumes that CR directly (`rebootMode: powercycle`). Its `healthChecks` gate on VolSync `ReplicationSource` `Synchronizing=False` and `CephCluster` `HEALTH_OK`. They do not look at kopiur. With VolSync left on three claims, a kopiur-only failure does not block the roll. Treat that file like a live upgrade. Skill `renovate`.
4. **Before any node reboot** (`upgrade-node`, `reboot-node`, `reset-node`, or an OSD roll): `ceph status` is `HEALTH_OK` and `task rook:check-osd-device-paths` is clean. Rook #17224 bakes unstable `/dev/nvmeXn1` names into OSD deployments. One node at a time, `HEALTH_OK` between nodes. `osdMaxUpdatesInParallel` is 1. Skill `rook-ceph`.
5. **Never `just talos shutdown-node` for planned power work.** The recipe hard-codes `talosctl shutdown --force`, which skips cordon and drain. Use plain `talosctl shutdown`. Runbook: [power-down-up.md](references/power-down-up.md).
6. **talos-3's Arc Pro B70 dock has its own PSU.** A host powercycle does not cycle it (`SLTCAP PowerController=0`). `pcie_port_pm=off` in the schematic prevents the root port from runtime-suspending to D3hot; it is not retroactive and does not replace dock-PSU-first power-on. Runbook: [talos-3-b70-reboot.md](references/talos-3-b70-reboot.md).
7. **`domains: []` must be explicit** in the machine config. A nil domains list keeps the DHCP search domain. `forwardKubeDNSToHost: true` requires Cilium `socketLB.hostNamespaceOnly`. MGLRU is disabled via `machine.sysfs` (`kernel/mm/lru_gen/enabled: "0"`), not a kernel arg. The `kube-system/mglru-disable` DaemonSet re-asserts the same sysfs every 300s; do not delete either until a reboot proves the other holds. Skill `system-namespaces`.
8. **`features.kubernetesTalosAPIAccess.allowedKubernetesNamespaces` includes `system-upgrade` and `actions-runner-system`.** That allowlist is what lets tuppr (`os:admin`) and the runner (`os:operator`, the least role that can `talosctl image pull`) reach the Talos API. Moving tuppr out of `system-upgrade` needs `apply-node` on all three nodes first. Skill `system-namespaces`.
9. **Node names, not the VIP.** `talos-1|talos-2|talos-3` are `10.10.10.11/12/13`. Never target `10.10.10.10`. Roll order for planned work is talos-3, then talos-2, then talos-1, unless the power-down runbook says otherwise (it shuts talos-3 down last).

## Where things live

| What | Path |
|---|---|
| Shared machine config | `talos/machineconfig.yaml.j2` |
| Per-node overlays (disk, hostname, talos-3 taint) | `talos/nodes/talos-N.yaml.j2` |
| Kernel args and extensions | `talos/schematic.yaml.j2` |
| Recipes | `talos/mod.just` (`just talos …`) |
| Live upgrade CRs | `kubernetes/apps/base/system-upgrade/tuppr/upgrades/talosupgrade.yaml`, `kubernetesupgrade.yaml` |
| talos-3 placement | skill `node-scheduling` |
| Offline pointer (not auto-loaded) | `talos/AGENTS.md` |

Secrets are `ref+op://Home-Lab/talos/*` via `vals`. Never put key material in a `*.j2` file. `talos/talosconfig` is gitignored and absent in a fresh worktree.

## Procedures

- Render, validate, apply versus upgrade, pin check, node roll: [operations.md](references/operations.md).
- Whole-cluster power-down and power-on: [power-down-up.md](references/power-down-up.md).
- talos-3 B70 reboot and missing-card recovery: [talos-3-b70-reboot.md](references/talos-3-b70-reboot.md).
- Schematic args, B70, bonds, what the incident log still means: [hardware.md](references/hardware.md).

## Verify

- Offline: `just talos render-config talos-1` needs `vals` and a `talosconfig`. Without them, render with `minijinja-cli`, substitute dummy base64 for `ref+op://` refs, then `talosctl machineconfig patch` and `talosctl validate -m metal -c <file>`. `just talos` itself still needs some `TALOSCONFIG` because `bootstrap/mod.just` calls `talosctl config info` at load.
- CI: `.github/workflows/validate.yaml` schema gate, plus `scripts/ci/version-consistency.sh` and `scripts/ci/schematic-pcie-port-pm-test.py`. Green validate is not permission to `apply-node`.
- Live, before apply: `kubectl get nodes -o wide` against the template pins. After a talos-3 reboot: `devic.es/b70` allocatable is 99 on talos-3 and 0 on the others.
