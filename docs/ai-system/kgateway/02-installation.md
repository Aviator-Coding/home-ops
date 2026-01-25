# Installation Guide

> **Comprehensive installation options for Kgateway Agentgateway including Helm, Argo CD, and advanced configurations.**

## Prerequisites

### Required Tools

| Tool | Minimum Version | Purpose |
|------|-----------------|---------|
| Kubernetes | 1.25+ | Target cluster |
| kubectl | Within 1 minor version of cluster | Cluster management |
| Helm | 3.x | Package installation |

### Optional Tools

| Tool | Purpose |
|------|---------|
| Kind | Local Kubernetes testing |
| k3d | Lightweight local cluster |
| jq | JSON parsing for testing |

## Installation Methods

### Method 1: Helm (Recommended)

#### Step 1: Install Gateway API CRDs

Choose one of the following:

**Standard Installation** (recommended):
```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml
```

**Experimental Features** (includes TCPRoute, UDPRoute, etc.):
```bash
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/experimental-install.yaml
```

#### Step 2: Install Kgateway CRDs

Optional: Inspect CRDs before installation:
```bash
helm template --version v2.1.2 kgateway-crds \
  oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds \
  --output-dir ./helm
```

Install CRDs:
```bash
helm upgrade -i kgateway-crds oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds \
  --create-namespace \
  --namespace kgateway-system \
  --version v2.1.2
```

#### Step 3: Install Agentgateway

**Basic Installation**:
```bash
helm upgrade -i kgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --namespace kgateway-system \
  --version v2.1.2 \
  --set agentgateway.enabled=true
```

**With Custom Values File**:
```bash
helm upgrade -i kgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --namespace kgateway-system \
  --version v2.1.2 \
  --set agentgateway.enabled=true \
  -f values.yaml
```

**Development Build** (latest main branch):
```bash
helm upgrade -i kgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --namespace kgateway-system \
  --version v2.2.0-main \
  --set controller.image.pullPolicy=Always \
  --set agentgateway.enabled=true \
  --set controller.extraEnv.KGW_ENABLE_GATEWAY_API_EXPERIMENTAL_FEATURES=true
```

#### Step 4: Verify Installation

```bash
# Check pods
kubectl get pods -n kgateway-system

# Check GatewayClass
kubectl get gatewayclass agentgateway

# Check CRDs
kubectl get crds | grep kgateway
```

### Method 2: Argo CD

For GitOps deployments, use Argo CD Application resources:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: kgateway-crds
  namespace: argocd
spec:
  project: default
  source:
    chart: kgateway-crds
    repoURL: cr.kgateway.dev/kgateway-dev/charts
    targetRevision: v2.1.2
  destination:
    server: https://kubernetes.default.svc
    namespace: kgateway-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
    - CreateNamespace=true
---
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: kgateway
  namespace: argocd
spec:
  project: default
  source:
    chart: kgateway
    repoURL: cr.kgateway.dev/kgateway-dev/charts
    targetRevision: v2.1.2
    helm:
      values: |
        agentgateway:
          enabled: true
  destination:
    server: https://kubernetes.default.svc
    namespace: kgateway-system
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
```

## Helm Values Reference

### Core Settings

```yaml
# values.yaml

# Enable agentgateway data plane
agentgateway:
  enabled: true

# Controller configuration
controller:
  image:
    repository: cr.kgateway.dev/kgateway-dev/kgateway
    tag: ""  # Uses chart version by default
    pullPolicy: IfNotPresent

  # Resource limits
  resources:
    requests:
      cpu: 100m
      memory: 128Mi
    limits:
      cpu: 500m
      memory: 512Mi

  # Extra environment variables
  extraEnv:
    KGW_ENABLE_GATEWAY_API_EXPERIMENTAL_FEATURES: "false"

# Service account
serviceAccount:
  create: true
  name: ""
  annotations: {}

# RBAC
rbac:
  create: true
```

### Advanced Settings

```yaml
# Advanced configuration

# High availability
controller:
  replicas: 2
  affinity:
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: kgateway
          topologyKey: kubernetes.io/hostname

# Custom annotations
podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "9090"

# Security context
securityContext:
  runAsNonRoot: true
  runAsUser: 10101
  fsGroup: 10101

# Node selector
nodeSelector:
  kubernetes.io/os: linux

# Tolerations
tolerations:
- key: "node-role.kubernetes.io/control-plane"
  operator: "Exists"
  effect: "NoSchedule"
```

## TLS Configuration

### Enable TLS for Control Plane

Create TLS secret:
```bash
kubectl create secret tls kgateway-tls \
  --cert=tls.crt \
  --key=tls.key \
  -n kgateway-system
```

Configure Helm values:
```yaml
controller:
  tls:
    enabled: true
    secretName: kgateway-tls
```

## Namespace Configuration

### Single Namespace Mode

Restrict kgateway to a single namespace:

```yaml
controller:
  watchNamespaces:
  - my-namespace
```

### Multi-Namespace Mode

Watch specific namespaces:

```yaml
controller:
  watchNamespaces:
  - namespace-a
  - namespace-b
  - namespace-c
```

### All Namespaces (Default)

```yaml
controller:
  watchNamespaces: []  # Empty = all namespaces
```

## Resource Requirements

### Minimum Requirements (Development)

| Component | CPU Request | Memory Request |
|-----------|-------------|----------------|
| Controller | 100m | 128Mi |
| Proxy (per) | 100m | 64Mi |

### Recommended (Production)

| Component | CPU Request | CPU Limit | Memory Request | Memory Limit |
|-----------|-------------|-----------|----------------|--------------|
| Controller | 500m | 2000m | 256Mi | 1Gi |
| Proxy (per) | 500m | 2000m | 128Mi | 512Mi |

## Upgrade Process

### Standard Upgrade

```bash
# Update CRDs first
helm upgrade kgateway-crds oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds \
  --namespace kgateway-system \
  --version v2.1.2

# Then upgrade controller
helm upgrade kgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --namespace kgateway-system \
  --version v2.1.2 \
  --set agentgateway.enabled=true
```

### Check Current Version

```bash
helm list -n kgateway-system
```

## Uninstallation

### Complete Removal

```bash
# Remove Helm releases
helm uninstall kgateway -n kgateway-system
helm uninstall kgateway-crds -n kgateway-system

# Remove Gateway API CRDs (optional, may affect other controllers)
kubectl delete -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml

# Remove namespace
kubectl delete namespace kgateway-system
```

### Partial Removal (Keep CRDs)

```bash
helm uninstall kgateway -n kgateway-system
```

## Troubleshooting

### Common Issues

**Pod not starting**:
```bash
kubectl describe pod -n kgateway-system -l app=kgateway
kubectl logs -n kgateway-system -l app=kgateway --tail=100
```

**GatewayClass not accepted**:
```bash
kubectl describe gatewayclass agentgateway
```

**CRD conflicts**:
```bash
kubectl get crds | grep -E 'gateway|kgateway'
```

### Verify Installation Health

```bash
# All components should be Running
kubectl get pods -n kgateway-system

# GatewayClass should show ACCEPTED=True
kubectl get gatewayclass

# Check controller logs for errors
kubectl logs -n kgateway-system deployment/kgateway --tail=50
```

---

*See [03-gateway-setup.md](./03-gateway-setup.md) for creating and configuring gateways.*
