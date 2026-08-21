# PROJECT KNOWLEDGE BASE

Home-ops GitOps repo for a 3-node Talos Linux Kubernetes cluster managed by Flux v2. For operator command detail use [`CLAUDE.md`](CLAUDE.md), [`talos/AGENTS.md`](talos/AGENTS.md), and [`bootstrap/AGENTS.md`](bootstrap/AGENTS.md).

## STRUCTURE

```
.
├── kubernetes/
│   ├── apps/           # 19 namespaces, each with app subdirs
│   ├── components/     # alerts, common, dragonfly, volsync
│   └── flux/           # cluster ks.yaml entry + meta/repos (Helm/OCI sources)
├── talos/              # minijinja templates (see talos/AGENTS.md)
├── bootstrap/          # just bootstrap stages (see bootstrap/AGENTS.md)
├── .taskfiles/         # included: 1password, k8s, flux, rook, network, actions-runner
├── docs/               # runbooks, incident history, ceph/network notes
└── .renovate/          # Renovate presets
```

Gatus is an app under `kubernetes/apps/monitoring/gatus`, not a component. Endpoint wiring is the `gatus.home-operations.com/endpoint` annotation on HTTPRoutes.

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add new app | `kubernetes/apps/{namespace}/{app}/` | Pattern: `ks.yaml` + `app/` dir |
| Add app to namespace | `kubernetes/apps/{namespace}/kustomization.yaml` | Add `- ./{app}/ks.yaml` to resources |
| Enable backups | `kubernetes/apps/{ns}/{app}/ks.yaml` | Flux `spec.components` + `dependsOn: volsync` (namespace `system`) + `VOLSYNC_*` substitute keys. Example: `media/immich/ks.yaml` |
| App secrets | `kubernetes/apps/{ns}/{app}/app/externalsecret.yaml` | OnePassword via ClusterSecretStore `onepassword` |
| Bootstrap secrets | `bootstrap/kustomize/apps/security/` | `vals` injects `ref+op://Home-Lab/1password/*` |
| Flux entry point | `kubernetes/flux/cluster/ks.yaml` | `cluster-meta` -> `cluster-apps` dependency chain |
| Helm/OCI repos | `kubernetes/flux/meta/repos/` | 12 repo yaml files plus `kustomization.yaml` |
| Talos node config | `talos/machineconfig.yaml.j2` + `talos/nodes/*.yaml.j2` + `talos/schematic.yaml.j2` | Rendered by `just talos`, not Flux |
| Task commands | `Taskfile.yaml` + `.taskfiles/{domain}/` | `task --list`. Talos/bootstrap are `just`, not `task` |
| CI workflows | `.github/workflows/` | flux-local, renovate, codeql, image-pull, label-sync, plus build-talosctl-busybox, labeler, tag, test-runner |
| Renovate config | `.renovaterc.json5` + `.renovate/` | Extends from Aviator-Coding/mortyops + local presets |
| Tool versions | `.mise.toml` | kubectl, flux, talos, helm, vals, 1password-cli, just, minijinja, etc. |
| AI stack | `kubernetes/apps/ai/` | Hermes + ToolHive (`toolhive.stacklok.dev/v1alpha1` `MCPServer`) + agentgateway. kagent/kmcp tombstones: `docs/ai-system/{kagent,kmcp}` |

## CONVENTIONS

- **YAML schemas**: Every manifest starts with `# yaml-language-server: $schema=...` comment
- **ks.yaml anchors**: `name: &app myapp`, `namespace: &namespace myns` - referenced via `*app`, `*namespace`
- **Schema URL**: Use `kubernetes-schemas.pages.dev` - never `crd.movishell.pl` or `fluxcd-community`
- **Naming**: All lowercase, kebab-case dirs/files. `helmrelease.yaml`, `kustomization.yaml`, `ks.yaml`, `externalsecret.yaml`
- **Commit format**: `type(scope): description` - types: feat, fix, chore, ci, docs, refactor, test. Authoritative rules: `.commitlintrc.yaml` (commit-msg hook in `.pre-commit-config.yaml`)
- **Commit scopes**: container, helm, github-action, mise, talos, flux, deps, github-release, or app/namespace names
- **Gateway API**: `envoy-internal` (private) and `envoy-external` (public) in `network` namespace
- **DNS annotation**: `external-dns.alpha.kubernetes.io/target: "internal.${SECRET_DOMAIN}"` or `"external.${SECRET_DOMAIN}"`
- **Homepage annotations**: `gethomepage.dev/*` annotations on HTTPRoutes for dashboard integration
- **Gatus monitoring**: `gatus.home-operations.com/endpoint` annotation with conditions on HTTPRoutes
- **HelmRelease defaults**: Auto-patched by cluster-apps Kustomization - CRD CreateReplace, rollback recreate, upgrade remediation

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER** put plaintext secrets in Git. App secrets are ExternalSecret + 1Password only. Bootstrap/Talos secrets are `ref+op://Home-Lab/...` resolved by `vals`.
- **NEVER** add a helm `postRenderer: bash` (breaks on Helm 4)
- **NEVER** run `just bootstrap cluster` / `apps` against a healthy cluster. See `bootstrap/AGENTS.md`
- **NEVER** commit without pre-commit file/security hooks - `task setup-dev-env` to install them
- **DO NOT** store secrets in `**/resources/**` - Renovate ignores this path
- There is no SOPS/age path and no `talos/clusterconfig/` or `talos/talconfig.yaml`. Do not reintroduce them.
- **kagent / kmcp are not deployed** (removed 2026-06-07, #941/#942). Live AI stack is Hermes + ToolHive + agentgateway. `docs/ai-system/{kagent,kmcp}` are tombstones. ToolHive `MCPServer` is `toolhive.stacklok.dev/v1alpha1` - never kagent.dev's same kind name.

## UNIQUE STYLES

- **Flux variable substitution**: `postBuild.substituteFrom` references `cluster-secrets` Secret + inline `substitute` map
- **Component composition**: Namespace `kustomization.yaml` includes `../../components/common` + `../../components/alerts` as components
- **Volsync triple-backup**: Apps get 3 ReplicationSources (ceph/minio/r2) + 1 ReplicationDestination via a single component include on the Flux `ks.yaml` (`../../../../components/volsync`). Defaults: Ceph every 4h, MinIO every 6h, R2 daily at 02:00. Per-app cron offsets live on `ks.yaml` `VOLSYNC_SCHEDULE_*` (not unique across every app). Live app list and retain settings: `kubernetes/components/volsync/Readme.md`
- **VOLSYNC_CACHE_CAPACITY**: Must be sized 20-50% of PVC size - small PVCs need 50-100%
- **dependsOn chains**: Many apps use `dependsOn` in ks.yaml - typically `onepassword-store` in `security` namespace

## COMMANDS

```bash
task setup-dev-env                 # Install tools + pre-commit hooks
task reconcile                     # Force Flux sync from Git
task cleanup-all                   # Remove failed/completed pods + old replicasets
task flux:test:ns NAMESPACE=X      # Validate Flux manifests for namespace
task rook:check-disks              # Check Ceph disk status
just talos render-config talos-1   # Render a node's machine config
just talos apply-node talos-1      # Apply config (node names, not IPs)
just bootstrap cluster             # DR / first-time only
```

Talos nodes are `talos-1|talos-2|talos-3` mapping to `10.10.10.11/12/13`. Do not target the VIP `10.10.10.10`. Full recipes: `talos/AGENTS.md`, `bootstrap/AGENTS.md`.

## NOTES

- `talos/*.j2` changes are not applied by Flux. Render, `--dry-run`, then `just talos apply-node` per node. Offline validation without creds is documented in `talos/AGENTS.md`.
- `kubeconfig` and `talos/talosconfig` are gitignored
- Vals (bootstrap/Talos render) uses 1Password vault `Home-Lab`. In-cluster ESO Connect uses vaults `Homelab`, `Automation`, and `Services`
- Cluster control plane VIP: `10.10.10.10`
- Nodes use bonded interfaces (802.3ad LACP), MTU 9000, VLANs 3 and 90
- Storage classes: `ceph-block` (RWO), `ceph-filesystem` (RWX), `openebs-hostpath` (local)
- `.private/` directory for local-only files (gitignored)
- Renovate ignores `**/*.sops.*` and `**/resources/**` paths
- Ceph metadata CronJob (`rook-ceph-backup` / `ceph-backup-pvc`) is in-cluster `openebs-hostpath`, **not** last-resort DR; complete cluster/host wipe destroys it. Emergency steps: `kubernetes/apps/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md`. Never restart all OSDs; see `docs/ceph/osd-device-path-recovery.md`.
- The home-ops local ARC runner (`gha-runner-scale-set-aviator-coding-home-ops`) has no Docker daemon (no socket, no DinD sidecar) - `docker://` container actions, `docker run`, and anything that shells out to Docker (e.g. `renovatebot/github-action`) fail there. Details and the exact `runs-on:` label: `kubernetes/apps/actions-runner-system/TROUBLESHOOTING.md`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
