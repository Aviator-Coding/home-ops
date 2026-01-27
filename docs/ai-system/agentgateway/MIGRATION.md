# Migration Guide

> **Upgrading AgentGateway versions, migrating protocols, and handling breaking changes.**

## Overview

This guide covers:
- Version upgrades within kgateway
- Protocol migrations (SSE → Streamable HTTP)
- Breaking changes and deprecations
- Rollback procedures

---

## Version Compatibility Matrix

| kgateway Version | AgentGateway | MCP Spec | A2A Spec | Gateway API | Status |
|------------------|--------------|----------|----------|-------------|--------|
| 2.0.x | 1.0 | June 2025 | v0.1 | v1.0 | EOL |
| 2.1.x | 1.1 | June 2025 | v0.2 | v1.1 | Current |
| 2.2.x | 1.2 | March 2026 | v1.0 | v1.2 | Beta |
| 3.0.x (planned) | 2.0 | March 2026+ | v1.0+ | v1.2 | Future |

---

## Upgrade Procedures

### Minor Version Upgrade (2.1.x → 2.1.y)

Minor versions are backward compatible. Safe for rolling updates.

```bash
# 1. Update Flux HelmRelease version
kubectl patch helmrelease agentgateway -n ai-system --type merge -p '
spec:
  chart:
    spec:
      version: "2.1.3"
'

# 2. Wait for rollout
flux reconcile helmrelease agentgateway -n ai-system
kubectl rollout status deployment/agentgateway-proxy -n ai-system

# 3. Verify
kubectl get gateway -n ai-system
curl -s http://ai.sklab.dev/health
```

### Feature Version Upgrade (2.1.x → 2.2.x)

Feature versions may have new CRDs or configuration changes.

```bash
# 1. Review release notes
# https://github.com/kgateway-dev/kgateway/releases

# 2. Backup current configuration
kubectl get gateway,httproute,backend,trafficpolicy -n ai-system -o yaml > backup.yaml

# 3. Update CRDs first
kubectl patch helmrelease agentgateway-crds -n ai-system --type merge -p '
spec:
  chart:
    spec:
      version: "2.2.0"
'
flux reconcile helmrelease agentgateway-crds -n ai-system

# 4. Wait for CRDs
kubectl wait --for condition=established crd/backends.gateway.kgateway.dev --timeout=60s

# 5. Update main release
kubectl patch helmrelease agentgateway -n ai-system --type merge -p '
spec:
  chart:
    spec:
      version: "2.2.0"
'
flux reconcile helmrelease agentgateway -n ai-system

# 6. Verify all resources reconciled
kubectl get gateway,httproute,backend -n ai-system
```

### Major Version Upgrade (2.x → 3.x)

Major versions may have breaking changes requiring configuration updates.

**Pre-upgrade checklist:**

- [ ] Read full release notes and migration guide
- [ ] Test in staging environment
- [ ] Backup all configurations
- [ ] Schedule maintenance window
- [ ] Prepare rollback plan

```bash
# 1. Full backup
kubectl get all,gateway,httproute,backend,trafficpolicy,gatewayparameters -n ai-system -o yaml > full-backup.yaml

# 2. Check deprecated features
kubectl get backend -n ai-system -o yaml | grep -i deprecated

# 3. Apply configuration migrations (see Breaking Changes section)

# 4. Upgrade CRDs
kubectl apply -f https://github.com/kgateway-dev/kgateway/releases/download/v3.0.0/kgateway-crds.yaml

# 5. Upgrade controller and proxy
helm upgrade agentgateway kgateway/kgateway \
  --namespace ai-system \
  --version 3.0.0 \
  --values values.yaml

# 6. Verify
kubectl get pods -n ai-system
kubectl logs -n ai-system -l app.kubernetes.io/name=kgateway-controller --tail=50
```

---

## Protocol Migrations

### SSE to Streamable HTTP

MCP is transitioning from Server-Sent Events to Streamable HTTP for bidirectional communication.

**Timeline:**
- June 2025: SSE is primary, Streamable HTTP experimental
- January 2026: Both supported equally
- June 2026: SSE deprecated, Streamable HTTP preferred
- January 2027: SSE removed

**Migration steps:**

1. **Update Backend protocol:**

```yaml
# Before (SSE)
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-server
spec:
  type: MCP
  mcp:
    targets:
      - name: k8s-tools
        static:
          host: kubernetes-mcp-server.ai-system.svc.cluster.local
          port: 80
          protocol: SSE  # Deprecated
```

```yaml
# After (Streamable HTTP)
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-server
spec:
  type: MCP
  mcp:
    targets:
      - name: k8s-tools
        static:
          host: kubernetes-mcp-server.ai-system.svc.cluster.local
          port: 80
          protocol: StreamableHTTP  # New
```

2. **Update MCP server:**

Ensure your MCP server supports Streamable HTTP:

```yaml
# kubernetes-mcp-server deployment
spec:
  containers:
    - name: mcp-server
      env:
        - name: MCP_TRANSPORT
          value: "streamable-http"  # Enable new transport
        - name: MCP_SSE_FALLBACK
          value: "true"  # Allow SSE clients during transition
```

3. **Update clients:**

```python
# Before: SSE client
async with aiohttp.ClientSession() as session:
    async with session.get(url, headers={"Accept": "text/event-stream"}) as resp:
        async for line in resp.content:
            # Process SSE events

# After: Streamable HTTP client
async with aiohttp.ClientSession() as session:
    async with session.post(url, json=request, headers={"Accept": "application/json"}) as resp:
        async for chunk in resp.content.iter_chunks():
            # Process streaming JSON
```

### OAuth Migration (MCP March 2026 Spec)

The March 2026 MCP specification adds OAuth 2.0 authentication.

**Before (API Key):**
```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-server
spec:
  type: MCP
  mcp:
    targets:
      - name: github-tools
        static:
          host: github-mcp.ai-system.svc.cluster.local
          port: 80
          auth:
            apiKey:
              secretRef:
                name: github-token
```

**After (OAuth):**
```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-server
spec:
  type: MCP
  mcp:
    targets:
      - name: github-tools
        static:
          host: github-mcp.ai-system.svc.cluster.local
          port: 80
          auth:
            oauth:
              tokenEndpoint: https://github.com/login/oauth/access_token
              clientCredentials:
                secretRef:
                  name: github-oauth-credentials
              scopes:
                - repo
                - read:org
```

---

## Breaking Changes

### 2.1.x → 2.2.x

#### 1. TrafficPolicy AI config restructure

```yaml
# Before (2.1.x)
spec:
  ai:
    routeType: CHAT
    promptGuard:
      request:
        - regex:
            action: Reject
            matches:
              - pattern: "..."

# After (2.2.x)
spec:
  ai:
    chat:
      routeType: CHAT
    security:
      promptGuard:
        request:
          - regex:
              action: Reject
              matches:
                - pattern: "..."
```

#### 2. Backend priority group syntax

```yaml
# Before (2.1.x)
spec:
  ai:
    priorityGroups:
    - providers:
      - name: openai
        openai:
          model: "gpt-4"

# After (2.2.x)
spec:
  ai:
    routing:
      priorityGroups:
      - backends:
        - name: openai
          provider:
            openai:
              model: "gpt-4"
```

### 2.2.x → 3.0.x (Planned)

#### 1. Gateway API v1.2 requirement

Gateway API v1.2 becomes minimum requirement:

```yaml
# Update GatewayClass
apiVersion: gateway.networking.k8s.io/v1
kind: GatewayClass  # Was v1beta1
```

#### 2. Session management API changes

```yaml
# Before
spec:
  ai:
    session:
      enabled: true
      timeout: 1h

# After
spec:
  ai:
    sessions:
      mode: stateful
      config:
        timeout: 1h
        storage:
          type: token
```

---

## Rollback Procedures

### Quick Rollback (Same Major Version)

```bash
# 1. Identify previous version
helm history agentgateway -n ai-system

# 2. Rollback Helm release
helm rollback agentgateway 1 -n ai-system

# 3. Verify pods restarted
kubectl rollout status deployment/agentgateway-proxy -n ai-system
```

### Full Rollback (With CRD Changes)

```bash
# 1. Scale down controller
kubectl scale deployment/kgateway-controller -n ai-system --replicas=0

# 2. Restore backed up resources
kubectl apply -f full-backup.yaml

# 3. Rollback CRDs (if changed)
kubectl apply -f https://github.com/kgateway-dev/kgateway/releases/download/v2.1.2/kgateway-crds.yaml

# 4. Rollback Helm release
helm rollback agentgateway -n ai-system

# 5. Scale up controller
kubectl scale deployment/kgateway-controller -n ai-system --replicas=1

# 6. Verify
kubectl get gateway,backend -n ai-system
```

### Emergency Rollback

If gateway is completely non-functional:

```bash
# 1. Route traffic away (if possible)
kubectl patch gateway agentgateway -n ai-system --type merge -p '
spec:
  listeners:
    - name: http
      port: 80
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: None  # Block all routes
'

# 2. Delete and recreate from backup
kubectl delete helmrelease agentgateway agentgateway-crds -n ai-system
kubectl apply -f full-backup.yaml
flux reconcile helmrelease agentgateway-crds -n ai-system
flux reconcile helmrelease agentgateway -n ai-system
```

---

## Deprecation Notices

### Currently Deprecated

| Feature | Deprecated In | Removed In | Alternative |
|---------|---------------|------------|-------------|
| SSE protocol for MCP | 2.1.0 | 3.0.0 | Streamable HTTP |
| `inline` auth tokens | 2.0.0 | 2.2.0 | SecretRef |
| `v1alpha1` Backend | 2.1.0 | 3.0.0 | `v1beta1` Backend |

### Planned Deprecations

| Feature | Target Version | Alternative |
|---------|----------------|-------------|
| Per-provider Backend type | 3.0.0 | Unified Backend with provider config |
| CEL v1 expressions | 3.0.0 | CEL v2 with AI-specific functions |

---

## Pre-Migration Validation

### Schema Validation Script

```bash
#!/bin/bash
# validate-migration.sh

echo "Checking for deprecated features..."

# Check SSE protocol usage
SSE_COUNT=$(kubectl get backend -A -o yaml | grep -c "protocol: SSE" || true)
if [ "$SSE_COUNT" -gt 0 ]; then
  echo "WARNING: Found $SSE_COUNT backends using deprecated SSE protocol"
  kubectl get backend -A -o yaml | grep -B5 "protocol: SSE"
fi

# Check inline auth tokens
INLINE_COUNT=$(kubectl get backend -A -o yaml | grep -c "authToken:$" || true)
if [ "$INLINE_COUNT" -gt 0 ]; then
  echo "WARNING: Found backends with inline auth (check for non-SecretRef)"
fi

# Check v1alpha1 resources
ALPHA_COUNT=$(kubectl get backend.v1alpha1.gateway.kgateway.dev -A 2>/dev/null | wc -l || echo "0")
if [ "$ALPHA_COUNT" -gt 1 ]; then
  echo "INFO: Found $ALPHA_COUNT v1alpha1 backends (will need migration to v1beta1)"
fi

echo "Validation complete."
```

### Dry-Run Upgrade

```bash
# Test upgrade without applying
helm upgrade agentgateway kgateway/kgateway \
  --namespace ai-system \
  --version 2.2.0 \
  --values values.yaml \
  --dry-run
```

---

## Post-Migration Verification

```bash
# 1. Check all resources are Accepted
kubectl get gateway,httproute,backend -n ai-system

# 2. Verify routing
curl -v http://ai.sklab.dev/health
curl -v http://ai.sklab.dev/openai/v1/models -H "Authorization: Bearer $TOKEN"

# 3. Check metrics
kubectl port-forward -n ai-system svc/agentgateway 9092:9092 &
curl localhost:9092/metrics | grep agentgateway

# 4. Verify logs are clean
kubectl logs -n ai-system -l app.kubernetes.io/name=kgateway-controller --tail=100 | grep -i error

# 5. Run smoke tests
./smoke-tests.sh
```

---

## References

- [kgateway Release Notes](https://github.com/kgateway-dev/kgateway/releases)
- [Gateway API Upgrades](https://gateway-api.sigs.k8s.io/guides/migrating-from-ingress/)
- [MCP Specification Changelog](https://modelcontextprotocol.io/changelog)
- [Helm Upgrade Best Practices](https://helm.sh/docs/howto/charts_tips_and_tricks/)

---

*See [12-troubleshooting.md](./12-troubleshooting.md) for resolving upgrade issues.*
