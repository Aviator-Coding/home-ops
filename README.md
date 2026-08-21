# home-ops

GitOps repository for a 3-node Talos Linux Kubernetes cluster, continuously reconciled by Flux v2.

This is a live cluster, not an upstream template. There is no Makejinja render step, no `cluster.yaml` / `nodes.yaml`, and no `task init` / `task configure` / `task bootstrap:talos` workflow. Operator commands live in the files listed below.

## Stack

- **OS:** Talos Linux (3 control-plane nodes, all schedulable)
- **GitOps:** Flux v2
- **CNI:** Cilium (native routing, kube-proxy replacement, BGP advertising LoadBalancer IPs; L2 announcements disabled)
- **Storage:** Rook-Ceph (`ceph-block` RWO, `ceph-filesystem` RWX) plus `openebs-hostpath` for local volumes
- **Secrets:** 1Password + External Secrets Operator. Bootstrap and Talos secrets are injected by `vals` from vault `Home-Lab`
- **Ingress:** Gateway API (`envoy-internal` / `envoy-external` in `network`) plus Cloudflare Tunnel
- **DNS:** External-DNS to Cloudflare (public) and the Unifi webhook (`network/unifi-dns`, internal)
- **Backup:** Volsync to local Ceph S3, NAS MinIO, and Cloudflare R2

## Operator docs

| Topic | File |
|---|---|
| Day-to-day commands, app layout, secrets, Volsync | [`CLAUDE.md`](CLAUDE.md) |
| Talos templates, render/apply/upgrade | [`talos/AGENTS.md`](talos/AGENTS.md) |
| First-time / disaster-recovery bootstrap | [`bootstrap/AGENTS.md`](bootstrap/AGENTS.md) |
| Agent-facing conventions and anti-patterns | [`AGENTS.md`](AGENTS.md) |
| Docs index | [`docs/reference.md`](docs/reference.md) |

## Layout

```
.
├── kubernetes/
│   ├── apps/           # Flux apps, one directory per namespace
│   ├── components/     # Reusable Kustomize components (alerts, common, dragonfly, volsync)
│   └── flux/           # cluster-meta / cluster-apps entry plus Helm/OCI sources
├── talos/              # minijinja machine config, node overlays, factory schematic
├── bootstrap/          # just bootstrap stages (nodes, k8s, base, apps)
├── .taskfiles/         # task recipes (flux, rook, k8s, network, 1password, actions-runner)
├── docs/               # runbooks and incident history
└── .mise.toml          # workstation tool versions
```

## Workstation

```bash
mise trust
mise install
task setup-dev-env          # pre-commit hooks
```

1Password auth for Talos render and bootstrap is `OP_SERVICE_ACCOUNT_TOKEN` in gitignored `.secrets.env` (see `.secrets.env.example`) or an interactive `op signin`. The token must be scoped to the `Home-Lab` vault. `kubeconfig` and `talos/talosconfig` are also gitignored.

## Common commands

```bash
# Flux
task reconcile
task flux:test:ns NAMESPACE=monitoring

# Talos (node names, not IPs: talos-1|talos-2|talos-3)
just talos render-config talos-1
just talos apply-node talos-1 --dry-run
just talos apply-node talos-1
just talos upgrade-node talos-1
just talos upgrade-k8s v1.36.1

# Bootstrap is DR / first-time only. Never run against a healthy cluster.
just bootstrap cluster
```

Do not pass the control-plane VIP `10.10.10.10` as a node target. Node addresses are `10.10.10.11/12/13`. See [`talos/AGENTS.md`](talos/AGENTS.md) and [`bootstrap/AGENTS.md`](bootstrap/AGENTS.md) for the full recipes and the DR warning.

## Adding an app

1. Create `kubernetes/apps/{namespace}/{app}/` with `ks.yaml` plus an `app/` directory.
2. Register `./{app}/ks.yaml` in the namespace `kustomization.yaml`.
3. Secrets go in `externalsecret.yaml` against ClusterSecretStore `onepassword`. Never commit plaintext or SOPS files.
4. Optional backups: add `spec.components: [../../../../components/volsync]`, `dependsOn: volsync` (namespace `system`), and `VOLSYNC_*` substitute keys on the Flux `ks.yaml`. Example: `kubernetes/apps/media/immich/ks.yaml`.

Flux reconciles from Git. After a merge, `task reconcile` forces a sync.

## Renovate

Renovate opens PRs for container images, Helm charts, GitHub Actions, and mise tools. Config is [`.renovaterc.json5`](.renovaterc.json5).
