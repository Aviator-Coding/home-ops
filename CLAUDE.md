# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This is a **home-ops** infrastructure repository managing a self-hosted Kubernetes cluster using GitOps principles. The cluster runs on Talos Linux with Flux v2 for continuous deployment.

## Core Stack

- **OS**: Talos Linux (immutable Kubernetes OS)
- **GitOps**: Flux v2
- **CNI**: Cilium with advanced networking (BGP, LoadBalancer)
- **Storage**: Rook-Ceph distributed storage
- **Secrets**: OnePassword + External Secrets Operator (application secrets), SOPS with age encryption (cluster bootstrap secrets only)
- **External Access**: Cloudflare Tunnel (cloudflared)
- **DNS**: External-DNS with Cloudflare integration
- **Backup**: Volsync with multiple destinations (NAS MinIO, NFS, Cloudflare R2)

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
# Generate Talos configuration
task talos:generate-config

# Apply config to specific node
task talos:apply-node IP=10.10.10.11 MODE=auto

# Upgrade Talos on a node
task talos:upgrade-node IP=10.10.10.11

# Upgrade Kubernetes version
task talos:upgrade-k8s

# Reset single node (preserves data by default)
task talos:reset-node IP=10.10.10.11 PRESERVE_DATA=true GRACEFUL=true

# Full cluster reset (DESTRUCTIVE)
task talos:reset
```

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

```bash
# Bootstrap Talos cluster (first-time setup)
task bootstrap:talos

# Bootstrap applications into cluster
task bootstrap:apps
```

## Architecture & Patterns

### Directory Structure

```
.
├── bootstrap/           # Initial cluster bootstrap configurations
├── kubernetes/
│   ├── apps/           # Applications organized by namespace
│   │   ├── flux-system/      # Flux controllers
│   │   ├── kube-system/      # Core K8s components (Cilium, CoreDNS)
│   │   ├── monitoring/       # Observability (Grafana, Prometheus, VictoriaMetrics, Loki)
│   │   ├── database/         # Databases (CloudNative-PG, Dragonfly)
│   │   ├── rook-ceph/        # Storage cluster
│   │   ├── security/         # Security tools (Authentik, External Secrets)
│   │   ├── network/          # Network services (Cloudflare tunnel, DNS)
│   │   ├── media/            # Media servers
│   │   ├── downloads/        # Download clients
│   │   ├── ai/               # AI applications (Ollama, Open-WebUI, etc.)
│   │   └── ...
│   ├── components/     # Reusable Kustomize components
│   │   ├── common/           # Common secrets, repos
│   │   ├── volsync/          # Backup configurations
│   │   ├── dragonfly/        # Dragonfly monitoring
│   │   └── gatus/            # Monitoring configs
│   └── flux/           # Flux-specific configurations
│       ├── cluster/          # Cluster-wide Flux resources
│       └── meta/             # Flux meta resources (repos, etc.)
├── talos/              # Talos Linux configuration
│   ├── patches/              # Machine config patches
│   ├── talconfig.yaml        # Main Talos config
│   ├── talenv.yaml           # Version configs
│   └── schematic.yaml        # Factory schematic
├── scripts/            # Automation scripts
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
- SOPS encrypted files: `*.sops.yaml`

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

**IMPORTANT - Two separate secret management systems:**

1. **Cluster Bootstrap Secrets** (SOPS encrypted):
   - Location: `kubernetes/components/common/sops/`
   - Used for: Age keys, cluster-wide secrets needed during bootstrap
   - Encryption: SOPS with age key
   - Pattern: `**/*.sops.yaml` files

2. **Application Secrets** (External Secrets Operator):
   - Source: OnePassword vaults
   - Manifests: `ExternalSecret` resources in app directories
   - Pattern: `externalsecret.yaml` files
   - **Never store application secrets in Git**

### Volsync Backup Strategy

Volsync provides triple-redundant backups with different schedules:

1. **NAS MinIO** - Hourly (`0 * * * *`) to S3-compatible storage
2. **NAS NFS** - Hourly (`15 * * * *`) to REST server
3. **Cloudflare R2** - Daily (`30 0 * * *`) to cloud storage

To enable backups for an app:

```yaml
# In app's kustomization.yaml
components:
  - ../../../components/volsync
```

This creates ReplicationSource resources for automated backups and ReplicationDestination for manual restores.

**Cache sizing**: Set `VOLSYNC_CACHE_CAPACITY` to 20-50% of PVC size for optimal performance.

### Network Architecture

- **Cilium CNI**: Layer 2 announcements via BGP, LoadBalancer mode enabled
- **Gateway API**: HTTPRoutes with `internal` (private) and `external` (public) gateways
- **Cloudflare Tunnel**: Secure ingress for public services without port forwarding
- **External-DNS**: Automatic DNS record creation in Cloudflare
- **Split DNS**: k8s_gateway provides internal DNS resolution

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
   - **Never use SOPS for application secrets**

4. Enable backups (optional):
   ```yaml
   # In app/kustomization.yaml
   components:
     - ../../../components/volsync
   ```

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
3. **Do not** modify generated files in `talos/clusterconfig/`
4. Test with `task flux:test:ns NAMESPACE={namespace}`
5. Commit and push

### Managing Cluster Secrets

**Only for cluster bootstrap secrets:**

```bash
# Encrypt a new secret
sops --encrypt --age age13qrheg54vtg3azk0qa7ua7fnszvcc839ln8zazpdvszsfxekrf3s8jytnl secret.yaml > secret.sops.yaml

# Edit encrypted file
sops kubernetes/components/common/sops/cluster-secrets.sops.yaml

# Decrypt to view
sops -d secret.sops.yaml
```

**For application secrets - use ExternalSecret resources pointing to OnePassword.**

### Pre-commit Hooks

**CRITICAL**: All commits are validated by pre-commit hooks including:

- **commitlint**: Enforces semantic commit format `type(scope): description`
- **file validation**: YAML, JSON, TOML syntax
- **formatting**: Auto-fixes whitespace, line endings
- **security**: Detects private keys and sensitive data

Valid commit types: `feat`, `fix`, `chore`, `ci`, `docs`, `refactor`, `test`
Valid scopes: `container`, `helm`, `github-action`, `mise`, `talos`, `flux`, or app/namespace names

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

- **talconfig.yaml**: Main configuration (node IPs, network, patches)
- **talenv.yaml**: Version definitions (Talos, Kubernetes)
- **patches/**: Machine config patches applied to all nodes
- **clusterconfig/**: Generated configs (do not edit manually)

After editing `talconfig.yaml`:
```bash
task talos:generate-config
task talos:apply-node IP={node-ip} MODE=auto
```

### Renovate Dependency Management

Renovate automatically creates PRs for:
- Container image updates
- Helm chart updates
- GitHub Actions versions
- mise tool versions

Review and merge Renovate PRs to keep dependencies current. Check the Dependency Dashboard issue for update status.

## Important Notes

1. **Never edit files in `talos/clusterconfig/`** - these are generated from `talconfig.yaml`

2. **SOPS is only for cluster bootstrap secrets** - use ExternalSecret for all application secrets

3. **All commits must pass pre-commit validation** - ensure commit messages follow semantic format

4. **Flux reconciles automatically** - changes pushed to Git are applied within ~1 minute

5. **Backup before destructive operations** - especially for Rook/Ceph disk operations

6. **Test Flux manifests locally** - use `task flux:test:ns` before pushing

7. **Storage class naming**:
   - `ceph-block` - RWO block storage (most apps)
   - `ceph-filesystem` - RWX filesystem storage (shared)
   - `openebs-hostpath` - Local hostpath storage (non-replicated)

8. **Network configuration**:
   - Nodes use bonded interfaces (802.3ad LACP)
   - MTU 9000 for jumbo frames
   - VLANs 3 and 90 configured on bond0
   - Virtual IP 10.10.10.10 for control plane

9. **Monitoring access**:
   - Grafana dashboards for observability
   - Gatus for uptime monitoring
   - Ceph dashboard embedded in Grafana

10. **Do not commit unencrypted secrets** - pre-commit hooks will catch this but be vigilant
