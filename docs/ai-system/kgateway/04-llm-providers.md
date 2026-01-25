# LLM Providers Configuration

> **Complete guide to configuring LLM provider backends including OpenAI, Anthropic, Google Gemini, AWS Bedrock, and Azure OpenAI.**

## Overview

Agentgateway supports routing to multiple LLM providers through the `Backend` custom resource. Each provider requires:

1. **API Key Secret** - Kubernetes Secret containing credentials
2. **Backend Resource** - Provider configuration
3. **HTTPRoute** - Traffic routing rules

## Supported Providers

| Provider | API Type | Models |
|----------|----------|--------|
| OpenAI | REST | GPT-4, GPT-3.5-turbo, etc. |
| Anthropic | REST | Claude 3 Opus, Sonnet, Haiku |
| Google Gemini | REST | Gemini Pro, Gemini Flash |
| AWS Bedrock | AWS SDK | Titan, Claude (via AWS), Llama |
| Azure OpenAI | REST | GPT-4, GPT-3.5-turbo (Azure-hosted) |
| Vertex AI | Google Cloud | Gemini, PaLM |
| OpenAI-Compatible | REST | Any provider with OpenAI-compatible API |

---

## OpenAI

### Step 1: Create API Key Secret

```bash
export OPENAI_API_KEY="sk-your-api-key-here"

kubectl apply -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: openai-secret
  namespace: kgateway-system
type: Opaque
stringData:
  Authorization: $OPENAI_API_KEY
EOF
```

### Step 2: Create Backend

```yaml
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

### Step 3: Create HTTPRoute

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: kgateway-system
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
      namespace: kgateway-system
      group: gateway.kgateway.dev
      kind: Backend
```

### Test Request

```bash
curl "localhost:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is Kubernetes?"}
    ]
  }' | jq
```

---

## Anthropic (Claude)

### Step 1: Create API Key Secret

```bash
export ANTHROPIC_API_KEY="sk-ant-your-api-key-here"

kubectl apply -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: anthropic-secret
  namespace: kgateway-system
type: Opaque
stringData:
  Authorization: $ANTHROPIC_API_KEY
EOF
```

### Step 2: Create Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: anthropic
  namespace: kgateway-system
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

### Step 3: Create HTTPRoute

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: anthropic
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: kgateway-system
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /anthropic
    backendRefs:
    - name: anthropic
      namespace: kgateway-system
      group: gateway.kgateway.dev
      kind: Backend
```

### Test Request

```bash
curl "localhost:8080/anthropic" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-3-opus-20240229",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Explain how AI works in simple terms."}
    ]
  }' | jq
```

---

## Google Gemini

### Step 1: Create API Key Secret

```bash
export GOOGLE_KEY="your-google-ai-studio-api-key"

kubectl apply -f- <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: google-secret
  namespace: kgateway-system
type: Opaque
stringData:
  Authorization: $GOOGLE_KEY
EOF
```

### Step 2: Create Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: google
  namespace: kgateway-system
  labels:
    app: agentgateway
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
```

**Configuration Options:**
- `apiVersion`: API version (`v1beta` for newer models)
- `model`: Gemini model ID

### Step 3: Create HTTPRoute

```yaml
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
```

### Test Request

```bash
curl "localhost:8080/gemini" \
  -H "content-type: application/json" \
  -d '{
    "model": "",
    "messages": [
      {"role": "user", "content": "Explain how AI works in simple terms."}
    ]
  }' | jq
```

---

## AWS Bedrock

### Step 1: Create Credentials Secret

**Using AWS Access Keys:**

```bash
export AWS_ACCESS_KEY_ID="your-access-key-id"
export AWS_SECRET_ACCESS_KEY="your-secret-access-key"
export AWS_SESSION_TOKEN="your-session-token"  # Optional

kubectl create secret generic bedrock-secret \
  -n kgateway-system \
  --from-literal=accessKey="$AWS_ACCESS_KEY_ID" \
  --from-literal=secretKey="$AWS_SECRET_ACCESS_KEY" \
  --from-literal=sessionToken="$AWS_SESSION_TOKEN"
```

**Using API Key (alternative):**

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: bedrock-secret
  namespace: kgateway-system
type: Opaque
stringData:
  Authorization: $BEDROCK_API_KEY
```

### Step 2: Create Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: bedrock
  namespace: kgateway-system
spec:
  type: AI
  ai:
    llm:
      bedrock:
        model: "amazon.titan-text-lite-v1"
        region: us-east-1
        auth:
          type: Secret
          secretRef:
            name: bedrock-secret
```

**Configuration Options:**
- `model`: Bedrock model ID (supports cross-region models with `us.` prefix)
- `region`: AWS region
- `auth`: Authentication configuration (omit for IRSA)

### Step 3: Create HTTPRoute

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: bedrock
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /bedrock
    backendRefs:
    - name: bedrock
      group: gateway.kgateway.dev
      kind: Backend
```

### Using IAM Roles for Service Accounts (IRSA)

For EKS clusters with IRSA configured, omit the `auth` section:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: bedrock
  namespace: kgateway-system
spec:
  type: AI
  ai:
    llm:
      bedrock:
        model: "amazon.titan-text-lite-v1"
        region: us-east-1
        # auth is omitted - uses pod's service account role
```

---

## API Key Management

### Authentication Methods

Agentgateway supports three authentication methods:

| Method | Security | Use Case |
|--------|----------|----------|
| Inline Token | Low | Testing only |
| SecretRef | Medium | Development, staging, controlled production |
| Passthrough | High | Federated identity, OIDC tokens |

### Inline Token (Not Recommended)

```yaml
spec:
  ai:
    llm:
      openai:
        authToken:
          kind: Inline
          inline: "sk-your-api-key"  # Exposed in YAML!
        model: "gpt-3.5-turbo"
```

### SecretRef (Recommended)

```yaml
spec:
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

Pass client-provided tokens directly to the LLM provider:

```yaml
spec:
  ai:
    llm:
      openai:
        authToken:
          kind: Passthrough
        model: "gpt-3.5-turbo"
```

Client includes token in request:
```bash
curl "localhost:8080/openai" \
  -H "Authorization: Bearer sk-client-token" \
  -H "content-type: application/json" \
  -d '{"model": "gpt-3.5-turbo", "messages": [...]}'
```

---

## Multiple Providers Example

Deploy all providers simultaneously:

```yaml
---
# OpenAI Backend
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
        model: "gpt-4"
---
# Anthropic Backend
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: anthropic
  namespace: kgateway-system
spec:
  type: AI
  ai:
    llm:
      anthropic:
        authToken:
          kind: SecretRef
          secretRef:
            name: anthropic-secret
        model: "claude-3-sonnet-20240229"
        apiVersion: "2023-06-01"
---
# Gemini Backend
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: gemini
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
        model: gemini-pro
---
# Combined HTTPRoute
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llm-routes
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
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
  - matches:
    - path:
        type: PathPrefix
        value: /anthropic
    backendRefs:
    - name: anthropic
      group: gateway.kgateway.dev
      kind: Backend
  - matches:
    - path:
        type: PathPrefix
        value: /gemini
    backendRefs:
    - name: gemini
      group: gateway.kgateway.dev
      kind: Backend
```

---

## Cleanup

Remove LLM provider resources:

```bash
kubectl delete httproute openai anthropic google bedrock -n kgateway-system
kubectl delete backend openai anthropic google bedrock -n kgateway-system
kubectl delete secret openai-secret anthropic-secret google-secret bedrock-secret -n kgateway-system
```

---

*See [07-security.md](./07-security.md) for adding RBAC and prompt guards to your LLM routes.*
