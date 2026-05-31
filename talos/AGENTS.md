# TALOS LINUX CONFIGURATION

## OVERVIEW

Talos Linux node configuration for a 3-node (all control-plane) k8s cluster.
Uses an onedr0p-style `just` render path: `minijinja-cli` templates rendered by
`just talos render-config` and patched with `talosctl machineconfig patch`, with
secrets injected from 1Password via `vals` (`ref+op://Home-Lab/talos/*`).

## STRUCTURE

```
talos/
├── machineconfig.yaml.j2   # SOURCE OF TRUTH — shared machine + cluster config (minijinja)
├── nodes/                  # Per-node overlays (machine.type, install disk, hostname)
│   ├── talos-1.yaml.j2
│   ├── talos-2.yaml.j2
│   └── talos-3.yaml.j2
├── schematic.yaml.j2       # Factory schematic template (kernel args + extensions)
└── mod.just                # `just talos` recipe module
```

> Secrets: cluster PKI/tokens live in 1Password (`Home-Lab/talos` item, 14 fields:
> `MACHINE_CA_CRT/KEY`, `MACHINE_TOKEN`, `CLUSTER_CA_CRT/KEY`, `CLUSTER_ID`,
> `CLUSTER_SECRET`, `CLUSTER_TOKEN`, `CLUSTER_AGGREGATORCA_CRT/KEY`,
> `CLUSTER_ETCD_CA_CRT/KEY`, `CLUSTER_SECRETBOXENCRYPTIONSECRET`,
> `CLUSTER_SERVICEACCOUNT_KEY`).

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Add/modify node | `nodes/talos-N.yaml.j2` | Machine type, install disk, hostname |
| Network / kubelet / sysctls | `machineconfig.yaml.j2` | Shared base config |
| Change Talos version | `machineconfig.yaml.j2` + 1Password | `machine.install.image` ref |
| Add system extension | `schematic.yaml.j2` | Factory schematic |

## WORKFLOW

```bash
# 1. Edit machineconfig.yaml.j2, nodes/talos-N.yaml.j2, or schematic.yaml.j2
# 2. Render + validate offline
just talos render-config talos-1 | talosctl validate -m metal -c /dev/stdin
# 3. Preview the diff against the live node, then apply
just talos apply-node talos-1 --dry-run
just talos apply-node talos-1
# 4. Upgrade Talos / Kubernetes
just talos upgrade-node talos-1
just talos upgrade-k8s v1.36.1
```

## ANTI-PATTERNS

- **NEVER** put plaintext secrets in `*.yaml.j2` — use `ref+op://Home-Lab/talos/*` references
- **ALWAYS** `--dry-run` an `apply-node` before a real apply, and roll one node at a time (talos-3 → talos-2 → talos-1), watching each rejoin etcd/CNI

## NOTES

- 3 nodes: bonded interfaces (802.3ad LACP), MTU 9000, VLANs 3 and 90
- Control plane VIP: `10.10.10.10`
- Each node has 2 NVMe disks dedicated to Ceph OSDs
- `talosconfig` path: `talos/talosconfig` (gitignored)
