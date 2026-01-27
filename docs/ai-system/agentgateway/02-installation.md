# Installation Guide

> **Detailed installation guide for AgentGateway using Flux CD GitOps in the Home-Ops cluster.**

## Overview

AgentGateway is deployed through the kgateway Helm chart with `agentgateway.enabled=true`. This guide covers:

1. Flux CD GitOps deployment (recommended)
2. Manual Helm installation
3. Cluster-specific configuration

---

## Prerequisites

- Kubernetes cluster v1.25+
- Flux CD configured and connected to your Git repository
- External Secrets Operator with 1Password ClusterSecretStore

---

## Flux CD Installation (Recommended)

### Directory Structure

```
kubernetes/apps/ai-system/agentgateway/
├── ks.yaml                    # Flux Kustomization
├── crds/
│   ├── kustomization.yaml
│   ├── helmrelease.yaml       # CRDs HelmRelease
│   └── ocirepository.yaml
└── app/
    ├── kustomization.yaml
    ├── helmrelease.yaml       # Main HelmRelease
    ├── ocirepository.yaml
    ├── gateway.yaml           # Gateway resource
    ├── networkpolicy.yaml
    ├── pdb.yaml
    └── servicemonitor.yaml
```

### Step 1: Create OCIRepository for CRDs

```yaml
# kubernetes/apps/ai-system/agentgateway/crds/ocirepository.yaml
---
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: OCIRepository
metadata:
  name: agentgateway-crds
spec:
  interval: 12h
  url: oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds
  ref:
    tag: v2.1.2
```

### Step 2: Create CRDs HelmRelease

```yaml
# kubernetes/apps/ai-system/agentgateway/crds/helmrelease.yaml
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: agentgateway-crds
spec:
  interval: 1h
  chartRef:
    kind: OCIRepository
    name: agentgateway-crds
  install:
    timeout: 10m
    crds: CreateReplace
    createNamespace: true
  upgrade:
    remediation:
      remediateLastFailure: true
      retries: 3
      strategy: rollback
    cleanupOnFail: true
    crds: CreateReplace
  uninstall:
    keepHistory: false
  driftDetection:
    mode: enabled
  maxHistory: 3
```

### Step 3: Create OCIRepository for App

```yaml
# kubernetes/apps/ai-system/agentgateway/app/ocirepository.yaml
---
apiVersion: source.toolkit.fluxcd.io/v1beta2
kind: OCIRepository
metadata:
  name: agentgateway
spec:
  interval: 12h
  url: oci://cr.kgateway.dev/kgateway-dev/charts/kgateway
  ref:
    tag: v2.1.2
```

### Step 4: Create Main HelmRelease

```yaml
# kubernetes/apps/ai-system/agentgateway/app/helmrelease.yaml
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: agentgateway
spec:
  interval: 1h
  chartRef:
    kind: OCIRepository
    name: agentgateway
  install:
    timeout: 10m
    replace: true
    crds: CreateReplace
    createNamespace: true
    strategy:
      name: RetryOnFailure
      retryInterval: 5m
  upgrade:
    remediation:
      remediateLastFailure: true
      retries: 3
      strategy: rollback
    cleanupOnFail: true
    crds: CreateReplace
  test:
    enable: true
  rollback:
    recreate: true
    force: true
    cleanupOnFail: true
  uninstall:
    keepHistory: false
  driftDetection:
    mode: enabled
  maxHistory: 3
  values:
    # Enable AgentGateway data plane
    agentgateway:
      enabled: true

    # Controller configuration
    controller:
      replicaCount: 2
      logLevel: "info"
      image:
        pullPolicy: IfNotPresent
      resources:
        requests:
          cpu: "100m"
          memory: "128Mi"
        limits:
          cpu: "500m"
          memory: "512Mi"

    # Pod scheduling
    affinity:
      podAntiAffinity:
        preferredDuringSchedulingIgnoredDuringExecution:
          - weight: 100
            podAffinityTerm:
              labelSelector:
                matchLabels:
                  app.kubernetes.io/name: agentgateway
              topologyKey: kubernetes.io/hostname
```

### Step 5: Create Flux Kustomization

```yaml
# kubernetes/apps/ai-system/agentgateway/ks.yaml
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app agentgateway
  namespace: &namespace ai-system
spec:
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  targetNamespace: *namespace
  interval: 30m
  retryInterval: 1m
  timeout: 3m
  path: "./kubernetes/apps/ai-system/agentgateway/app"
  prune: true
  wait: false
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
  dependsOn:
    - name: agentgateway-crds
      namespace: *namespace
  postBuild:
    substituteFrom:
      - name: cluster-secrets
        kind: Secret
    substitute:
      APP: *app
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: &app agentgateway-crds
  namespace: &namespace ai-system
spec:
  commonMetadata:
    labels:
      app.kubernetes.io/name: *app
  targetNamespace: *namespace
  interval: 30m
  retryInterval: 1m
  timeout: 3m
  path: "./kubernetes/apps/ai-system/agentgateway/crds"
  prune: true
  wait: false
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
```

### Step 6: Add to ai-system Kustomization

```yaml
# kubernetes/apps/ai-system/kustomization.yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: ai-system

components:
  - ../../components/common
  - ../../components/alerts

resources:
  - ./kagent/ks.yaml
  - ./kmcp/ks.yaml
  - ./kgateway/ks.yaml
  - ./agentgateway/ks.yaml
```

---

## Manual Helm Installation

For testing or non-GitOps environments:

### Step 1: Install Gateway API CRDs

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml
```

### Step 2: Install kgateway CRDs

```bash
helm upgrade -i kgateway-crds oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds \
  --create-namespace \
  --namespace ai-system \
  --version v2.1.2
```

### Step 3: Install kgateway with AgentGateway

```bash
helm upgrade -i agentgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --namespace ai-system \
  --version v2.1.2 \
  --set agentgateway.enabled=true \
  --set controller.replicaCount=2 \
  --set controller.image.pullPolicy=IfNotPresent
```

---

## Verify Installation

### Check Pods

```bash
kubectl get pods -n ai-system -l app.kubernetes.io/name=agentgateway
```

### Check GatewayClass

```bash
kubectl get gatewayclass agentgateway
```

Expected:
```
NAME           CONTROLLER                ACCEPTED   AGE
agentgateway   kgateway.dev/kgateway     True       2m
```

### Check CRDs

```bash
kubectl get crd | grep kgateway
```

Expected CRDs:
```
backends.gateway.kgateway.dev
trafficpolicies.gateway.kgateway.dev
gatewayparameters.gateway.kgateway.dev
gatewayextensions.gateway.kgateway.dev
httplistenerpolicies.gateway.kgateway.dev
backendconfigpolicies.gateway.kgateway.dev
directresponses.gateway.kgateway.dev
```

---

## Helm Values Reference

| Parameter | Description | Default |
|-----------|-------------|---------|
| `agentgateway.enabled` | Enable AgentGateway data plane | `false` |
| `controller.replicaCount` | Number of controller replicas | `1` |
| `controller.logLevel` | Log level (debug, info, warn, error) | `info` |
| `controller.image.pullPolicy` | Image pull policy | `IfNotPresent` |
| `controller.resources.requests.cpu` | CPU request | `100m` |
| `controller.resources.requests.memory` | Memory request | `128Mi` |
| `controller.resources.limits.cpu` | CPU limit | `500m` |
| `controller.resources.limits.memory` | Memory limit | `512Mi` |

Full values reference: https://kgateway.dev/docs/agentgateway/latest/reference/helm/kgateway/

---

## Upgrade Procedures

### With Flux CD

Update the `tag` in `ocirepository.yaml`:

```yaml
spec:
  ref:
    tag: v2.1.3  # New version
```

Flux will automatically reconcile the change.

### With Helm

```bash
# Get current values
helm get values agentgateway -n ai-system > current-values.yaml

# Upgrade
helm upgrade agentgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --namespace ai-system \
  --version v2.1.3 \
  -f current-values.yaml

# Rollback if needed
helm rollback agentgateway -n ai-system
```

---

## References

- [kgateway Helm Installation](https://kgateway.dev/docs/agentgateway/main/install/helm/)
- [kgateway Helm Chart Reference](https://kgateway.dev/docs/agentgateway/latest/reference/helm/kgateway/)
- [Flux HelmRelease Documentation](https://fluxcd.io/flux/components/helm/helmreleases/)

---

*See [03-gateway-setup.md](./03-gateway-setup.md) for Gateway resource configuration.*
