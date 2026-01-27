# LLM Providers Configuration

> **Complete guide to configuring LLM provider backends including OpenAI, Anthropic, Google Gemini, AWS Bedrock, Azure OpenAI, and Vertex AI.**

## Overview

AgentGateway supports routing to multiple LLM providers through the `Backend` custom resource. Each provider requires:

1. **API Key Secret** - Kubernetes Secret containing credentials
2. **Backend Resource** - Provider configuration
3. **HTTPRoute** - Traffic routing rules

---

## Supported Providers

| Provider | API Type | Models |
|----------|----------|--------|
| OpenAI | REST | GPT-4, GPT-3.5-turbo, GPT-4o |
| Anthropic | REST | Claude 3 Opus, Sonnet, Haiku |
| Google Gemini | REST | Gemini Pro, Gemini Flash |
| AWS Bedrock | AWS SDK | Titan, Claude (via AWS), Llama |
| Azure OpenAI | REST | GPT-4, GPT-3.5-turbo (Azure-hosted) |
| Vertex AI | Google Cloud | Gemini, PaLM |
| OpenAI-Compatible | REST | LiteLLM, Ollama, vLLM |

---

## Authentication Methods

| Method | Security | Use Case |
|--------|----------|----------|
| `SecretRef` | Recommended | Production - references Kubernetes Secret |
| `Inline` | Not Recommended | Testing only - exposes key in YAML |
| `Passthrough` | High | Federated identity - client provides token |

---

## OpenAI

### With External Secrets (Recommended)

```yaml
# externalsecret.yaml
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
```

### Backend Configuration

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

### HTTPRoute

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
  rules:
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
```

### Test Request

```bash
curl "http://ai.sklab.dev/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-4",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is Kubernetes?"}
    ]
  }' | jq
```

---

## Anthropic (Claude)

### External Secret

```yaml
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

### Backend Configuration

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: anthropic
  namespace: ai-system
spec:
  type: AI
  ai:
    llm:
      anthropic:
        authToken:
          kind: SecretRef
          secretRef:
            name: anthropic-secret
        model: "claude-3-opus-20240229"
        apiVersion: "2023-06-01"
```

**Configuration Options:**
- `model`: Claude model ID (optional if specified in request)
- `apiVersion`: Anthropic API version header (required)

### HTTPRoute

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: anthropic
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /anthropic
    backendRefs:
    - name: anthropic
      group: gateway.kgateway.dev
      kind: Backend
```

---

## Google Gemini

### External Secret

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: google-secret
  namespace: ai-system
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: google-secret
    template:
      data:
        Authorization: "{{ .GOOGLE_AI_API_KEY }}"
  dataFrom:
    - extract:
        key: google-ai-credentials
```

### Backend Configuration

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: gemini
  namespace: ai-system
spec:
  type: AI
  ai:
    llm:
      gemini:
        apiVersion: v1beta  # Use v1beta for newer models
        authToken:
          kind: SecretRef
          secretRef:
            name: google-secret
        model: gemini-2.5-flash-lite
```

**Configuration Options:**
- `apiVersion`: `v1` or `v1beta` (use `v1beta` for newer models)
- `model`: Gemini model ID

---

## AWS Bedrock

### External Secret (AWS Credentials)

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: bedrock-secret
  namespace: ai-system
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: bedrock-secret
    template:
      data:
        accessKey: "{{ .AWS_ACCESS_KEY_ID }}"
        secretKey: "{{ .AWS_SECRET_ACCESS_KEY }}"
        sessionToken: "{{ .AWS_SESSION_TOKEN }}"
  dataFrom:
    - extract:
        key: aws-bedrock-credentials
```

### Backend Configuration

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: bedrock
  namespace: ai-system
spec:
  type: AI
  ai:
    llm:
      bedrock:
        model: "anthropic.claude-3-sonnet-20240229-v1:0"
        region: us-east-1
        auth:
          type: Secret
          secretRef:
            name: bedrock-secret
```

### Using IRSA (EKS)

For EKS with IAM Roles for Service Accounts, omit auth:

```yaml
spec:
  type: AI
  ai:
    llm:
      bedrock:
        model: "anthropic.claude-3-sonnet-20240229-v1:0"
        region: us-east-1
        # auth omitted - uses pod's service account
```

### Cross-Region Models

Use `us.` prefix for cross-region inference:

```yaml
model: "us.anthropic.claude-3-5-sonnet-20240620-v1:0"
```

---

## Azure OpenAI

### External Secret

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: azure-openai-secret
  namespace: ai-system
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: azure-openai-secret
    template:
      data:
        Authorization: "{{ .AZURE_OPENAI_API_KEY }}"
  dataFrom:
    - extract:
        key: azure-openai-credentials
```

### Backend Configuration

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: azure-openai
  namespace: ai-system
spec:
  type: AI
  ai:
    llm:
      azureOpenai:
        endpoint: "https://your-resource.openai.azure.com"
        deploymentName: "gpt-4-deployment"
        apiVersion: "2024-02-15-preview"
        authToken:
          kind: SecretRef
          secretRef:
            name: azure-openai-secret
```

---

## Vertex AI

### Backend Configuration

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: vertex-ai
  namespace: ai-system
spec:
  type: AI
  ai:
    llm:
      vertexai:
        projectId: "your-gcp-project-id"
        region: "us-central1"
        model: "gemini-pro"
        authToken:
          kind: SecretRef
          secretRef:
            name: vertex-ai-secret
```

---

## LiteLLM Integration (Recommended)

Route through LiteLLM proxy for unified access to all providers:

### Backend Pointing to LiteLLM

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: litellm
  namespace: ai-system
spec:
  type: AI
  ai:
    llm:
      openai:
        authToken:
          kind: SecretRef
          secretRef:
            name: litellm-secret
        # Model is determined by LiteLLM routing
        model: ""
        # Point to LiteLLM service
        # Note: This requires using host override in policies
```

### Using Static Backend for LiteLLM

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: litellm-proxy
  namespace: ai-system
spec:
  type: Static
  static:
    hosts:
      - host: litellm.ai.svc.cluster.local
        port: 4000
```

### HTTPRoute to LiteLLM

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: litellm
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /v1
    backendRefs:
    - name: litellm-proxy
      group: gateway.kgateway.dev
      kind: Backend
```

---

## Model Failover

Configure automatic failover between providers:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: llm-failover
  namespace: ai-system
spec:
  type: AI
  ai:
    priorityGroups:
    # Priority 1: Primary models (load balanced)
    - providers:
      - name: openai-gpt-4
        openai:
          model: "gpt-4"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
      - name: anthropic-sonnet
        anthropic:
          model: "claude-3-5-sonnet-20241022"
          apiVersion: "2023-06-01"
          authToken:
            kind: SecretRef
            secretRef:
              name: anthropic-secret
    # Priority 2: Fallback (cheaper models)
    - providers:
      - name: openai-gpt-3.5
        openai:
          model: "gpt-3.5-turbo"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
```

**Failover Behavior:**
- Requests load balance within a priority group
- Automatic failover to next priority group on failure
- Up to 32 priority groups supported

---

## Combined HTTPRoute Example

Route multiple providers through single Gateway:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-routes
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
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
  # Bedrock
  - matches:
    - path:
        type: PathPrefix
        value: /bedrock
    backendRefs:
    - name: bedrock
      group: gateway.kgateway.dev
      kind: Backend
  # LiteLLM (unified)
  - matches:
    - path:
        type: PathPrefix
        value: /v1
    backendRefs:
    - name: litellm-proxy
      group: gateway.kgateway.dev
      kind: Backend
```

---

## Troubleshooting

### Authentication Errors

```bash
# Check secret exists
kubectl get secret openai-secret -n ai-system

# Check secret content
kubectl get secret openai-secret -n ai-system -o jsonpath='{.data.Authorization}' | base64 -d

# Check ExternalSecret status
kubectl get externalsecret openai-secret -n ai-system
```

### Backend Not Ready

```bash
# Check Backend status
kubectl get backend openai -n ai-system -o yaml

# Check controller logs
kubectl logs -n ai-system -l app.kubernetes.io/name=agentgateway
```

### Route Not Working

```bash
# Check HTTPRoute status
kubectl get httproute llm-routes -n ai-system -o yaml

# Check attached routes on Gateway
kubectl get gateway agentgateway -n ai-system -o yaml
```

---

## References

- [OpenAI Provider](https://kgateway.dev/docs/agentgateway/latest/llm/providers/openai/)
- [Anthropic Provider](https://kgateway.dev/docs/agentgateway/latest/llm/providers/anthropic/)
- [Gemini Provider](https://kgateway.dev/docs/agentgateway/latest/llm/providers/gemini/)
- [Bedrock Provider](https://kgateway.dev/docs/agentgateway/main/llm/providers/bedrock/)
- [Model Failover](https://kgateway.dev/docs/agentgateway/latest/llm/failover/)

---

*See [05-mcp-connectivity.md](./05-mcp-connectivity.md) for MCP server integration.*
