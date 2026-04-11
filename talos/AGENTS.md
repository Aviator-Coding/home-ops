# TALOS LINUX CONFIGURATION

## OVERVIEW

Talos Linux node configuration for 3-node k8s cluster. `talconfig.yaml` is the single source of truth — all other configs are generated.

## STRUCTURE

```
talos/
├── talconfig.yaml          # SOURCE OF TRUTH — all node definitions
├── talenv.yaml             # Talos + Kubernetes version pins
├── schematic.yaml          # Factory schematic (system extensions)
├── talsecret.sops.yaml     # SOPS-encrypted cluster secrets (full-file encryption)
├── patches/
│   ├── global/             # Applied to ALL nodes
│   │   ├── machine-features.yaml
│   │   ├── machine-files.yaml
│   │   ├── machine-kubelet.yaml
│   │   ├── machine-network.yaml
│   │   ├── machine-sysctls.yaml
│   │   ├── machine-time.yaml
│   │   └── machine-udev.yaml
│   └── controller/         # Applied to controller nodes only
└── clusterconfig/          # ⚠️ GENERATED — never edit manually
```

## WHERE TO LOOK

| Task | File | Notes |
|------|------|-------|
| Add/modify node | `talconfig.yaml` | Node IPs, roles, disk assignments |
| Change Talos version | `talenv.yaml` | `talosVersion` field |
| Change k8s version | `talenv.yaml` | `kubernetesVersion` field |
| Add system extension | `schematic.yaml` | Factory schematic ID |
| Network config (all nodes) | `patches/global/machine-network.yaml` | Bond, VLAN, MTU settings |
| Kubelet config | `patches/global/machine-kubelet.yaml` | Node-level k8s settings |
| Kernel params | `patches/global/machine-sysctls.yaml` | Sysctl overrides |
| Controller-specific | `patches/controller/` | Control plane patches |

## WORKFLOW

```bash
# 1. Edit talconfig.yaml or patches
# 2. Regenerate configs
task talos:generate-config

# 3. Apply to specific node
task talos:apply-node IP=10.10.10.11 MODE=auto

# 4. Upgrade Talos version (after updating talenv.yaml)
task talos:upgrade-node IP=10.10.10.11

# 5. Upgrade Kubernetes version
task talos:upgrade-k8s
```

## ANTI-PATTERNS

- **NEVER** edit files in `clusterconfig/` — always edit `talconfig.yaml` then regenerate
- **NEVER** decrypt `talsecret.sops.yaml` and commit unencrypted
- SOPS uses **full-file encryption** for talos (unlike bootstrap/kubernetes which encrypt only `data`/`stringData`)

## NOTES

- 3 nodes: bonded interfaces (802.3ad LACP), MTU 9000, VLANs 3 and 90
- Control plane VIP: `10.10.10.10`
- Each node has 2 NVMe disks dedicated to Ceph OSDs
- `talosconfig` path: `talos/clusterconfig/talosconfig` (gitignored)
