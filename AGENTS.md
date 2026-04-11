# PROJECT KNOWLEDGE BASE

**Generated:** 2026-04-09 | **Commit:** bffbe886 | **Branch:** main

## OVERVIEW

Home-ops GitOps infrastructure repo: Talos Linux k8s cluster managed by Flux v2. 21 namespaces, 100+ apps, triple-redundant Volsync backups, Cilium CNI, Rook-Ceph storage, OnePassword secrets via External Secrets Operator.

## STRUCTURE

```
.
├── kubernetes/
│   ├── apps/           # 21 namespaces, each with app subdirs (see kubernetes/AGENTS.md)
│   ├── components/     # 5 reusable Kustomize Components (volsync, common, alerts, dragonfly, gatus)
│   └── flux/           # cluster ks.yaml (entry point) + meta/repos (OCI/Helm sources)
├── talos/              # Talos Linux config (see talos/AGENTS.md)
├── bootstrap/          # Helmfile-based initial cluster bootstrap (cilium, coredns, flux)
├── .taskfiles/         # 9 task domains: bootstrap, talos, flux, rook, k8s, network, postgres, 1password, actions-runner
├── scripts/            # bootstrap-apps.sh, create-dynamic-bucket.sh
├── docs/               # Reference docs (ai-system, ceph, networking, 1password)
├── specs/              # Ceph reliability plans and verification best practices
└── .renovate/          # Renovate config presets (autoMerge, customManagers, groups, labels, overrides)
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add new app | `kubernetes/apps/{namespace}/{app}/` | Follow pattern: `ks.yaml` + `app/` dir |
| Add app to namespace | `kubernetes/apps/{namespace}/kustomization.yaml` | Add `- ./{app}/ks.yaml` to resources |
| Enable backups | `kubernetes/apps/{ns}/{app}/app/kustomization.yaml` | Add `components: [../../../components/volsync]` |
| App secrets | `kubernetes/apps/{ns}/{app}/app/externalsecret.yaml` | OnePassword via ClusterSecretStore `onepassword` |
| Bootstrap secrets | `kubernetes/components/common/sops/` | SOPS+age encrypted, cluster-wide only |
| Flux entry point | `kubernetes/flux/cluster/ks.yaml` | `cluster-meta` → `cluster-apps` dependency chain |
| Helm/OCI repos | `kubernetes/flux/meta/repos/` | 13 repo definitions |
| Talos node config | `talos/talconfig.yaml` | Source of truth for all nodes |
| Task commands | `Taskfile.yaml` + `.taskfiles/{domain}/` | `task --list` for all available |
| CI workflows | `.github/workflows/` | flux-local, renovate, codeql, image-pull, label-sync |
| Renovate config | `.renovaterc.json5` + `.renovate/` | Extends from Aviator-Coding/mortyops + local presets |
| Tool versions | `.mise.toml` | 25+ tools: kubectl, flux, talos, helm, sops, age, etc. |

## CONVENTIONS

- **YAML schemas**: Every manifest starts with `# yaml-language-server: $schema=...` comment
- **ks.yaml anchors**: `name: &app myapp`, `namespace: &namespace myns` — referenced via `*app`, `*namespace`
- **Schema URL**: Use `kubernetes-schemas.pages.dev` — never `crd.movishell.pl` or `fluxcd-community`
- **Naming**: All lowercase, kebab-case dirs/files. `helmrelease.yaml`, `kustomization.yaml`, `ks.yaml`, `externalsecret.yaml`
- **Commit format**: `type(scope): description` — types: feat, update, fix, perf, refactor, style, test, revert, chore, docs, ci, build, misc
- **Commit scopes**: build, ci, docs, src, test, misc
- **Commit body/footer**: MUST be empty (enforced by commitlint)
- **Gateway API**: `envoy-internal` (private) and `envoy-external` (public) in `network` namespace
- **DNS annotation**: `external-dns.alpha.kubernetes.io/target: "internal.${SECRET_DOMAIN}"` or `"external.${SECRET_DOMAIN}"`
- **Homepage annotations**: `gethomepage.dev/*` annotations on HTTPRoutes for dashboard integration
- **Gatus monitoring**: `gatus.home-operations.com/endpoint` annotation with conditions on HTTPRoutes
- **HelmRelease defaults**: Auto-patched by cluster-apps Kustomization — CRD CreateReplace, rollback recreate, upgrade remediation

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER** edit `talos/clusterconfig/` — generated from `talconfig.yaml`
- **NEVER** use SOPS for application secrets — use ExternalSecret + OnePassword only
- **NEVER** commit unencrypted secrets — pre-commit hooks catch but be vigilant
- **NEVER** commit without pre-commit validation — `task setup-dev-env` to install hooks
- **DO NOT** store secrets in `**/resources/**` — Renovate ignores this path
- **DO NOT** use `*.sops.yaml` for app secrets — reserved for cluster bootstrap only
- Secrets pattern: `**/*.sops.yaml` = SOPS encrypted; `externalsecret.yaml` = OnePassword reference

## UNIQUE STYLES

- **Flux variable substitution**: `postBuild.substituteFrom` references `cluster-secrets` Secret + inline `substitute` map
- **Component composition**: Namespace `kustomization.yaml` includes `../../components/common` + `../../components/alerts` as components
- **Volsync triple-backup**: Apps get 3 ReplicationSources (ceph/minio/r2) + 1 ReplicationDestination via single component include
- **Staggered backup schedules**: 27 apps have unique cron offsets to avoid IOPS contention (see `kubernetes/components/volsync/Readme.md`)
- **VOLSYNC_CACHE_CAPACITY**: Must be sized 20-50% of PVC size — small PVCs need 50-100%
- **dependsOn chains**: 68 apps use `dependsOn` in ks.yaml — typically `onepassword-store` in `security` namespace

## COMMANDS

```bash
task setup-dev-env          # Install tools + pre-commit hooks
task reconcile              # Force Flux sync from Git
task cleanup-all            # Remove failed/completed pods + old replicasets
task flux:test:ns NS=X      # Validate Flux manifests for namespace
task talos:generate-config  # Regenerate Talos configs from talconfig.yaml
task talos:apply-node IP=X  # Apply config to node
task rook:check-disks       # Check Ceph disk status
task bootstrap:talos        # First-time cluster bootstrap
task bootstrap:apps         # Bootstrap applications into cluster
```

## NOTES

- `age.key` and `kubeconfig` are gitignored — required locally but never committed
- SOPS age key: `age13qrheg54vtg3azk0qa7ua7fnszvcc839ln8zazpdvszsfxekrf3s8jytnl`
- SOPS encrypts only `data`/`stringData` fields in bootstrap/kubernetes, full encryption for talos
- Cluster control plane VIP: `10.10.10.10`
- Nodes use bonded interfaces (802.3ad LACP), MTU 9000, VLANs 3 and 90
- Storage classes: `ceph-block` (RWO), `ceph-filesystem` (RWX), `openebs-hostpath` (local)
- `docs/` and `specs/` are gitignored from Claude artifacts — reference only
- `.private/` directory for local-only files (gitignored)
- Renovate ignores `**/*.sops.*` and `**/resources/**` paths
