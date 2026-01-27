# API Reference

> **Complete Custom Resource Definition (CRD) specifications for AgentGateway.**

## Overview

AgentGateway extends the Kubernetes Gateway API with custom resources for AI workloads.

---

## CRD Summary

| CRD | API Group | Description |
|-----|-----------|-------------|
| `Backend` | gateway.kgateway.dev | Backend service configurations (AI, Static, MCP) |
| `TrafficPolicy` | gateway.kgateway.dev | Route-level policies (RBAC, prompt guards) |
| `BackendConfigPolicy` | gateway.kgateway.dev | Connection and protocol settings |
| `HTTPListenerPolicy` | gateway.kgateway.dev | Listener-level behavior |
| `GatewayParameters` | gateway.kgateway.dev | Proxy deployment configuration |
| `GatewayExtension` | gateway.kgateway.dev | External service integrations |
| `DirectResponse` | gateway.kgateway.dev | Static responses |

---

## Backend

Defines backend service configurations for AI providers, static endpoints, and MCP servers.

### API Version

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
```

### Specification

```yaml
spec:
  # Backend type: AI | Static | MCP
  type: <string>

  # AI Backend Configuration
  ai:
    # Single LLM provider
    llm:
      openai: <OpenAIConfig>
      anthropic: <AnthropicConfig>
      gemini: <GeminiConfig>
      bedrock: <BedrockConfig>
      azureOpenai: <AzureOpenAIConfig>
      vertexai: <VertexAIConfig>

    # Multi-provider failover (max 32 groups)
    priorityGroups:
      - providers:
        - name: <string>
          openai: <OpenAIConfig>
          # ... other provider configs

  # Static Backend Configuration
  static:
    hosts:
      - host: <string>
        port: <integer>
    protocol: <string>  # http2, grpc, grpc-web

  # MCP Backend Configuration
  mcp:
    targets:
      - name: <string>
        static:
          host: <string>
          port: <integer>
          protocol: SSE | StreamableHTTP
        selector:
          services:
            matchLabels:
              <key>: <value>
```

### OpenAI Provider Config

```yaml
openai:
  authToken:
    kind: SecretRef | Inline | Passthrough
    secretRef:
      name: <string>
    inline: <string>  # Not recommended
  model: <string>
  # Optional: Override host for OpenAI-compatible APIs
  host: <string>
  port: <integer>
```

### Anthropic Provider Config

```yaml
anthropic:
  authToken:
    kind: SecretRef | Inline | Passthrough
    secretRef:
      name: <string>
  model: <string>
  apiVersion: <string>  # Required, e.g., "2023-06-01"
```

### Gemini Provider Config

```yaml
gemini:
  authToken:
    kind: SecretRef | Inline | Passthrough
    secretRef:
      name: <string>
  model: <string>
  apiVersion: <string>  # "v1" or "v1beta"
```

### Bedrock Provider Config

```yaml
bedrock:
  model: <string>
  region: <string>
  auth:
    type: Secret | IRSA | WebIdentity
    secretRef:
      name: <string>  # Contains accessKey, secretKey
  guardrail:
    id: <string>
    version: <string>
```

### Example: OpenAI Backend

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

### Example: MCP Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: kubernetes-mcp
  namespace: ai-system
spec:
  type: MCP
  mcp:
    targets:
      - name: k8s-tools
        static:
          host: kubernetes-mcp-server.ai-system.svc.cluster.local
          port: 80
          protocol: StreamableHTTP
```

---

## TrafficPolicy

Route-level policy for AI handling, transformations, and security.

### API Version

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
```

### Specification

```yaml
spec:
  # Target routes
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute | Gateway
      name: <string>

  # AI-specific configuration
  ai:
    routeType: CHAT | CHAT_STREAMING

    # Prompt enrichment
    promptEnrichment:
      prepend:
        - role: SYSTEM | USER | ASSISTANT
          content: <string>
      append:
        - role: SYSTEM | USER | ASSISTANT
          content: <string>

    # Prompt guards
    promptGuard:
      request:
        - regex:
            action: Reject | Mask
            matches:
              - pattern: <regex>
                name: <string>
          response:
            message: <string>
      response:
        - regex:
            action: Mask
            builtins:
              - CREDIT_CARD
              - EMAIL
              - PHONE_NUMBER
              - SSN

  # CEL-based RBAC
  rbac:
    policy:
      matchExpressions:
        - <CEL expression>

  # Request transformation
  transform:
    request:
      headers:
        add:
          - name: <string>
            value: <string>
        remove:
          - <string>
      body:
        jsonPatch:
          - op: add | remove | replace
            path: <string>
            value: <any>
    response:
      headers:
        add:
          - name: <string>
            value: <string>

  # Timeout
  timeout: <duration>

  # Retry
  retry:
    numRetries: <integer>
    perTryTimeout: <duration>
    retryOn:
      - 5xx
      - connect-failure
    backoff:
      baseInterval: <duration>
      maxInterval: <duration>

  # Rate limiting
  rateLimit:
    local:
      - maxTokens: <integer>
        tokensPerFill: <integer>
        fillInterval: <duration>
        type: requests | tokens

  # CORS
  cors:
    allowOrigins:
      - <string>
    allowMethods:
      - <string>
    allowHeaders:
      - <string>
    maxAge: <integer>

  # JWT Authentication
  traffic:
    jwtAuthentication:
      mode: Strict | Optional | Permissive
      providers:
        - issuer: <string>
          audiences:
            - <string>
          jwks:
            remote:
              jwksPath: <string>
              backendRef:
                kind: Service
                name: <string>
                port: <integer>

    # External auth
    extAuth:
      backendRef:
        name: <string>
        port: <integer>
      grpc: {}
```

### Example: Prompt Guards

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
        - regex:
            action: Reject
            matches:
              - pattern: "ignore.*previous.*instructions"
                name: "prompt-injection"
          response:
            message: "Request blocked"
      response:
        - regex:
            action: Mask
            builtins:
              - CREDIT_CARD
              - EMAIL
```

---

## BackendConfigPolicy

Connection and protocol settings for backends.

### API Version

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: BackendConfigPolicy
```

### Specification

```yaml
spec:
  targetRefs:
    - group: gateway.kgateway.dev
      kind: Backend
      name: <string>

  connectTimeout: <duration>

  tcpKeepalive:
    keepaliveTime: <duration>
    keepaliveInterval: <duration>
    keepaliveProbes: <integer>

  http2ProtocolOptions:
    maxConcurrentStreams: <integer>
    initialStreamWindowSize: <bytes>
    initialConnectionWindowSize: <bytes>

  tls:
    mode: SIMPLE | MUTUAL
    clientCertificate:
      secretRef:
        name: <string>
    caCertificates:
      secretRef:
        name: <string>

  loadBalancer:
    type: RoundRobin | LeastRequest | Random

  healthCheck:
    interval: <duration>
    timeout: <duration>
    healthyThreshold: <integer>
    unhealthyThreshold: <integer>

  outlierDetection:
    consecutiveErrors: <integer>
    interval: <duration>
    baseEjectionTime: <duration>
    maxEjectionPercent: <integer>
```

---

## HTTPListenerPolicy

HTTP listener-level behavior.

### API Version

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: HTTPListenerPolicy
```

### Specification

```yaml
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: <string>

  accessLog:
    - fileSink:
        path: <string>
        jsonFormat:
          <field>: <value>
      grpcSink:
        logName: <string>
        service:
          backendRef:
            name: <string>

  tracing:
    provider:
      backendRef:
        name: <string>
    samplerType: AlwaysOn | TraceIdRatio
    samplerPercent: <0-100>
```

---

## GatewayParameters

Proxy deployment configuration.

### API Version

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
```

### Specification

```yaml
spec:
  kube:
    agentgateway:
      enabled: <boolean>
      customConfigMapName: <string>

    deployment:
      replicas: <integer>

    podTemplate:
      spec:
        containers:
          - name: <string>
            resources:
              requests:
                cpu: <string>
                memory: <string>
              limits:
                cpu: <string>
                memory: <string>

    service:
      type: ClusterIP | LoadBalancer | NodePort
      annotations:
        <key>: <value>

  # Raw agentgateway config
  rawConfig:
    config:
      tracing:
        otlpEndpoint: <string>
        otlpProtocol: grpc | http
        randomSampling: <boolean>
        fields:
          add:
            <field>: <value>

  # Self-managed proxy
  selfManaged:
    host: <string>
    port: <integer>
```

---

## GatewayExtension

External service integrations.

### API Version

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayExtension
```

### Specification

```yaml
spec:
  type: ExtAuth | ExtProc | RateLimit

  extAuth:
    grpcService:
      backendRef:
        name: <string>
        port: <integer>
      timeout: <duration>
    failureModeAllow: <boolean>

  extProc:
    grpcService:
      backendRef:
        name: <string>
      timeout: <duration>
    processingMode:
      requestHeaderMode: SEND | SKIP
      requestBodyMode: NONE | BUFFERED | STREAMED
      responseHeaderMode: SEND | SKIP
      responseBodyMode: NONE | BUFFERED | STREAMED

  rateLimit:
    grpcService:
      backendRef:
        name: <string>
      timeout: <duration>
    failureModeAllow: <boolean>
```

---

## DirectResponse

Return static responses without backend.

### API Version

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: DirectResponse
```

### Specification

```yaml
spec:
  status: <200-599>
  body: <string>  # Max 4096 characters
```

---

## Status Conditions

All CRDs report status conditions:

```yaml
status:
  conditions:
    - type: Accepted | Programmed | Ready | Rejected
      status: "True" | "False" | "Unknown"
      reason: <string>
      message: <string>
      lastTransitionTime: <timestamp>
      observedGeneration: <integer>
```

---

## Labels and Annotations

### Required Annotations

| Annotation | Target | Description |
|------------|--------|-------------|
| `kgateway.dev/mcp-path` | Service | Override MCP endpoint path |

### Required AppProtocol Values

| AppProtocol | Target | Description |
|-------------|--------|-------------|
| `kgateway.dev/mcp` | Service | Enable MCP protocol |
| `kgateway.dev/a2a` | Service | Enable A2A protocol |

---

## Helm Values Reference

Key Helm values for kgateway chart:

```yaml
# Enable AgentGateway
agentgateway:
  enabled: true

# Controller configuration
controller:
  replicaCount: 2
  logLevel: "info"
  resources:
    requests:
      cpu: "100m"
      memory: "128Mi"
    limits:
      cpu: "500m"
      memory: "512Mi"

# Pod scheduling
affinity: {}
nodeSelector: {}
tolerations: []

# Service account
serviceAccount:
  create: true
  name: ""
```

Full reference: https://kgateway.dev/docs/agentgateway/latest/reference/helm/kgateway/

---

## References

- [API Reference](https://kgateway.dev/docs/agentgateway/latest/reference/api/)
- [Helm Reference](https://kgateway.dev/docs/agentgateway/latest/reference/helm/)
- [Gateway API Specification](https://gateway-api.sigs.k8s.io/reference/spec/)

---

*See [11-cluster-deployment.md](./11-cluster-deployment.md) for Home-Ops specific deployment.*
