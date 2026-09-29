# Render, apply, upgrade, roll

## Which command

| Edit | Command | Why |
|---|---|---|
| `machineconfig.yaml.j2`, `nodes/*.yaml.j2` | `just talos apply-node <node>` after `--dry-run` | Stages machine config. No new factory image. |
| `schematic.yaml.j2` (kernel args, extensions, Talos version baked into the image) | `just talos upgrade-node <node>` | Resolves a schematic id from `factory.talos.dev` and powercycles into that image. |
| Kubernetes version the cluster should run | tuppr `KubernetesUpgrade` CR, or `just talos upgrade-k8s <version>` when doing it by hand | The template pins are not the upgrade. |

`apply-node` restages the install image *reference*. It does not boot a new schematic. A schematic edit that is only applied leaves the node on the old kernel args.

Always `--dry-run` an apply first. One node at a time.

## Pin drift

`machineconfig.yaml.j2` pins, all Renovate-managed and independent of tuppr:

- `factory.talos.dev/installer/…:v1.14.1`
- `ghcr.io/siderolabs/kubelet:v1.36.5`
- `registry.k8s.io/kube-apiserver:v1.36.5`
- `registry.k8s.io/kube-controller-manager:v1.36.5`
- `registry.k8s.io/kube-proxy:v1.36.5`
- `registry.k8s.io/kube-scheduler:v1.36.5`

Live nodes at the last check: Talos v1.14.1, kubelet v1.36.5, matching `TalosUpgrade` `spec.talos.version: v1.14.1`. Re-check both sides before every apply. `version-consistency.sh` greps for exactly these six image lines. A seventh image-looking string in a comment fails the gate.

`TalosUpgrade` `healthChecks`:

- every VolSync `ReplicationSource` has `Synchronizing=False`
- `CephCluster` `status.ceph.health` is `HEALTH_OK`

No kopiur check. `policy.rebootMode: powercycle` is a hard reset, not a graceful shutdown. A version bump merged to `main` is the upgrade.

## Node roll

Planned reboot or upgrade, one node at a time. Default order talos-3, talos-2, talos-1 so the GPU node is the attended one and the last two rebuild quorum around it. Power-off of the whole cluster is the opposite order: shut talos-3 down last (`docs/runbooks/power-down-up.md`).

Before the first node:

```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status   # HEALTH_OK, 6/6 OSDs
task rook:check-osd-device-paths
```

Wait for `HEALTH_OK` again before the next node. If an OSD stays `Init`, stop and follow `docs/ceph/osd-device-path-recovery.md`. Do not reboot the other nodes to "unstick" it.

talos-3 additionally: dock PSU first on the way back up, then the host. After it is Ready, `devic.es/b70` allocatable is 99. Details: `docs/runbooks/talos-3-b70-reboot.md`.

## Offline render without 1Password

`just talos render-config` pipes through `vals`. A worktree with no token can still schema-check:

```bash
minijinja-cli talos/machineconfig.yaml.j2 --format yaml \
  | talosctl machineconfig patch -p @talos/nodes/talos-1.yaml.j2 -o rendered.yaml
talosctl validate -m metal -c rendered.yaml
```

Substitute dummy base64 for any unresolved `ref+op://` before validate. `just` itself still wants a `TALOSCONFIG` file, even a fake one, because the bootstrap module runs `talosctl config info` when the root justfile loads.

## Machine-config invariants

- `features.kubePrism` / host DNS: `forwardKubeDNSToHost: true` only works with Cilium `socketLB.hostNamespaceOnly`. Turning either off alone black-holes host DNS.
- `machine.network.searchDomains` equivalent: an explicit empty `domains: []` replaces DHCP search domains. Omitting the key keeps `sklab.dev` in the search list.
- `allowedKubernetesNamespaces`: `actions-runner-system` (`os:operator`) and `system-upgrade` (`os:admin` for tuppr). Adding a namespace here is a node apply, not a Flux change.
- `OOMConfig` in this file is the codified PSI killer. A live `talosctl patch mc` does not survive the next render. Edit the template.
- `imageGCHighThresholdPercent` / `imageGCLowThresholdPercent` stay 60 / 45. The kubelet defaults (85 / 80) let unused image layers fill `/var` on the mon disk.
- Bonds: 802.3ad, MTU 9000, VLANs 3 and 90. Install disks are per-node in `nodes/`. Link names that must survive `pci=assign-busses` use `LinkAliasConfig`, not a guessed `ethN`.
- talos-3 taint `home-operations.com/dedicated: PreferNoSchedule` is in `nodes/talos-3.yaml.j2` and is inert until `apply-node talos-3`. Scheduling meaning: skill `node-scheduling`.

## Bootstrap

`just bootstrap cluster` is disaster recovery, not a reconcile. It `kubectl apply`s and `helmfile sync`s. Never run it against a healthy cluster. Human steps: [bootstrap/README.md](../../../../bootstrap/README.md).
