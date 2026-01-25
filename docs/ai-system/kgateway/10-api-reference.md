# API Reference

> **Complete reference for Kgateway Agentgateway Custom Resource Definitions and API specifications.**

## Overview

Agentgateway uses the `gateway.kgateway.dev/v1alpha1` API group. This reference covers all resource types, configurations, and specifications.

---

## Core Resources

### Backend

Defines upstream service backends for AI providers, static services, MCP, and more.

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: <name>
  namespace: <namespace>
spec:
  type: <AI|Static|AWS|MCP|DynamicForwardProxy>
  # Type-specific configuration follows
```

#### Backend Types

| Type | Description |
|------|-------------|
| `AI` | LLM provider backends |
| `Static` | Direct upstream services |
| `AWS` | AWS services (Lambda, etc.) |
| `MCP` | Model Context Protocol servers |
| `DynamicForwardProxy` | Runtime destination resolution |

#### AI Backend Spec

```yaml
spec:
  type: AI
  ai:
    # Single provider
    llm:
      openai: <OpenAIConfig>
      anthropic: <AnthropicConfig>
      gemini: <GeminiConfig>
      bedrock: <BedrockConfig>
      azureOpenai: <AzureOpenAIConfig>
      vertexAi: <VertexAIConfig>

    # OR priority groups for failover
    priorityGroups:
    - providers:
      - name: <string>
        openai: <OpenAIConfig>
        anthropic: <AnthropicConfig>
        # etc.
```

---

### TrafficPolicy

Route-level policies for routing, transformation, AI features, and security.

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: <name>
  namespace: <namespace>
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: <route-name>

  # RBAC
  rbac:
    policy:
      matchExpressions:
      - "<CEL expression>"

  # AI features
  ai:
    promptGuard: <PromptGuardConfig>
    promptEnrichment: <PromptEnrichmentConfig>
    fieldDefaults: <FieldDefaultsConfig>

  # Rate limiting
  rateLimit:
    rateLimits:
    - actions: [...]
```

---

### HTTPListenerPolicy

HTTP listener configuration for access logs, tracing, and protocol upgrades.

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: HTTPListenerPolicy
metadata:
  name: <name>
  namespace: <namespace>
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: <gateway-name>

  accessLog:
  - fileSink:
      path: <string>
      jsonFormat: <object>
  - grpcService:
      logName: <string>
      staticClusterName: <string>

  tracing:
    provider:
      otel:
        grpcAddress: <string>
    samplingRate: <number>
    propagators: [W3C_TRACE_CONTEXT, B3, ...]
```

---

### GatewayParameters

Proxy deployment configuration for Kubernetes or self-managed deployments.

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: <name>
  namespace: <namespace>
spec:
  kube:
    agentgateway:
      customConfigMapName: <string>

    deployment:
      replicas: <number>
      container:
        resources:
          requests:
            cpu: <string>
            memory: <string>
          limits:
            cpu: <string>
            memory: <string>
        securityContext:
          runAsNonRoot: <boolean>
          runAsUser: <number>

    podTemplate:
      spec:
        affinity: <AffinitySpec>
        nodeSelector: <object>
        tolerations: [...]
```

---

### DirectResponse

Return direct HTTP responses without backend routing.

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: DirectResponse
metadata:
  name: <name>
  namespace: <namespace>
spec:
  status: <number>  # HTTP status code
  body: <string>    # Response body
```

---

### GatewayExtension

External services for authentication, processing, and rate limiting.

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayExtension
metadata:
  name: <name>
  namespace: <namespace>
spec:
  type: <ExtAuth|ExtProc|RateLimit>
  # Type-specific configuration
```

---

## LLM Provider Configurations

### OpenAI

```yaml
openai:
  authToken:
    kind: <Inline|SecretRef|Passthrough>
    inline: <string>  # If kind: Inline
    secretRef:        # If kind: SecretRef
      name: <secret-name>
  model: <string>  # e.g., "gpt-4", "gpt-3.5-turbo"
```

### Anthropic

```yaml
anthropic:
  authToken:
    kind: <Inline|SecretRef|Passthrough>
    secretRef:
      name: <secret-name>
  model: <string>      # e.g., "claude-3-opus-20240229"
  apiVersion: <string> # e.g., "2023-06-01"
```

### Gemini

```yaml
gemini:
  authToken:
    kind: <Inline|SecretRef|Passthrough>
    secretRef:
      name: <secret-name>
  model: <string>      # e.g., "gemini-pro", "gemini-2.5-flash-lite"
  apiVersion: <string> # e.g., "v1beta"
```

### AWS Bedrock

```yaml
bedrock:
  model: <string>   # e.g., "amazon.titan-text-lite-v1"
  region: <string>  # e.g., "us-east-1"
  auth:
    type: Secret
    secretRef:
      name: <secret-name>
  # OR omit auth for IRSA
```

### Azure OpenAI

```yaml
azureOpenai:
  authToken:
    kind: <Inline|SecretRef|Passthrough>
    secretRef:
      name: <secret-name>
  endpoint: <string>      # Azure endpoint URL
  deploymentName: <string> # Deployment name
  apiVersion: <string>    # API version
```

### Vertex AI

```yaml
vertexAi:
  authToken:
    kind: <Inline|SecretRef|Passthrough>
    secretRef:
      name: <secret-name>
  project: <string>   # GCP project ID
  location: <string>  # GCP region
  model: <string>     # Model name
```

---

## AI Feature Configurations

### Prompt Guard

```yaml
promptGuard:
  request:
    customResponse:
      message: <string>  # Custom rejection message
    regex:
      action: <REJECT|MASK>
      matches:
      - pattern: <regex>
        name: <string>
      builtins:
      - CreditCard
      - Email
      - PhoneNumber
      - Ssn
      - CaSin

  response:
    regex:
      action: MASK
      matches:
      - pattern: <regex>
        name: <string>
      builtins:
      - CREDIT_CARD
      - PHONE_NUMBER
      - EMAIL
      - SSN
```

### Prompt Enrichment

```yaml
promptEnrichment:
  prepend:
  - role: <system|user|assistant>
    content: <string>
  append:
  - role: <system|user|assistant>
    content: <string>
```

### Field Defaults

```yaml
fieldDefaults:
- path: <string>       # JSON path, e.g., "model"
  value: <string>      # Default value
  override: <boolean>  # Override client value
```

---

## RBAC Configuration

### CEL Expressions

```yaml
rbac:
  policy:
    matchExpressions:
    # Header matching
    - "request.headers['x-api-key'] == 'value'"
    - "has(request.headers['authorization'])"
    - "request.headers['x-tenant'].contains('acme')"

    # Source matching
    - "source.address == '10.0.0.0/8'"

    # Path matching
    - "request.path.startsWith('/api/')"

    # Method matching
    - "request.method == 'POST'"
```

---

## MCP Configuration

### Backend Spec

```yaml
spec:
  type: MCP
  mcp:
    targets:
    # Kubernetes service
    - name: <string>
      service:
        name: <service-name>
        namespace: <namespace>
        port: <number>

    # Static host
    - name: <string>
      static:
        host: <hostname>
        port: <number>
        path: <string>
```

---

## Observability Configuration

### Access Logging

```yaml
accessLog:
# File sink
- fileSink:
    path: /dev/stdout
    jsonFormat:
      timestamp: "%START_TIME%"
      request_id: "%REQ(X-REQUEST-ID)%"
      method: "%REQ(:METHOD)%"
      path: "%REQ(:PATH)%"
      response_code: "%RESPONSE_CODE%"
      duration: "%DURATION%"

# gRPC service
- grpcService:
    logName: <string>
    staticClusterName: <string>

# OpenTelemetry
- otel:
    endpoint: <string>
    protocol: <grpc|http/protobuf|http/json>
```

### Tracing

```yaml
tracing:
  provider:
    otel:
      grpcAddress: <host:port>
      # OR
      httpAddress: <host:port>
      protocol: <grpc|http/protobuf|http/json>

  samplingRate: <0-100>  # Percentage

  propagators:
  - W3C_TRACE_CONTEXT
  - B3
  - B3_SINGLE_HEADER

  customTags:
    <tag-name>:
      literal: <string>
      environment: <env-var>
      requestHeader: <header-name>
```

---

## Load Balancing

### Strategies

```yaml
loadBalancer:
  type: <RoundRobin|LeastRequest|RingHash|Maglev|Random>

  # For RingHash/Maglev
  hashPolicy:
  - header:
      headerName: <string>
  - cookie:
      name: <string>
      ttl: <duration>
  - connectionProperties:
      sourceIp: true
```

---

## Health Checking

### Outlier Detection

```yaml
outlierDetection:
  consecutive5xx: <number>
  interval: <duration>
  baseEjectionTime: <duration>
  maxEjectionPercent: <number>
  enforcingConsecutive5xx: <number>
  enforcingSuccessRate: <number>
  successRateMinimumHosts: <number>
  successRateRequestVolume: <number>
  successRateStdevFactor: <number>
```

### TCP Keepalive

```yaml
tcpKeepalive:
  probes: <number>
  time: <duration>
  interval: <duration>
```

---

## Helm Values Reference

### Key Settings

| Value | Type | Default | Description |
|-------|------|---------|-------------|
| `agentgateway.enabled` | bool | `false` | Enable agentgateway data plane |
| `controller.replicas` | int | `1` | Controller replicas |
| `controller.image.repository` | string | `cr.kgateway.dev/kgateway-dev/kgateway` | Controller image |
| `controller.image.tag` | string | Chart version | Image tag |
| `controller.image.pullPolicy` | string | `IfNotPresent` | Image pull policy |
| `controller.resources.requests.cpu` | string | `100m` | CPU request |
| `controller.resources.requests.memory` | string | `128Mi` | Memory request |
| `controller.resources.limits.cpu` | string | `500m` | CPU limit |
| `controller.resources.limits.memory` | string | `512Mi` | Memory limit |
| `serviceAccount.create` | bool | `true` | Create service account |
| `rbac.create` | bool | `true` | Create RBAC resources |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `KGW_ENABLE_GATEWAY_API_EXPERIMENTAL_FEATURES` | Enable experimental Gateway API features |

---

## Status Conditions

### Gateway Status

| Condition | Description |
|-----------|-------------|
| `Accepted` | Gateway accepted by controller |
| `Programmed` | Configuration applied to data plane |
| `Ready` | Gateway ready to accept traffic |

### HTTPRoute Status

| Condition | Description |
|-----------|-------------|
| `Accepted` | Route accepted by gateway |
| `ResolvedRefs` | All backend references resolved |

---

## Common Patterns

### Multi-Provider with Failover and Guards

```yaml
# Backend with failover
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: llm-backend
spec:
  type: AI
  ai:
    priorityGroups:
    - providers:
      - name: primary
        openai:
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
          model: "gpt-4"
    - providers:
      - name: fallback
        anthropic:
          authToken:
            kind: SecretRef
            secretRef:
              name: anthropic-secret
          model: "claude-3-sonnet"
          apiVersion: "2023-06-01"
---
# Route
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-route
spec:
  parentRefs:
  - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /llm
    backendRefs:
    - name: llm-backend
      group: gateway.kgateway.dev
      kind: Backend
---
# Security policy
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: llm-security
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: llm-route
  rbac:
    policy:
      matchExpressions:
      - "has(request.headers['x-api-key'])"
  ai:
    promptGuard:
      request:
        regex:
          action: REJECT
          builtins: [CreditCard, Ssn]
      response:
        regex:
          action: MASK
          builtins: [CREDIT_CARD, EMAIL]
```

---

## External Links

- **GitHub**: [github.com/kgateway-dev/kgateway](https://github.com/kgateway-dev/kgateway)
- **Documentation**: [kgateway.dev/docs/agentgateway](https://kgateway.dev/docs/agentgateway/latest/)
- **Gateway API**: [gateway-api.sigs.k8s.io](https://gateway-api.sigs.k8s.io/)
- **CNCF**: [Sandbox Project](https://www.cncf.io/projects/)

---

*Last updated: 2026-01-24 | Kgateway Agentgateway v2.1.2*
