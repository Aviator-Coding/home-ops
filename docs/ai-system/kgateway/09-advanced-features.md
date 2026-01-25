# Advanced Features Guide

> **Model failover, function calling, inference routing, prompt enrichment, and custom configuration.**

## Model Failover

### Overview

Failover keeps services running smoothly by automatically switching to a backup LLM when the primary fails or becomes unavailable.

### Priority Groups

Configure failover using priority groups:
- First-listed group has highest priority
- Models within the same group use round-robin load balancing
- Failover proceeds to next group when current group fails

### Single Provider Failover

Route to progressively different models from the same provider:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai-failover
  namespace: kgateway-system
spec:
  type: AI
  ai:
    priorityGroups:
    # Priority 1: Best model
    - providers:
      - name: openai-gpt-4.1
        openai:
          model: "gpt-4.1"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
    # Priority 2: Fallback model
    - providers:
      - name: openai-gpt-4o
        openai:
          model: "gpt-4o"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
    # Priority 3: Budget model
    - providers:
      - name: openai-gpt-3.5-turbo
        openai:
          model: "gpt-3.5-turbo"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
```

**Failover Order:** `gpt-4.1` → `gpt-4o` → `gpt-3.5-turbo`

### Multi-Provider Failover

Balance across providers with cost-based priority:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: multi-provider-failover
  namespace: kgateway-system
spec:
  type: AI
  ai:
    priorityGroups:
    # Priority 1: Cheaper models (load balanced)
    - providers:
      - name: openai-gpt-3.5-turbo
        openai:
          model: "gpt-3.5-turbo"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
      - name: claude-haiku
        anthropic:
          model: "claude-3-5-haiku-latest"
          authToken:
            kind: SecretRef
            secretRef:
              name: anthropic-secret
    # Priority 2: Premium models (load balanced)
    - providers:
      - name: openai-gpt-4.1
        openai:
          model: "gpt-4.1"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
      - name: claude-opus
        anthropic:
          model: "claude-opus-4-1"
          authToken:
            kind: SecretRef
            secretRef:
              name: anthropic-secret
```

**Strategy:** Load balance cheaper models first, failover to premium if needed.

### HTTPRoute for Failover

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: model-failover
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: kgateway-system
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /model
    filters:
    - type: URLRewrite
      urlRewrite:
        path:
          type: ReplaceFullPath
          replaceFullPath: /v1/chat/completions
    backendRefs:
    - name: multi-provider-failover
      namespace: kgateway-system
      group: gateway.kgateway.dev
      kind: Backend
```

### Test Failover

```bash
curl "localhost:8080/model" \
  -H "content-type: application/json" \
  -d '{
    "messages": [
      {"role": "user", "content": "What is kubernetes?"}
    ]
  }' | jq
```

---

## Function Calling

### Overview

Function calling extends LLM capabilities with external APIs, apps, and data. Tools consist of:
- Function name and description
- Input parameters
- LLM decides when to invoke tools

### Request Format

```bash
curl "localhost:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [
      {"role": "user", "content": "What is the weather in San Francisco?"}
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "Get the current weather for a location",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {
                "type": "string",
                "description": "City and state, e.g. San Francisco, CA"
              },
              "format": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],
                "description": "Temperature unit"
              }
            },
            "required": ["location", "format"]
          }
        }
      }
    ],
    "tool_choice": "auto"
  }' | jq
```

### Response with Tool Call

```json
{
  "choices": [
    {
      "message": {
        "role": "assistant",
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "get_weather",
              "arguments": "{\"location\":\"San Francisco, CA\",\"format\":\"fahrenheit\"}"
            }
          }
        ]
      }
    }
  ]
}
```

### Feature Interactions

| Feature | With Function Calling |
|---------|----------------------|
| **Streaming** | Compatible |
| **Semantic Caching** | Function calls NOT cached |
| **Prompt Guards** | Do NOT apply to function calls |

---

## Inference Routing

### Overview

The Kubernetes Gateway API Inference Extension enables routing to local LLM inference workloads running in your Kubernetes environment.

### Key Resources

| Resource | Purpose |
|----------|---------|
| `InferencePool` | Groups InferenceModels into routable backend |
| `InferenceModel` | Represents specific LLM configuration |

### Architecture

```
Client → Kgateway → InferencePool → InferenceModel Selection → LLM Pod
```

The InferencePool manages:
- Resource consumption across workloads
- Least-loaded routing decisions
- Criticality-based selection

### Example Configuration

```yaml
apiVersion: inference.ai.k8s.io/v1alpha1
kind: InferencePool
metadata:
  name: llm-pool
  namespace: inference
spec:
  targetPortNumber: 8080
  selector:
    matchLabels:
      app: llm-inference
---
apiVersion: inference.ai.k8s.io/v1alpha1
kind: InferenceModel
metadata:
  name: llama-7b
  namespace: inference
spec:
  modelName: "llama-7b"
  poolRef:
    name: llm-pool
  targetModel:
    name: llama-7b-chat
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: inference-route
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /inference
    backendRefs:
    - name: llm-pool
      namespace: inference
      group: inference.ai.k8s.io
      kind: InferencePool
```

---

## Prompt Enrichment

### Overview

Enrich prompts by injecting static context before or after user messages.

### Configuration

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: prompt-enrichment
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    promptEnrichment:
      prepend:
      - role: system
        content: |
          You are a helpful AI assistant for Acme Corporation.
          Always be polite and professional.
          Current date: 2024-01-15
      append:
      - role: system
        content: |
          Remember to format responses in markdown.
          Include relevant documentation links when appropriate.
```

### Field Defaults

Set default values for request fields:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: field-defaults
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  ai:
    fieldDefaults:
      - path: "model"
        value: "gpt-4"
        override: false  # Don't override if client specifies
      - path: "max_tokens"
        value: "2048"
        override: false
      - path: "temperature"
        value: "0.7"
        override: true  # Always use this value
```

---

## Custom Configuration

### Using ConfigMap

For features not yet exposed via Gateway API:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agentgateway-config
  namespace: kgateway-system
data:
  config.yaml: |
    binds:
    - name: main
      port: 3000
    listeners:
    - name: http-listener
      bind: main
      protocol: http
    routes:
    - name: health
      paths:
      - /health
      policies:
      - directResponse:
          status: 200
          body: "OK"
    - name: llm-route
      paths:
      - /llm
      policies:
      - proxy:
          upstream: openai-backend
```

### Reference via GatewayParameters

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: custom-config-params
  namespace: kgateway-system
spec:
  kube:
    agentgateway:
      customConfigMapName: agentgateway-config
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: kgateway-system
spec:
  gatewayClassName: agentgateway
  infrastructure:
    parametersRef:
      group: gateway.kgateway.dev
      kind: GatewayParameters
      name: custom-config-params
  listeners:
  - protocol: HTTP
    port: 80
    name: http
    allowedRoutes:
      namespaces:
        from: All
```

### Verification

```bash
# Check pod for ConfigMap mount
kubectl describe pod -n kgateway-system -l gateway=agentgateway-proxy | grep -A5 Volumes

# Check logs for bind initialization
kubectl logs -n kgateway-system -l gateway=agentgateway-proxy | grep "started bind"

# Test custom endpoint
curl "localhost:8080/health"
```

---

## Request/Response Transformation

### Header Transformation

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: transformed-route
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /api
    filters:
    # Add request headers
    - type: RequestHeaderModifier
      requestHeaderModifier:
        add:
        - name: X-Custom-Header
          value: "custom-value"
        set:
        - name: X-Forwarded-For
          value: "gateway"
    # Add response headers
    - type: ResponseHeaderModifier
      responseHeaderModifier:
        add:
        - name: X-Served-By
          value: "agentgateway"
    backendRefs:
    - name: backend
      port: 8080
```

### URL Rewriting

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: rewrite-route
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /v1
    filters:
    - type: URLRewrite
      urlRewrite:
        path:
          type: ReplacePrefixMatch
          replacePrefixMatch: /api/v1
    backendRefs:
    - name: api-backend
      port: 8080
```

---

## Rate Limiting

### Configure Rate Limits

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: rate-limit
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: openai
  rateLimit:
    rateLimits:
    - actions:
      - requestHeaders:
          headerName: x-api-key
          descriptorKey: api_key
      - genericKey:
          descriptorValue: per_api_key
    - actions:
      - remoteAddress: {}
      - genericKey:
          descriptorValue: per_ip
```

---

## Timeout Configuration

### Per-Route Timeouts

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-with-timeout
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /openai
    timeouts:
      request: 60s  # Total request timeout
      backendRequest: 55s  # Timeout to backend
    backendRefs:
    - name: openai
      group: gateway.kgateway.dev
      kind: Backend
```

---

## Cleanup

Remove advanced feature resources:

```bash
# Failover
kubectl delete backend openai-failover multi-provider-failover -n kgateway-system
kubectl delete httproute model-failover -n kgateway-system

# Prompt enrichment
kubectl delete trafficpolicy prompt-enrichment field-defaults -n kgateway-system

# Custom config
kubectl delete gateway agentgateway-proxy -n kgateway-system
kubectl delete gatewayparameters custom-config-params -n kgateway-system
kubectl delete configmap agentgateway-config -n kgateway-system
```

---

*See [10-api-reference.md](./10-api-reference.md) for complete API and CRD reference.*
