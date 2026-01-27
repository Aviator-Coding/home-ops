# Security

> **Comprehensive security configuration including RBAC, prompt guards, API key management, and External Secrets integration.**

## Overview

AgentGateway provides multiple security layers:

| Layer | Feature | Description |
|-------|---------|-------------|
| **Authentication** | API Keys, JWT, Passthrough | Verify client identity |
| **Authorization** | CEL-based RBAC | Fine-grained access control |
| **Content Filtering** | Prompt Guards | Block/mask sensitive content |
| **Secrets Management** | External Secrets | Secure credential storage |
| **Network** | TLS, Network Policies | Encrypt and isolate traffic |

---

## API Key Authentication

### Using External Secrets (Recommended)

Store LLM API keys in 1Password and sync via External Secrets:

```yaml
# externalsecret.yaml
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: llm-api-keys
  namespace: ai-system
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: llm-api-keys
    template:
      data:
        OPENAI_API_KEY: "{{ .OPENAI_API_KEY }}"
        ANTHROPIC_API_KEY: "{{ .ANTHROPIC_API_KEY }}"
        GOOGLE_AI_API_KEY: "{{ .GOOGLE_AI_API_KEY }}"
  dataFrom:
    - extract:
        key: agentgateway-credentials
```

### Provider-Specific Secrets

Create separate secrets per provider for isolation:

```yaml
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: openai-secret
  namespace: ai-system
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: openai-secret
    template:
      data:
        Authorization: "{{ .OPENAI_API_KEY }}"
  dataFrom:
    - extract:
        key: openai-credentials
---
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: anthropic-secret
  namespace: ai-system
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: anthropic-secret
    template:
      data:
        Authorization: "{{ .ANTHROPIC_API_KEY }}"
  dataFrom:
    - extract:
        key: anthropic-credentials
```

### Reference in Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai
  namespace: ai-system
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
```

---

## JWT Authentication

Validate JWT tokens from identity providers:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: jwt-auth
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway
  traffic:
    jwtAuthentication:
      mode: Strict  # Strict, Optional, or Permissive
      providers:
        - issuer: "https://auth.sklab.dev/realms/ai"
          audiences: ["agentgateway"]
          jwks:
            remote:
              jwksPath: "/protocol/openid-connect/certs"
              cacheDuration: "5m"
              backendRef:
                kind: Service
                name: keycloak
                namespace: security
                port: 8080
```

### JWT Validation Modes

| Mode | Behavior |
|------|----------|
| `Strict` | Requires valid JWT for all requests |
| `Optional` | Validates if present, allows anonymous |
| `Permissive` | Never rejects, even with invalid JWT |

---

## CEL-Based RBAC

Use Common Expression Language (CEL) for fine-grained authorization:

### Basic Header-Based RBAC

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: rbac-policy
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  rbac:
    policy:
      matchExpressions:
        # Require API key header
        - 'request.headers["x-api-key"] != ""'
        # OR specific LLM header
        - 'request.headers["x-llm"] in ["openai", "anthropic", "gemini"]'
```

### JWT Claims-Based RBAC

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: jwt-rbac
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  rbac:
    policy:
      matchExpressions:
        # Admin users get full access
        - 'jwt.role == "admin"'
        # Regular users only GPT-3.5
        - 'jwt.role == "user" && request.path.startsWith("/openai") && request.body.model == "gpt-3.5-turbo"'
```

### MCP Tool-Based RBAC

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: mcp-rbac
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mcp-route
  rbac:
    policy:
      matchExpressions:
        # Restrict dangerous tools to admins
        - 'mcp.tool.name in ["kubectl-delete", "helm-uninstall"] && jwt.role == "admin"'
        # Allow read-only tools for all authenticated users
        - 'mcp.tool.name in ["kubectl-get", "kubectl-describe", "list-pods"] && jwt.sub != ""'
```

### CEL Variables Reference

| Variable | Description |
|----------|-------------|
| `request.headers` | HTTP request headers |
| `request.path` | Request URL path |
| `request.method` | HTTP method |
| `request.body` | Request body (parsed) |
| `jwt.sub` | JWT subject claim |
| `jwt.role` | JWT role claim |
| `jwt.*` | Any JWT claim |
| `mcp.tool.name` | MCP tool being invoked |
| `mcp.tool.target` | MCP target backend |
| `source.ip` | Client IP address |

---

## Prompt Guards

Protect against prompt injection and filter sensitive content:

### Request Filtering (Block Malicious Input)

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: prompt-guards
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  ai:
    promptGuard:
      request:
        - response:
            message: "Request blocked due to policy violation"
          regex:
            action: Reject
            matches:
              # Block prompt injection attempts
              - pattern: "ignore.*previous.*instructions"
                name: "prompt-injection-1"
              - pattern: "you.*are.*now"
                name: "prompt-injection-2"
              - pattern: "(?i)forget.*everything"
                name: "prompt-injection-3"
              # Block requests for sensitive data
              - pattern: "(?i)(password|api.?key|secret|token)"
                name: "sensitive-request"
```

### Response Masking (Protect PII)

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: response-masking
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  ai:
    promptGuard:
      response:
        - regex:
            action: Mask
            builtins:
              - CREDIT_CARD
              - EMAIL
              - PHONE_NUMBER
              - SSN
            matches:
              # Custom patterns
              - pattern: "\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Z|a-z]{2,}\\b"
                name: "email-custom"
```

### Built-in PII Patterns

| Pattern | Description |
|---------|-------------|
| `CREDIT_CARD` | Credit card numbers |
| `EMAIL` | Email addresses |
| `PHONE_NUMBER` | Phone numbers |
| `SSN` | US Social Security Numbers |
| `CA_SIN` | Canadian Social Insurance Numbers |

---

## TLS Configuration

### Backend TLS (to LLM Providers)

TLS is automatic for external providers. For internal services:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: BackendConfigPolicy
metadata:
  name: tls-policy
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.kgateway.dev
      kind: Backend
      name: internal-llm
  tls:
    mode: SIMPLE
    caCertificates:
      secretRef:
        name: internal-ca-cert
```

### Mutual TLS (mTLS)

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: BackendConfigPolicy
metadata:
  name: mtls-policy
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.kgateway.dev
      kind: Backend
      name: secure-backend
  tls:
    mode: MUTUAL
    clientCertificate:
      secretRef:
        name: client-cert
    caCertificates:
      secretRef:
        name: ca-cert
```

---

## Network Policies

Restrict network access for AgentGateway:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: agentgateway-network-policy
  namespace: ai-system
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: agentgateway
  policyTypes:
    - Ingress
    - Egress
  ingress:
    # Allow from ingress controller
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: network
      ports:
        - protocol: TCP
          port: 8080
        - protocol: TCP
          port: 443
    # Allow health checks
    - from:
        - ipBlock:
            cidr: 10.0.0.0/8
      ports:
        - protocol: TCP
          port: 9093
    # Allow Prometheus scraping
    - from:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: monitoring
      ports:
        - protocol: TCP
          port: 9092
  egress:
    # Allow to LLM providers (external)
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
    # Allow to internal services
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ai-system
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: ai
      ports:
        - protocol: TCP
          port: 4000  # LiteLLM
        - protocol: TCP
          port: 8000  # MCP servers
    # Allow DNS
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: UDP
          port: 53
```

---

## External Auth Service

Integrate with external authentication services:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: external-auth
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway
  traffic:
    extAuth:
      backendRef:
        name: ext-authz-service
        namespace: security
        port: 9000
      grpc: {}
      failureModeAllow: false
```

---

## Passthrough Authentication

For federated identity (client provides token):

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai-passthrough
  namespace: ai-system
spec:
  type: AI
  ai:
    llm:
      openai:
        authToken:
          kind: Passthrough
        model: "gpt-4"
```

Client includes token:
```bash
curl "http://ai.sklab.dev/openai" \
  -H "Authorization: Bearer $USER_OPENAI_KEY" \
  -H "content-type: application/json" \
  -d '{"model": "gpt-4", "messages": [...]}'
```

---

## Security Best Practices

### 1. Use External Secrets

Never store API keys in Git. Always use External Secrets with 1Password:

```yaml
# GOOD - External Secrets
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword

# BAD - Hardcoded in YAML
stringData:
  Authorization: "sk-actual-api-key"  # NEVER DO THIS
```

### 2. Implement Defense in Depth

Combine multiple security layers:

```yaml
# Layer 1: JWT Authentication
jwtAuthentication:
  mode: Strict

# Layer 2: RBAC
rbac:
  policy:
    matchExpressions:
      - 'jwt.role == "admin"'

# Layer 3: Prompt Guards
promptGuard:
  request:
    - regex:
        action: Reject
        matches:
          - pattern: "ignore.*instructions"
```

### 3. Rotate Secrets Regularly

Configure short refresh intervals:

```yaml
spec:
  refreshInterval: 1h  # Sync from 1Password hourly
```

### 4. Monitor and Alert

Set up alerts for security events:

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: agentgateway-security-alerts
spec:
  groups:
    - name: security
      rules:
        - alert: HighRejectedRequests
          expr: sum(rate(agentgateway_rejected_requests_total[5m])) > 10
          for: 5m
          labels:
            severity: warning
```

---

## Troubleshooting

### Authentication Failures

```bash
# Check ExternalSecret status
kubectl get externalsecret -n ai-system

# Check secret sync
kubectl describe externalsecret openai-secret -n ai-system

# Verify secret exists
kubectl get secret openai-secret -n ai-system
```

### RBAC Denials

```bash
# Check TrafficPolicy status
kubectl get trafficpolicy -n ai-system -o yaml

# Check gateway logs for CEL evaluation
kubectl logs -n ai-system -l app.kubernetes.io/name=agentgateway | grep -i "rbac\|denied"
```

---

## References

- [API Key Management](https://kgateway.dev/docs/agentgateway/latest/llm/api-keys/)
- [CEL-based RBAC](https://kgateway.dev/docs/agentgateway/latest/rbac/)
- [Prompt Guards](https://kgateway.dev/docs/agentgateway/latest/llm/prompt-guards/)
- [External Secrets Operator](https://external-secrets.io/)
- [1Password Integration](https://external-secrets.io/latest/provider/1password-sdk/)

---

*See [08-observability.md](./08-observability.md) for monitoring and tracing.*
