# Advanced Features

> **Failover, function calling, inference routing, rate limiting, and prompt enrichment.**

## Model Failover

Configure automatic failover between LLM providers using priority groups:

### Cost-Based Failover

Route to cheaper models first, fall back to premium:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: llm-cost-optimized
  namespace: ai-system
spec:
  type: AI
  ai:
    priorityGroups:
    # Priority 1: Cheapest models (load balanced)
    - providers:
      - name: gpt-3.5-turbo
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
    # Priority 2: Mid-tier models
    - providers:
      - name: gpt-4-turbo
        openai:
          model: "gpt-4-turbo"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
      - name: claude-sonnet
        anthropic:
          model: "claude-3-5-sonnet-20241022"
          apiVersion: "2023-06-01"
          authToken:
            kind: SecretRef
            secretRef:
              name: anthropic-secret
    # Priority 3: Premium fallback
    - providers:
      - name: gpt-4o
        openai:
          model: "gpt-4o"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
      - name: claude-opus
        anthropic:
          model: "claude-3-opus-20240229"
          apiVersion: "2023-06-01"
          authToken:
            kind: SecretRef
            secretRef:
              name: anthropic-secret
```

### Provider Redundancy

Failover between providers for the same capability:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: llm-redundant
  namespace: ai-system
spec:
  type: AI
  ai:
    priorityGroups:
    # Primary: OpenAI
    - providers:
      - name: openai-primary
        openai:
          model: "gpt-4"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
    # Fallback: Anthropic
    - providers:
      - name: anthropic-fallback
        anthropic:
          model: "claude-3-5-sonnet-20241022"
          apiVersion: "2023-06-01"
          authToken:
            kind: SecretRef
            secretRef:
              name: anthropic-secret
    # Last resort: Bedrock
    - providers:
      - name: bedrock-backup
        bedrock:
          model: "anthropic.claude-3-sonnet-20240229-v1:0"
          region: us-east-1
          auth:
            type: Secret
            secretRef:
              name: bedrock-secret
```

### Load Balancing Behavior

- **Within Priority Group**: Round-robin with Power of Two Random Choices
- **Between Groups**: Sequential failover on failure
- **Maximum Groups**: 32 priority groups supported

---

## Inference Routing (Local LLMs)

Route to self-hosted models with the Gateway API Inference Extension:

### Ollama Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: ollama
  namespace: ai-system
spec:
  type: AI
  ai:
    llm:
      openai:
        # Ollama uses OpenAI-compatible API
        host: ollama.ai.svc.cluster.local
        port: 11434
        model: "llama3.2"
        # No auth needed for local Ollama
```

### vLLM Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: vllm
  namespace: ai-system
spec:
  type: Static
  static:
    hosts:
      - host: vllm.ai.svc.cluster.local
        port: 8000
```

### InferencePool (Gateway API Inference Extension)

```yaml
apiVersion: inference.ai.networking.k8s.io/v1alpha1
kind: InferencePool
metadata:
  name: llama-pool
  namespace: ai-system
spec:
  selector:
    matchLabels:
      app: vllm-llama3-8b
  targetPortNumber: 8000
---
apiVersion: inference.ai.networking.k8s.io/v1alpha1
kind: InferenceModel
metadata:
  name: llama3-8b
  namespace: ai-system
spec:
  modelName: "Meta-Llama-3.1-8B-Instruct"
  criticality: Standard
  poolRef:
    name: llama-pool
```

### Hybrid Cloud/Local Routing

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: hybrid-llm
  namespace: ai-system
spec:
  type: AI
  ai:
    priorityGroups:
    # Priority 1: Local inference (cost-free)
    - providers:
      - name: local-llama
        openai:
          host: ollama.ai.svc.cluster.local
          port: 11434
          model: "llama3.2:70b"
    # Priority 2: Cloud fallback
    - providers:
      - name: cloud-gpt4
        openai:
          model: "gpt-4"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
```

---

## Rate Limiting

### Request-Based Rate Limiting

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: request-rate-limit
  namespace: ai-system
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
```

### Token-Based Rate Limiting

Limit by LLM tokens (AI-specific):

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: token-rate-limit
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  rateLimit:
    local:
      - maxTokens: 100000
        tokensPerFill: 100000
        fillInterval: 1h
        type: tokens
```

**Token Evaluation:**
1. Request time: Estimate tokens from prompt
2. Response time: Adjust based on actual token count

### Per-User Rate Limiting

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: per-user-rate-limit
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  rateLimit:
    local:
      - maxTokens: 1000
        tokensPerFill: 1000
        fillInterval: 1h
        type: requests
        descriptors:
          - entries:
              - key: user
                value: 'request.headers["x-user-id"]'
```

---

## Prompt Enrichment

Add context to prompts automatically:

### System Prompt Injection

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: prompt-enrichment
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  ai:
    promptEnrichment:
      prepend:
        - role: SYSTEM
          content: |
            You are an assistant for the Home-Ops Kubernetes cluster.
            Current context:
            - Cluster: home-ops-talos
            - Environment: production
            - User: authenticated via JWT
            Always follow security best practices.
```

### RAG Context Injection

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: rag-context
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  ai:
    promptEnrichment:
      prepend:
        - role: SYSTEM
          content: |
            Retrieved context from knowledge base:
            {{.rag_context}}
      # Context from header
      append:
        - role: USER
          content: |
            Additional context: {{request.headers["x-rag-context"]}}
```

---

## Request/Response Transformation

### Header Transformation

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: header-transform
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  transform:
    request:
      headers:
        add:
          - name: X-Request-ID
            value: '{{uuid()}}'
          - name: X-Gateway-Version
            value: "v2.1.2"
        remove:
          - X-Internal-Header
    response:
      headers:
        add:
          - name: X-LLM-Provider
            value: '{{upstream.provider}}'
          - name: X-Token-Count
            value: '{{response.usage.total_tokens}}'
```

### Body Transformation

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: body-transform
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  transform:
    request:
      body:
        # Add default parameters
        jsonPatch:
          - op: add
            path: /temperature
            value: 0.7
          - op: add
            path: /max_tokens
            value: 4096
```

---

## Timeouts and Retries

### Request Timeout

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: llm-timeouts
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  timeout: 60s  # LLM requests can be slow
```

### Retry Policy

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: llm-retries
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  retry:
    numRetries: 3
    perTryTimeout: 30s
    retryOn:
      - 5xx
      - reset
      - connect-failure
      - retriable-4xx
    backoff:
      baseInterval: 1s
      maxInterval: 10s
```

---

## Streaming Responses

Configure for SSE streaming:

### Backend for Streaming

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai-streaming
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

### TrafficPolicy for Streaming

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: streaming-config
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  ai:
    routeType: CHAT_STREAMING
  timeout: 300s  # Longer timeout for streaming
```

---

## Direct Response

Return static responses without backend:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: DirectResponse
metadata:
  name: maintenance-response
  namespace: ai-system
spec:
  status: 503
  body: |
    {
      "error": "Service temporarily unavailable",
      "message": "AgentGateway is under maintenance. Please try again later."
    }
```

Use in HTTPRoute:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: maintenance-route
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      filters:
        - type: ExtensionRef
          extensionRef:
            group: gateway.kgateway.dev
            kind: DirectResponse
            name: maintenance-response
```

---

## CORS Configuration

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: cors-policy
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  cors:
    allowOrigins:
      - "https://app.sklab.dev"
      - "https://chat.sklab.dev"
    allowMethods:
      - GET
      - POST
      - OPTIONS
    allowHeaders:
      - Content-Type
      - Authorization
      - X-Request-ID
    exposeHeaders:
      - X-Token-Count
      - X-LLM-Provider
    maxAge: 86400
    allowCredentials: true
```

---

## References

- [Model Failover](https://kgateway.dev/docs/agentgateway/latest/llm/failover/)
- [Inference Routing](https://kgateway.dev/docs/main/agentgateway/inference/)
- [Rate Limiting](https://agentgateway.dev/docs/configuration/resiliency/rate-limits/)
- [Request Retries](https://kgateway.dev/docs/envoy/main/resiliency/retry/retry/)
- [Gateway API Inference Extension](https://gateway-api-inference-extension.sigs.k8s.io/)

---

*See [10-api-reference.md](./10-api-reference.md) for complete CRD specifications.*
