# Security Guide

> **Comprehensive security configuration including RBAC, prompt guards, API key management, and access control.**

## Overview

Agentgateway provides multiple security layers:

| Layer | Purpose | Implementation |
|-------|---------|----------------|
| **Authentication** | Verify caller identity | API keys, tokens, headers |
| **Authorization** | Control access to resources | CEL-based RBAC |
| **Content Filtering** | Protect LLM interactions | Prompt guards |
| **Data Protection** | Mask sensitive output | Response masking |

---

## CEL-Based RBAC

### Overview

Agentgateway proxies use CEL (Common Expression Language) expressions to match requests on specific parameters such as headers, source addresses, and more.

### Basic RBAC Policy

Require a specific header value:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: rbac-policy
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  rbac:
    policy:
      matchExpressions:
        - "request.headers['x-api-key'] == 'valid-api-key'"
```

### Access Control Behavior

| Condition | HTTP Response |
|-----------|---------------|
| CEL expression matches | 200 (request proceeds) |
| CEL expression fails | 403 Forbidden |

### CEL Expression Examples

**Header Matching:**
```yaml
matchExpressions:
  # Exact header value
  - "request.headers['x-llm'] == 'gemini'"

  # Header exists
  - "has(request.headers['authorization'])"

  # Header contains value
  - "request.headers['x-api-key'].contains('sk-')"
```

**Multiple Conditions (AND):**
```yaml
matchExpressions:
  - "request.headers['x-api-key'] == 'valid-key'"
  - "request.headers['x-tenant'] == 'acme-corp'"
```

**Source IP Matching:**
```yaml
matchExpressions:
  - "source.address == '10.0.0.0/8'"
```

### Complete RBAC Example

```yaml
# Backend
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: google
  namespace: kgateway-system
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
# HTTPRoute
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: google
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /gemini
    backendRefs:
    - name: google
      group: gateway.kgateway.dev
      kind: Backend
---
# RBAC Policy
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: gemini-rbac
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: google
  rbac:
    policy:
      matchExpressions:
        - "request.headers['x-llm'] == 'gemini'"
```

**Test Access:**

```bash
# Allowed - correct header
curl "localhost:8080/gemini" \
  -H "content-type: application/json" \
  -H "x-llm: gemini" \
  -d '{"messages": [{"role": "user", "content": "Hello"}]}'

# Denied - missing/wrong header
curl "localhost:8080/gemini" \
  -H "content-type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello"}]}'
# Returns: 403 Forbidden - authorization failed
```

---

## Prompt Guards

### Overview

Prompt guards ensure that LLM interactions are secure, appropriate, and aligned with intended use. They filter, block, monitor, and control LLM inputs and outputs.

### Request Protection

Block unwanted requests based on content patterns:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: openai-prompt-guard
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptGuard:
      request:
        customResponse:
          message: "Rejected due to inappropriate content"
        regex:
          action: REJECT
          matches:
          - pattern: "credit card"
            name: "CC"
          - pattern: "social security"
            name: "SSN"
          - pattern: "password"
            name: "Password"
```

**Behavior:**
- Requests containing flagged strings receive 403 Forbidden
- Custom message explains rejection reason

### Response Masking

Mask sensitive data in LLM responses:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: response-masking
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptGuard:
      response:
        regex:
          action: MASK
          builtins:
          - CREDIT_CARD
          - PHONE_NUMBER
          - EMAIL
```

**Example:**

Original response:
```
Call me at 555-123-4567 or email john@example.com
```

Masked response:
```
Call me at <PHONE_NUMBER> or email <EMAIL>
```

### Built-in Pattern Detectors

**Request Patterns:**
| Pattern | Description |
|---------|-------------|
| `CreditCard` | Credit card numbers |
| `Email` | Email addresses |
| `PhoneNumber` | Phone numbers |
| `Ssn` | Social Security Numbers |
| `CaSin` | Canadian Social Insurance Numbers |

**Response Patterns:**
| Pattern | Replacement Token |
|---------|-------------------|
| `CREDIT_CARD` | `<CREDIT_CARD>` |
| `PHONE_NUMBER` | `<PHONE_NUMBER>` |
| `EMAIL` | `<EMAIL>` |
| `SSN` | `<SSN>` |

### Custom Regex Patterns

```yaml
ai:
  promptGuard:
    request:
      regex:
        action: REJECT
        matches:
        # Custom patterns
        - pattern: "\\b[A-Z]{2}\\d{6}\\b"
          name: "EmployeeID"
        - pattern: "confidential|classified|secret"
          name: "ClassificationLevel"
    response:
      regex:
        action: MASK
        matches:
        - pattern: "\\b\\d{3}-\\d{2}-\\d{4}\\b"
          name: "SSN_PATTERN"
```

### Combined Request and Response Guards

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: comprehensive-guard
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptGuard:
      request:
        customResponse:
          message: "Request blocked due to policy violation"
        regex:
          action: REJECT
          matches:
          - pattern: "ignore previous instructions"
            name: "PromptInjection"
          - pattern: "system prompt"
            name: "SystemPromptLeak"
          builtins:
          - CreditCard
          - Ssn
      response:
        regex:
          action: MASK
          builtins:
          - CREDIT_CARD
          - PHONE_NUMBER
          - EMAIL
          - SSN
```

---

## API Key Management

### Authentication Methods

| Method | Security Level | Use Case |
|--------|----------------|----------|
| Inline Token | Low | Testing only |
| SecretRef | Medium | Development, staging, controlled production |
| Passthrough | High | Federated identity, client-provided tokens |

### Inline Token (Not Recommended)

```yaml
spec:
  ai:
    llm:
      openai:
        authToken:
          kind: Inline
          inline: "sk-your-api-key"  # Exposed in YAML - avoid
        model: "gpt-3.5-turbo"
```

### SecretRef (Recommended)

```yaml
# Create secret
apiVersion: v1
kind: Secret
metadata:
  name: openai-secret
  namespace: kgateway-system
type: Opaque
stringData:
  Authorization: "sk-your-api-key"
---
# Reference in Backend
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai
  namespace: kgateway-system
spec:
  type: AI
  ai:
    llm:
      openai:
        authToken:
          kind: SecretRef
          secretRef:
            name: openai-secret
        model: "gpt-3.5-turbo"
```

### Passthrough Authentication

Pass client tokens directly to the LLM provider:

```yaml
spec:
  ai:
    llm:
      openai:
        authToken:
          kind: Passthrough
        model: "gpt-3.5-turbo"
```

Client provides token:
```bash
curl "localhost:8080/openai" \
  -H "Authorization: Bearer sk-client-api-key" \
  -H "content-type: application/json" \
  -d '{"model": "gpt-3.5-turbo", "messages": [...]}'
```

---

## Secret Rotation

### Using External Secrets Operator

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: openai-secret
  namespace: kgateway-system
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: openai-secret
    creationPolicy: Owner
  data:
  - secretKey: Authorization
    remoteRef:
      key: secret/llm/openai
      property: api_key
```

### Manual Rotation

```bash
# Update secret
kubectl create secret generic openai-secret \
  --namespace kgateway-system \
  --from-literal=Authorization="sk-new-api-key" \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart proxy to pick up new secret (if not using dynamic refresh)
kubectl rollout restart deployment/agentgateway-proxy -n kgateway-system
```

---

## Network Policies

### Restrict Egress to LLM Providers

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: llm-egress
  namespace: kgateway-system
spec:
  podSelector:
    matchLabels:
      gateway: agentgateway-proxy
  policyTypes:
  - Egress
  egress:
  # OpenAI
  - to:
    - ipBlock:
        cidr: 0.0.0.0/0
    ports:
    - protocol: TCP
      port: 443
  # DNS
  - to:
    - namespaceSelector: {}
      podSelector:
        matchLabels:
          k8s-app: kube-dns
    ports:
    - protocol: UDP
      port: 53
```

### Restrict Ingress to Gateway

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: gateway-ingress
  namespace: kgateway-system
spec:
  podSelector:
    matchLabels:
      gateway: agentgateway-proxy
  policyTypes:
  - Ingress
  ingress:
  # Allow from specific namespaces
  - from:
    - namespaceSelector:
        matchLabels:
          gateway-access: "true"
    ports:
    - protocol: TCP
      port: 80
    - protocol: TCP
      port: 443
```

---

## Audit Logging

### Enable Access Logging

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: HTTPListenerPolicy
metadata:
  name: access-logging
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: agentgateway-proxy
  accessLog:
  - fileSink:
      path: /dev/stdout
      jsonFormat:
        timestamp: "%START_TIME%"
        request_id: "%REQ(X-REQUEST-ID)%"
        method: "%REQ(:METHOD)%"
        path: "%REQ(:PATH)%"
        response_code: "%RESPONSE_CODE%"
        duration: "%DURATION%"
        user_agent: "%REQ(USER-AGENT)%"
        x_api_key: "%REQ(X-API-KEY)%"
```

---

## Best Practices

### 1. Defense in Depth

Apply multiple security layers:

```yaml
# Layer 1: RBAC
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: layer-1-rbac
spec:
  rbac:
    policy:
      matchExpressions:
        - "has(request.headers['x-api-key'])"
---
# Layer 2: Prompt Guards
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: layer-2-guards
spec:
  ai:
    promptGuard:
      request:
        regex:
          action: REJECT
          builtins: [CreditCard, Ssn]
---
# Layer 3: Response Masking
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: layer-3-masking
spec:
  ai:
    promptGuard:
      response:
        regex:
          action: MASK
          builtins: [CREDIT_CARD, EMAIL]
```

### 2. Principle of Least Privilege

- Use SecretRef instead of Inline tokens
- Restrict secret access with RBAC
- Limit namespace access for routes

### 3. Regular Key Rotation

- Rotate API keys every 90 days
- Use External Secrets Operator for automation
- Monitor for exposed credentials

### 4. Comprehensive Logging

- Enable access logging for all routes
- Include request IDs for tracing
- Log security-relevant headers

---

*See [08-observability.md](./08-observability.md) for monitoring and tracing configuration.*
