# KUBERNETES GITOPS LAYER

## OVERVIEW

Flux v2 GitOps structure: 21 namespaces, 100+ apps, 5 reusable Kustomize Components, 13 Helm/OCI repositories.

## STRUCTURE

```
kubernetes/
├── apps/               # One dir per namespace → one dir per app
│   ├── {namespace}/
│   │   ├── kustomization.yaml   # Lists all apps + includes components
│   │   └── {app}/
│   │       ├── ks.yaml          # Flux Kustomization (entry point)
│   │       └── app/
│   │           ├── helmrelease.yaml
│   │           ├── kustomization.yaml
│   │           └── externalsecret.yaml
├── components/         # Reusable Kustomize Components
│   ├── common/         # Namespace setup, SOPS secrets, shared repos
│   ├── alerts/         # Alert rules for Flux resources
│   ├── volsync/        # Triple-redundant backups (ceph/minio/r2)
│   ├── dragonfly/      # Dragonfly monitoring integration
│   └── gatus/          # Uptime monitoring config
└── flux/
    ├── cluster/ks.yaml # Entry point: cluster-meta → cluster-apps chain
    └── meta/repos/     # 13 HelmRepository/OCIRepository definitions
```

## APP CREATION PATTERN

### 1. Create app directory
```
kubernetes/apps/{namespace}/{app}/
├── ks.yaml
└── app/
    ├── helmrelease.yaml
    ├── kustomization.yaml
    └── externalsecret.yaml  (if secrets needed)
```

### 2. ks.yaml template (MUST follow exactly)
```yaml
---
# yaml-language-server: $schema=https://kubernetes-schemas.pages.dev/kustomize.toolkit.fluxcd.io/kustomization_v1.json
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app {appname}
  namespace: &namespace {namespace}
spec:
  targetNamespace: *namespace
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  interval: 30m
  timeout: 5m
  path: "./kubernetes/apps/{namespace}/{app}/app"
  postBuild:
    substituteFrom:
      - name: cluster-secrets
        kind: Secret
  prune: true
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  wait: false
  dependsOn:
    - name: onepassword-store
      namespace: security
```

### 3. Register in namespace kustomization
```yaml
# kubernetes/apps/{namespace}/kustomization.yaml — add to resources:
resources:
  - ./{app}/ks.yaml
```

## NAMESPACE KUSTOMIZATION PATTERN

Every namespace `kustomization.yaml` includes shared components:
```yaml
components:
  - ../../components/common     # Always included
  - ../../components/alerts     # Always included
resources:
  - ./{app1}/ks.yaml
  - ./{app2}/ks.yaml
```

## NAMESPACES (21)

| Namespace | Apps | Domain |
|-----------|------|--------|
| actions-runner-system | 3 | GitHub Actions self-hosted runners |
| ai | 8 | AI apps (ollama, open-webui, litellm, qdrant, etc.) |
| ai-system | 4 | AI infrastructure (agentgateway, kagent) |
| cert-manager | 1 | TLS certificate management |
| coder | 1 | Development environments |
| database | 6 | CloudNative-PG, Dragonfly, EMQX, SurrealDB |
| downloads | 14 | *arr stack, qbittorrent, sabnzbd, autobrr |
| flux-system | 3 | Flux controllers |
| home-automation | 4 | Home Assistant, Zigbee2MQTT, ESPHome, Matter |
| kube-system | 8 | Cilium, CoreDNS, Spegel, Reloader |
| media | 4 | Jellyfin, Calibre, Immich |
| monitoring | 15 | Grafana, Prometheus, Loki, VictoriaMetrics, Gatus |
| network | 7 | Envoy Gateway, Cloudflare tunnel/DNS, k8s-gateway |
| openclaw | 3 | OpenClaw instances |
| rook-ceph | 1 | Ceph storage operator + cluster |
| security | 3 | Authentik, External Secrets, OAuth2 Proxy |
| selfhosted | 10 | Homepage, n8n, Paperless, Linkwarden, etc. |
| system | 5 | System services |
| system-controller | 1 | System controllers |
| system-upgrade | 1 | Upgrade automation |

## CONVENTIONS (beyond root)

- **ExternalSecret**: Always `ClusterSecretStore` named `onepassword`, target name uses `&secret` anchor
- **HTTPRoute annotations**: `gethomepage.dev/*` for dashboard, `external-dns.alpha.kubernetes.io/target` for DNS, `gatus.home-operations.com/endpoint` for monitoring
- **Internal gateway**: `parentRefs: [{name: envoy-internal, namespace: network, sectionName: https}]`
- **External gateway**: `parentRefs: [{name: envoy-external, namespace: network, sectionName: https}]`
- **Volsync vars**: Set `APP`, `VOLSYNC_CAPACITY`, `VOLSYNC_CACHE_CAPACITY`, `VOLSYNC_SCHEDULE_*` in ks.yaml `postBuild.substitute`
- **HelmRelease defaults**: Don't set install/upgrade/rollback — auto-patched by `cluster-apps` Kustomization

## ANTI-PATTERNS

- **DO NOT** add `decryption` block to ks.yaml — auto-patched by cluster-apps
- **DO NOT** add HelmRelease remediation config — auto-patched by cluster-apps
- **DO NOT** use `crd.movishell.pl` or `fluxcd-community` schema URLs
- **DO NOT** forget `dependsOn: [{name: onepassword-store, namespace: security}]` for apps using ExternalSecrets
