# Home-Ops Cluster Deployment

> **Complete deployment manifests for AgentGateway in the Home-Ops cluster with Flux CD, External Secrets, and production hardening.**

## Overview

This document provides production-ready manifests for deploying AgentGateway in the Home-Ops cluster, integrating with:

- **Flux CD** for GitOps
- **External Secrets Operator** with 1Password
- **Cilium** for LoadBalancer IP assignment
- **cert-manager** for TLS certificates
- **Prometheus/Grafana** for monitoring

---

## Directory Structure

```
kubernetes/apps/ai-system/agentgateway/
├── ks.yaml
├── crds/
│   ├── kustomization.yaml
│   ├── helmrelease.yaml
│   └── ocirepository.yaml
└── app/
    ├── kustomization.yaml
    ├── helmrelease.yaml
    ├── ocirepository.yaml
    ├── gateway.yaml
    ├── externalsecret.yaml
    ├── backends.yaml
    ├── httproutes.yaml
    ├── trafficpolicies.yaml
    ├── networkpolicy.yaml
    ├── pdb.yaml
    └── servicemonitor.yaml
```

---

## Flux Kustomization

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
    - name: external-secrets-stores
      namespace: security
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
  wait: true
  sourceRef:
    kind: GitRepository
    name: flux-system
    namespace: flux-system
```

---

## CRDs Installation

### OCI Repository

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

### HelmRelease

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

### Kustomization

```yaml
# kubernetes/apps/ai-system/agentgateway/crds/kustomization.yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ocirepository.yaml
  - helmrelease.yaml
```

---

## Application Installation

### OCI Repository

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

### HelmRelease

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
    crds: Skip
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
    crds: Skip
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
    agentgateway:
      enabled: true

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

---

## External Secrets

```yaml
# kubernetes/apps/ai-system/agentgateway/app/externalsecret.yaml
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: &name llm-provider-secrets
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: *name
    template:
      data:
        OPENAI_API_KEY: "{{ .OPENAI_API_KEY }}"
        ANTHROPIC_API_KEY: "{{ .ANTHROPIC_API_KEY }}"
        GOOGLE_AI_API_KEY: "{{ .GOOGLE_AI_API_KEY }}"
  dataFrom:
    - extract:
        key: agentgateway
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: &name openai-secret
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: *name
    template:
      data:
        Authorization: "{{ .OPENAI_API_KEY }}"
  dataFrom:
    - extract:
        key: agentgateway
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: &name anthropic-secret
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: *name
    template:
      data:
        Authorization: "{{ .ANTHROPIC_API_KEY }}"
  dataFrom:
    - extract:
        key: agentgateway
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: &name google-secret
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: *name
    template:
      data:
        Authorization: "{{ .GOOGLE_AI_API_KEY }}"
  dataFrom:
    - extract:
        key: agentgateway
```

---

## Gateway

```yaml
# kubernetes/apps/ai-system/agentgateway/app/gateway.yaml
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  labels:
    app: agentgateway
  annotations:
    external-dns.alpha.kubernetes.io/target: "ai.${SECRET_DOMAIN}"
    gatus.home-operations.com/endpoint: |-
      group: ai-system
      guarded: true
      ui:
        hide-hostname: true
        hide-url: true
spec:
  gatewayClassName: agentgateway
  infrastructure:
    annotations:
      lbipam.cilium.io/ips: "10.50.0.30"
      external-dns.alpha.kubernetes.io/hostname: "ai.${SECRET_DOMAIN}"
  listeners:
    - name: http
      protocol: HTTP
      port: 80
      allowedRoutes:
        namespaces:
          from: Same
    - name: https
      protocol: HTTPS
      port: 443
      allowedRoutes:
        namespaces:
          from: All
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: sklab-dev-production-tls
```

---

## Backends

```yaml
# kubernetes/apps/ai-system/agentgateway/app/backends.yaml
---
# OpenAI Backend
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai
spec:
  type: AI
  ai:
    llm:
      openai:
        authToken:
          kind: SecretRef
          secretRef:
            name: openai-secret
        model: "gpt-4"
---
# Anthropic Backend
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: anthropic
spec:
  type: AI
  ai:
    llm:
      anthropic:
        authToken:
          kind: SecretRef
          secretRef:
            name: anthropic-secret
        model: "claude-3-5-sonnet-20241022"
        apiVersion: "2023-06-01"
---
# Google Gemini Backend
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: gemini
spec:
  type: AI
  ai:
    llm:
      gemini:
        apiVersion: v1beta
        authToken:
          kind: SecretRef
          secretRef:
            name: google-secret
        model: gemini-2.5-flash-lite
---
# LiteLLM Backend (Static)
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: litellm
spec:
  type: Static
  static:
    hosts:
      - host: litellm.ai.svc.cluster.local
        port: 4000
---
# Cost-Optimized Failover Backend
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: llm-failover
spec:
  type: AI
  ai:
    priorityGroups:
      - providers:
        - name: gpt-3.5
          openai:
            model: "gpt-3.5-turbo"
            authToken:
              kind: SecretRef
              secretRef:
                name: openai-secret
        - name: claude-haiku
          anthropic:
            model: "claude-3-5-haiku-20241022"
            apiVersion: "2023-06-01"
            authToken:
              kind: SecretRef
              secretRef:
                name: anthropic-secret
      - providers:
        - name: gpt-4
          openai:
            model: "gpt-4"
            authToken:
              kind: SecretRef
              secretRef:
                name: openai-secret
```

---

## HTTPRoutes

```yaml
# kubernetes/apps/ai-system/agentgateway/app/httproutes.yaml
---
# HTTPS Redirect
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: https-redirect
spec:
  parentRefs:
    - name: agentgateway
      sectionName: http
  rules:
    - filters:
        - type: RequestRedirect
          requestRedirect:
            scheme: https
            statusCode: 301
---
# LLM Routes
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-routes
spec:
  parentRefs:
    - name: agentgateway
      sectionName: https
  rules:
    # OpenAI
    - matches:
        - path:
            type: PathPrefix
            value: /openai
      filters:
        - type: URLRewrite
          urlRewrite:
            path:
              type: ReplaceFullPath
              replaceFullPath: /v1/chat/completions
      backendRefs:
        - name: openai
          group: gateway.kgateway.dev
          kind: Backend
    # Anthropic
    - matches:
        - path:
            type: PathPrefix
            value: /anthropic
      backendRefs:
        - name: anthropic
          group: gateway.kgateway.dev
          kind: Backend
    # Gemini
    - matches:
        - path:
            type: PathPrefix
            value: /gemini
      backendRefs:
        - name: gemini
          group: gateway.kgateway.dev
          kind: Backend
    # LiteLLM (unified)
    - matches:
        - path:
            type: PathPrefix
            value: /v1
      backendRefs:
        - name: litellm
          group: gateway.kgateway.dev
          kind: Backend
    # Failover route
    - matches:
        - path:
            type: PathPrefix
            value: /auto
      backendRefs:
        - name: llm-failover
          group: gateway.kgateway.dev
          kind: Backend
```

---

## Traffic Policies

```yaml
# kubernetes/apps/ai-system/agentgateway/app/trafficpolicies.yaml
---
# Prompt Guards
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: prompt-guards
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  ai:
    promptGuard:
      request:
        - regex:
            action: Reject
            matches:
              - pattern: "ignore.*previous.*instructions"
                name: "prompt-injection-1"
              - pattern: "(?i)forget.*everything"
                name: "prompt-injection-2"
          response:
            message: "Request blocked due to policy violation"
      response:
        - regex:
            action: Mask
            builtins:
              - CREDIT_CARD
              - EMAIL
              - SSN
---
# Rate Limiting
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: rate-limits
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  rateLimit:
    local:
      - maxTokens: 100
        tokensPerFill: 10
        fillInterval: 1s
        type: requests
  timeout: 60s
  retry:
    numRetries: 2
    perTryTimeout: 30s
    retryOn:
      - 5xx
      - connect-failure
```

---

## Network Policy

```yaml
# kubernetes/apps/ai-system/agentgateway/app/networkpolicy.yaml
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agentgateway
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: agentgateway
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: network
      ports:
        - protocol: TCP
          port: 8080
        - protocol: TCP
          port: 443
    - from:
        - ipBlock:
            cidr: 10.0.0.0/8
      ports:
        - protocol: TCP
          port: 9093
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
      ports:
        - protocol: TCP
          port: 9092
  egress:
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - protocol: TCP
          port: 443
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ai
      ports:
        - protocol: TCP
          port: 4000
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: UDP
          port: 53
```

---

## Pod Disruption Budget

```yaml
# kubernetes/apps/ai-system/agentgateway/app/pdb.yaml
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: agentgateway
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app.kubernetes.io/name: agentgateway
```

---

## ServiceMonitor

```yaml
# kubernetes/apps/ai-system/agentgateway/app/servicemonitor.yaml
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: agentgateway
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: agentgateway
  endpoints:
    - port: metrics
      path: /metrics
      interval: 30s
      scrapeTimeout: 10s
  namespaceSelector:
    matchNames:
      - ai-system
```

---

## Kustomization

```yaml
# kubernetes/apps/ai-system/agentgateway/app/kustomization.yaml
---
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - ocirepository.yaml
  - helmrelease.yaml
  - externalsecret.yaml
  - gateway.yaml
  - backends.yaml
  - httproutes.yaml
  - trafficpolicies.yaml
  - networkpolicy.yaml
  - pdb.yaml
  - servicemonitor.yaml
```

---

## 1Password Configuration

Create the following item in 1Password vault `Homelab`:

**Item Name:** `agentgateway`

**Fields:**
- `OPENAI_API_KEY`: Your OpenAI API key
- `ANTHROPIC_API_KEY`: Your Anthropic API key
- `GOOGLE_AI_API_KEY`: Your Google AI Studio API key

---

## Deployment Verification

```bash
# Check Flux Kustomizations
flux get kustomizations -n ai-system | grep agentgateway

# Check HelmReleases
flux get helmreleases -n ai-system | grep agentgateway

# Check pods
kubectl get pods -n ai-system -l app.kubernetes.io/name=agentgateway

# Check Gateway status
kubectl get gateway agentgateway -n ai-system

# Check ExternalSecrets
kubectl get externalsecrets -n ai-system

# Check Backend status
kubectl get backends -n ai-system

# Test endpoint
curl -k https://ai.sklab.dev/openai \
  -H "content-type: application/json" \
  -d '{"model":"gpt-4","messages":[{"role":"user","content":"Hello"}]}'
```

---

## References

- [Flux CD Documentation](https://fluxcd.io/flux/)
- [External Secrets Operator](https://external-secrets.io/)
- [Cilium LoadBalancer IPAM](https://docs.cilium.io/en/stable/network/lb-ipam/)

---

*See [12-troubleshooting.md](./12-troubleshooting.md) for common issues and solutions.*
