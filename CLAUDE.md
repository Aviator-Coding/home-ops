# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a **home-ops** infrastructure repository managing a self-hosted Kubernetes cluster using GitOps principles. The cluster runs on Talos Linux with Flux v2 for continuous deployment.

## Core Stack

- **OS**: Talos Linux (immutable Kubernetes OS)
- **GitOps**: Flux v2
- **CNI**: Cilium with advanced networking (BGP, LoadBalancer)
- **Storage**: Rook-Ceph distributed storage
- **Secrets**: OnePassword + External Secrets Operator (all secrets); bootstrap minimum via `vals` + `kustomize`
- **External Access**: Cloudflare Tunnel (cloudflared)
- **DNS**: External-DNS with Cloudflare integration
- **Backup**: VolSync with three destinations (local Ceph S3, NAS MinIO, Cloudflare R2; no NFS)

## Common Commands

### Development Environment Setup

```bash
# Install all required tools
mise install

# Setup pre-commit hooks (required for commits)
task setup-dev-env
# OR manually:
pre-commit install --hook-type pre-commit --hook-type commit-msg
```

### Kubernetes Operations

```bash
# Force Flux to reconcile from Git
task reconcile

# Clean up failed/completed pods
task cleanup-all
task cleanup-failed-pods
task cleanup-succeeded-pods

# Flux validation tests
task flux:test:all           # Test all namespaces
task flux:test:ns NAMESPACE=monitoring  # Test specific namespace
task flux:test:quick         # Quick test without verbose output
```

### Talos Operations

```bash
# Render a node's machine config to stdout (node = talos-1|talos-2|talos-3)
just talos render-config talos-1

# Apply config to a node (append --dry-run to preview, --insecure for maintenance mode)
just talos apply-node talos-1
just talos apply-node talos-1 --dry-run

# Upgrade Talos on a node (install image derived from the rendered config)
just talos upgrade-node talos-1

# Upgrade Kubernetes version
just talos upgrade-k8s v1.36.1

# Reboot / shutdown / reset a node
just talos reboot-node talos-1
just talos reset-node talos-1
```

Secrets live in 1Password (`Home-Lab/talos` item) and are injected at render time by `vals`;
auth via `OP_SERVICE_ACCOUNT_TOKEN` in the gitignored `.secrets.env` (see `.secrets.env.example`)
or an interactive `op signin`.

### Rook-Ceph Operations

```bash
# Check disk status across all nodes
task rook:check-disks
task rook:check-disks-talos  # Using Talos CLI

# Validate cluster and templates before operations
task rook:validate-all

# Backup current Rook configuration
task rook:backup-rook-config

# Wipe specific node disks
task rook:wipe-talos-1
task rook:wipe-talos-2
task rook:wipe-talos-3

# Wipe all Rook disks and data (DESTRUCTIVE)
task rook:wipe-all-with-progress

# Check wipe operation status
task rook:status

# Validate wipe was successful
task rook:validate-wipe

# Clean up leftover wipe jobs
task rook:cleanup
```

### Bootstrap Operations

> ⚠️ **Bootstrap is a disaster-recovery / first-time-setup operation only.** The
> `base` and `apps` stages `kubectl apply` / `helmfile sync` against the
> cluster - never run a full bootstrap against a healthy running cluster. See
> `bootstrap/AGENTS.md`.

```bash
# Full end-to-end bootstrap
just bootstrap cluster
# Or run individual stages:
just bootstrap nodes      # apply Talos config to all nodes (insecure/maintenance)
just bootstrap k8s        # talosctl bootstrap etcd
just bootstrap base       # apply bootstrap secrets (kustomize + vals) + CRDs
just bootstrap apps       # helmfile sync (cilium, coredns, spegel, cert-manager, flux)
```

Bootstrap secrets live in 1Password `Home-Lab` and are injected by `vals` at apply time
via `bootstrap/kustomize/apps/` (kustomize + vals pipeline).

## Architecture & Patterns

### Directory Structure

```
.
├── bootstrap/           # Initial cluster bootstrap configurations
├── kubernetes/
│   ├── apps/           # Applications organized by namespace
│   │   ├── flux-system/      # Flux controllers
│   │   ├── kube-system/      # Core K8s components (Cilium, CoreDNS)
│   │   ├── monitoring/       # Observability (Grafana, Prometheus, VictoriaMetrics, Loki, Gatus)
│   │   ├── database/         # Databases (CloudNative-PG, Dragonfly)
│   │   ├── rook-ceph/        # Storage cluster
│   │   ├── security/         # Security tools (Authentik, External Secrets)
│   │   ├── network/          # Network services (Cloudflare tunnel, Unifi DNS, Envoy)
│   │   ├── media/            # Media servers
│   │   ├── downloads/        # Download clients
│   │   ├── ai/               # AI apps (open-webui, vllm, toolhive, agentgateway, ...)
│   │   └── ...
│   ├── components/     # Reusable Kustomize components
│   │   ├── common/           # Common secrets, repos
│   │   ├── alerts/           # Included by every namespace kustomization
│   │   ├── volsync/          # Backup configurations
│   │   └── dragonfly/        # Dragonfly monitoring (includes gatus.yaml)
│   └── flux/           # Flux-specific configurations
│       ├── cluster/          # Cluster-wide Flux resources
│       └── meta/             # Flux meta resources (repos, etc.)
├── talos/              # Talos Linux configuration
│   ├── nodes/                # Per-node overlays (talos-{1,2,3}.yaml.j2)
│   ├── machineconfig.yaml.j2 # Shared machine + cluster config template
│   ├── schematic.yaml.j2     # Factory schematic template
│   └── mod.just              # just talos recipe module
├── .taskfiles/         # Task automation organized by domain
└── .mise.toml          # Development tool versions
```

### Application Structure Pattern

Each application follows this structure:

```
kubernetes/apps/{namespace}/{app}/
├── app/                # HelmRelease or Kustomization
│   ├── helmrelease.yaml
│   ├── kustomization.yaml
│   └── resources/      # ConfigMaps, Secrets, etc.
├── ks.yaml            # Flux Kustomization for this app
└── README.md          # App-specific docs (if needed)
```

### Flux GitOps Pattern

- Each namespace has a `kustomization.yaml` that references all apps
- Apps use `ks.yaml` (Flux Kustomization) to define deployment order and health checks
- HelmReleases define Helm chart deployments with custom values
- Dependencies between apps are managed via `dependsOn` in `ks.yaml`

### Naming Conventions

**Directory Naming:**
- All directories use **lowercase** names
- Multi-word directories use **kebab-case** (e.g., `home-automation`, `rook-ceph`)
- Task file directories in `.taskfiles/` follow the same convention (e.g., `rook`, not `Rook`)

**File Naming:**
- All files use **lowercase** names
- Kubernetes manifest files: `helmrelease.yaml`, `kustomization.yaml`, `ks.yaml`
- External secrets: `externalsecret.yaml`
- External Secrets: `externalsecret.yaml`

**YAML Anchors in ks.yaml:**
- Use `&app` for the application name anchor: `name: &app myapp`
- Use `&namespace` for the namespace anchor: `namespace: &namespace myns`
- Reference with `*app` and `*namespace` respectively
- All ks.yaml files must have `namespace:` defined in metadata

**Schema URLs:**
- Standard schema for Flux Kustomizations: `https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json`
- Avoid using alternative schemas like `crd.movishell.pl` or `fluxcd-community`

**Example ks.yaml structure:**
```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app myapp
  namespace: &namespace mynamespace
spec:
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  targetNamespace: *namespace
  # ... other fields
```

### Secret Management

**One secret management system: ExternalSecrets Operator + 1Password**

All secrets are managed via `ExternalSecret` resources backed by the 1Password ClusterSecretStore.

- **Bootstrap minimum** (`bootstrap/kustomize/apps/security/`): `onepassword-secret` is injected
  at bootstrap time from 1Password via `vals` (`ref+op://Home-Lab/1password/*`). It carries the
  prune-disabled annotation so Flux never deletes it (ESO cannot self-bootstrap its own credential).
- **All other secrets**: `ExternalSecret` resources in app directories pulling from 1Password vaults.
- **Never store secrets in Git** — no SOPS, no plaintext, no encrypted files.

### Volsync Backup Strategy

VolSync provides triple-redundant backups. Authoritative schedules, retention, and the app list live in `kubernetes/components/volsync/Readme.md` and the three `replicationsource.yaml` files (`ceph/`, `minio/`, `r2/`). Component defaults:

1. **Local Ceph S3** (`ceph/`) - every 4 hours (`0 */4 * * *`)
2. **NAS MinIO** (`minio/`) - every 6 hours (`30 */6 * * *`)
3. **Cloudflare R2** (`r2/`) - daily at 02:00 (`0 2 * * *`)

There is no NFS/REST destination. Per-app `VOLSYNC_SCHEDULE_CEPH` / `VOLSYNC_SCHEDULE_MINIO` / `VOLSYNC_SCHEDULE_R2` overrides go on the Flux `ks.yaml` `postBuild.substitute` map.

To enable backups for an app, include the component on the **Flux Kustomization** (`ks.yaml`), not `app/kustomization.yaml`:

```yaml
# kubernetes/apps/{namespace}/{app}/ks.yaml
spec:
  components:
    - ../../../../components/volsync
  dependsOn:
    - name: volsync
      namespace: system
  postBuild:
    substitute:
      APP: *app
      VOLSYNC_CAPACITY: 10Gi
      VOLSYNC_CACHE_CAPACITY: 5Gi
      # optional schedule offsets:
      # VOLSYNC_SCHEDULE_CEPH: "15 */4 * * *"
      # VOLSYNC_SCHEDULE_MINIO: "45 */6 * * *"
      # VOLSYNC_SCHEDULE_R2: "15 2 * * *"
```

Example: `kubernetes/apps/media/immich/ks.yaml`. This creates three ReplicationSources (automated backups) and one ReplicationDestination (manual restore).

**Cache sizing**: Set `VOLSYNC_CACHE_CAPACITY` to 20-50% of PVC size for optimal performance. Small PVCs need 50-100%.

### Network Architecture

- **Cilium CNI**: native routing, kube-proxy replacement, BGP control plane peers with UniFi for LoadBalancer IP advertisement. L2 announcements are disabled. See `docs/networking/bgp.md`.
- **Gateway API**: HTTPRoutes with `envoy-internal` (private) and `envoy-external` (public) gateways in `network`
- **Cloudflare Tunnel**: Secure ingress for public services without port forwarding
- **External-DNS**: Public names via `network/cloudflare-dns`
- **Split DNS**: External-DNS Unifi webhook (`network/unifi-dns`) publishes internal records to the Unifi DNS server. Public names go through `cloudflare-dns` + the external gateway. There is no `k8s-gateway` app.

### Storage Architecture

- **Rook-Ceph**: Distributed storage across 3 nodes
- **Block Storage**: `ceph-block` StorageClass for RWO volumes
- **Filesystem Storage**: `ceph-filesystem` for RWX volumes
- **Snapshot Class**: `csi-ceph-blockpool` for volume snapshots
- **Node Configuration**: Each node has 2 NVMe disks dedicated to Ceph OSDs

### Monitoring Stack

- **Metrics**: VictoriaMetrics (primary), Prometheus (secondary)
- **Logs**: VictoriaLogs with Vector aggregation
- **Dashboards**: Grafana with pre-configured dashboards
- **Alerting**: Alertmanager with external integrations
- **Uptime**: Gatus for endpoint monitoring
- **Exporters**: Multiple exporters (UnPoller for UniFi, custom exporters)

## Development Workflow

### Adding a New Application

1. Create namespace directory structure:
   ```bash
   mkdir -p kubernetes/apps/{namespace}/{app}/app
   ```

2. Create application manifests:
   - `ks.yaml` - Flux Kustomization
   - `app/helmrelease.yaml` - Helm chart (if using Helm)
   - `app/kustomization.yaml` - Kustomize config

3. Add secrets (if needed):
   - Create `ExternalSecret` resource referencing OnePassword
   - **Never store secrets in Git**

4. Enable backups (optional): add `spec.components: [../../../../components/volsync]`, `dependsOn: volsync` (namespace `system`), and `VOLSYNC_*` substitute keys on the Flux `ks.yaml`. See Volsync Backup Strategy above.

5. Add to namespace kustomization:
   ```yaml
   # In kubernetes/apps/{namespace}/kustomization.yaml
   resources:
     - ./{app}/ks.yaml
   ```

6. Commit and push - Flux will deploy automatically

### Modifying Existing Applications

1. Read the app's current configuration first
2. Edit manifests directly in `kubernetes/apps/{namespace}/{app}/`
3. Test with `task flux:test:ns NAMESPACE={namespace}`
4. Commit and push

### Managing Cluster Secrets

All secrets use `ExternalSecret` resources backed by the `onepassword` ClusterSecretStore:

```yaml
# kubernetes/apps/{namespace}/{app}/app/externalsecret.yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: my-app
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: my-app-secret
    template:
      data:
        MY_KEY: "{{ .MY_FIELD }}"
  dataFrom:
    - extract:
        key: my-1password-item
```

Add the secret value to the appropriate 1Password vault item (`Homelab`, `Automation`, or `Services`).
**Never commit plaintext or encrypted secret files to Git.**

### Pre-commit Hooks

**CRITICAL**: All commits are validated by pre-commit hooks including:

- **commitlint**: Enforces semantic commit format `type(scope): description`
- **file validation**: YAML, JSON, TOML syntax
- **formatting**: Auto-fixes whitespace, line endings
- **security**: Detects private keys and sensitive data

Valid commit types: `feat`, `fix`, `chore`, `ci`, `docs`, `refactor`, `test`
Valid scopes: `container`, `helm`, `github-action`, `mise`, `talos`, `flux`, `deps`, `github-release`, or app/namespace names
Authoritative rules live in `.commitlintrc.yaml` (enforced by the commit-msg hook). Bodies and footers are allowed.

Examples:
- `feat(container): update nginx to v1.25.0`
- `fix(helm): correct victoriametrics values`
- `chore(talos): update to v1.12.0`

### Debugging Applications

```bash
# Check Flux reconciliation status
flux get sources git -A
flux get ks -A
flux get hr -A

# View application pods
kubectl -n {namespace} get pods -o wide

# Check pod logs
kubectl -n {namespace} logs {pod-name} -f

# Describe resource for events
kubectl -n {namespace} describe pod {pod-name}

# View namespace events
kubectl -n {namespace} get events --sort-by='.metadata.creationTimestamp'

# Check Volsync backups
kubectl get replicationsource -A
kubectl get replicationdestination -A
```

### Talos Maintenance

- **machineconfig.yaml.j2**: Shared machine + cluster config template (minijinja). Secrets are `ref+op://Home-Lab/talos/*` references resolved by `vals`.
- **nodes/talos-{1,2,3}.yaml.j2**: Per-node overlays (machine.type, install disk, hostname).
- **schematic.yaml.j2**: Factory schematic template (kernel args + system extensions).
- **mod.just**: `just talos` recipe module.

After editing `machineconfig.yaml.j2`, a node overlay, or `schematic.yaml.j2`:
```bash
just talos render-config talos-1 | talosctl validate -m metal -c /dev/stdin
just talos apply-node talos-1 --dry-run   # review diff before a real apply
just talos apply-node talos-1
```

> ⚠️ **Before a node reboot** (`just talos upgrade-node` / `reboot-node` / `reset-node`),
> which restarts that node's Ceph OSDs: confirm `ceph status` is **HEALTH_OK** and run
> `task rook:check-osd-device-paths`. Rook bug [#17224](https://github.com/rook/rook/issues/17224)
> bakes unstable `/dev/nvmeXn1` names into OSD deployments; on reboot an OSD relies on a
> relocate fallback that can fail if the cluster is already degraded. Reboot one node at a
> time, waiting for HEALTH_OK between nodes. If an OSD is stuck `Init` afterward, see
> [`docs/ceph/osd-device-path-recovery.md`](docs/ceph/osd-device-path-recovery.md).

### Renovate Dependency Management

Renovate automatically creates PRs for:
- Container image updates
- Helm chart updates
- GitHub Actions versions
- mise tool versions

Review and merge Renovate PRs to keep dependencies current. Check the Dependency Dashboard issue for update status.

## Important Notes

1. **All secrets via ExternalSecret + 1Password** - never store secrets in Git (no SOPS files, no plaintext)

2. **All commits must pass pre-commit file/security checks** - commitlint is not currently a hook

3. **Flux reconciles automatically** - changes pushed to Git are applied within ~1 minute

4. **Backup before destructive operations** - especially for Rook/Ceph disk operations

5. **Test Flux manifests locally** - use `task flux:test:ns` before pushing

6. **Storage class naming**:
   - `ceph-block` - RWO block storage (most apps)
   - `ceph-filesystem` - RWX filesystem storage (shared)
   - `openebs-hostpath` - Local hostpath storage (non-replicated)

7. **Network configuration**:
   - Nodes use bonded interfaces (802.3ad LACP)
   - MTU 9000 for jumbo frames
   - VLANs 3 and 90 configured on bond0
   - Virtual IP 10.10.10.10 for control plane

8. **Monitoring access**:
   - Grafana dashboards for observability
   - Gatus for uptime monitoring
   - Ceph dashboard embedded in Grafana

9. **Do not commit unencrypted secrets** - pre-commit hooks will catch this but be vigilant
