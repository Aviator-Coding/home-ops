# LLM Providers

Complete configuration guide for all supported AI model providers.

## Overview

Kagent supports multiple LLM providers:

| Provider | Models | Best For |
|----------|--------|----------|
| **OpenAI** | GPT-4, GPT-4o, GPT-3.5 | General purpose, best tool calling |
| **Anthropic** | Claude 3 family | Reasoning, long context |
| **Azure OpenAI** | GPT models via Azure | Enterprise, compliance |
| **Google Gemini** | Gemini 2.5 family | Cost-effective, fast |
| **Amazon Bedrock** | Multiple models | AWS integration |
| **Ollama** | Llama, Mistral, etc. | Local/private deployment |
| **BYO Compatible** | Any OpenAI-compatible | Custom endpoints |

---

## OpenAI

### Step 1: Create Secret

```bash
export OPENAI_API_KEY="sk-..."
kubectl create secret generic kagent-openai -n kagent \
  --from-literal OPENAI_API_KEY=$OPENAI_API_KEY
```

### Step 2: Create ModelConfig

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: openai-config
  namespace: kagent
spec:
  apiKeySecret: kagent-openai
  apiKeySecretKey: OPENAI_API_KEY
  model: gpt-4o-mini
  provider: OpenAI
  openAI: {}
```

### Available Models

| Model | Use Case | Context |
|-------|----------|---------|
| `gpt-4o` | Best performance | 128K |
| `gpt-4o-mini` | Cost-effective | 128K |
| `gpt-4-turbo` | Previous gen | 128K |
| `gpt-3.5-turbo` | Fastest, cheapest | 16K |

### Apply Configuration

```bash
kubectl apply -f openai-config.yaml
```

---

## Anthropic

### Step 1: Create Secret

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
kubectl create secret generic kagent-anthropic -n kagent \
  --from-literal ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
```

### Step 2: Create ModelConfig

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: anthropic-config
  namespace: kagent
spec:
  apiKeySecret: kagent-anthropic
  apiKeySecretKey: ANTHROPIC_API_KEY
  model: claude-3-sonnet-20240229
  provider: Anthropic
  anthropic: {}
```

### Available Models

| Model | Use Case | Context |
|-------|----------|---------|
| `claude-3-opus-20240229` | Most capable | 200K |
| `claude-3-sonnet-20240229` | Balanced | 200K |
| `claude-3-haiku-20240307` | Fastest | 200K |
| `claude-3-5-sonnet-20241022` | Latest | 200K |

### Features

- Automatic vision support for Claude-3 models
- Kagent auto-configures appropriate capabilities

---

## Azure OpenAI

### Step 1: Create Secret

```bash
export AZURE_OPENAI_API_KEY="..."
kubectl create secret generic kagent-azureopenai -n kagent \
  --from-literal AZURE_OPENAI_API_KEY=$AZURE_OPENAI_API_KEY
```

### Step 2: Create ModelConfig

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: azure-config
  namespace: kagent
spec:
  apiKeySecret: kagent-azureopenai
  apiKeySecretKey: AZURE_OPENAI_API_KEY
  model: gpt-4o-mini
  provider: AzureOpenAI
  azureOpenAI:
    azureEndpoint: "https://your-resource.openai.azure.com/"
    apiVersion: "2025-03-01-preview"
    azureDeployment: "gpt-4o-mini"
    # Optional: Azure AD token
    # azureAdToken: "<token>"
```

### Configuration Fields

| Field | Required | Description |
|-------|----------|-------------|
| `azureEndpoint` | Yes | Azure OpenAI resource endpoint |
| `apiVersion` | Yes | Azure API version |
| `azureDeployment` | Yes | Deployment name in Azure |
| `azureAdToken` | No | Azure AD authentication token |

### Finding Your Endpoint

1. Go to Azure Portal
2. Navigate to your Azure OpenAI resource
3. Find endpoint under **Keys and Endpoint**

---

## Google Gemini

### Step 1: Get API Key

Obtain from [Google AI Studio](https://ai.google.dev/)

### Step 2: Create Secret

```bash
export GOOGLE_API_KEY="..."
kubectl create secret generic kagent-gemini -n kagent \
  --from-literal GOOGLE_API_KEY=$GOOGLE_API_KEY
```

### Step 3: Create ModelConfig

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: gemini-config
  namespace: kagent
spec:
  apiKeySecret: kagent-gemini
  apiKeySecretKey: GOOGLE_API_KEY
  model: gemini-2.5-flash
  provider: Gemini
  gemini: {}
```

### Available Models

| Model | Use Case |
|-------|----------|
| `gemini-2.5-pro` | Most capable |
| `gemini-2.5-flash` | Fast, cost-effective |
| `gemini-1.5-pro` | Previous generation |

---

## Amazon Bedrock

### Step 1: Get AWS Credentials

Follow [AWS Bedrock API keys guide](https://docs.aws.amazon.com/bedrock/latest/userguide/getting-started-api-keys.html)

### Step 2: Create Secret

```bash
export AWS_API_KEY="..."
kubectl create secret generic kagent-bedrock -n kagent \
  --from-literal AWS_API_KEY=$AWS_API_KEY
```

### Step 3: Create ModelConfig

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: bedrock-config
  namespace: kagent
spec:
  apiKeySecret: kagent-bedrock
  apiKeySecretKey: AWS_API_KEY
  model: amazon.titan-text-express-v1
  provider: OpenAI
  openAI:
    baseUrl: "https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1"
```

### Configuration Notes

- Uses OpenAI-compatible endpoint
- Set `baseUrl` to Bedrock runtime URL
- Region in URL must match your model access

### Available Regions

| Region | URL Pattern |
|--------|-------------|
| us-west-2 | `https://bedrock-runtime.us-west-2.amazonaws.com/openai/v1` |
| us-east-1 | `https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1` |

### Model Access

Some models (like Anthropic on Bedrock) require additional access controls. See [AWS Bedrock model access docs](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html).

---

## Ollama (Local Models)

### Overview

Run LLMs locally without external API dependencies.

> **Important**: Use models that support function calling, as kagent relies on tool invocation.

### Step 1: Deploy Ollama to Kubernetes

```yaml
---
apiVersion: v1
kind: Namespace
metadata:
  name: ollama
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ollama
  namespace: ollama
spec:
  replicas: 1
  selector:
    matchLabels:
      name: ollama
  template:
    metadata:
      labels:
        name: ollama
    spec:
      containers:
        - name: ollama
          image: ollama/ollama:latest
          ports:
            - containerPort: 11434
              protocol: TCP
          resources:
            requests:
              memory: "4Gi"
              cpu: "2"
            limits:
              memory: "8Gi"
              cpu: "4"
---
apiVersion: v1
kind: Service
metadata:
  name: ollama
  namespace: ollama
spec:
  type: ClusterIP
  selector:
    name: ollama
  ports:
    - port: 80
      targetPort: 11434
      protocol: TCP
```

### Step 2: Pull Model

```bash
# Port-forward to Ollama
kubectl port-forward -n ollama svc/ollama 11434:80

# Pull a model (in another terminal)
curl http://localhost:11434/api/pull -d '{"name": "llama3"}'
```

### Step 3: Create ModelConfig

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: ollama-config
  namespace: kagent
spec:
  # Note: Ollama doesn't require API key, but field may be required
  apiKeySecret: kagent-openai
  apiKeySecretKey: OPENAI_API_KEY
  model: llama3
  provider: Ollama
  ollama:
    host: http://ollama.ollama.svc.cluster.local
```

### Recommended Models

| Model | Size | Function Calling |
|-------|------|------------------|
| `llama3` | 8B | Yes |
| `llama3:70b` | 70B | Yes |
| `mistral` | 7B | Limited |
| `mixtral` | 47B | Yes |

### Verify Deployment

```bash
kubectl get pod -n ollama
```

---

## BYO OpenAI-Compatible

Use any OpenAI-compatible endpoint (vLLM, LiteLLM, etc.).

### ModelConfig

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: custom-config
  namespace: kagent
spec:
  apiKeySecret: kagent-custom
  apiKeySecretKey: API_KEY
  model: your-model-name
  provider: OpenAI
  openAI:
    baseUrl: "https://your-endpoint.com/v1"
```

### Common Use Cases

| Platform | Base URL |
|----------|----------|
| vLLM | `http://vllm-service:8000/v1` |
| LiteLLM | `http://litellm:4000/v1` |
| LocalAI | `http://localai:8080/v1` |
| Together AI | `https://api.together.xyz/v1` |

---

## Using Multiple Providers

You can configure multiple ModelConfigs and reference different ones per agent.

### Create Multiple Configs

```yaml
---
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: openai-fast
  namespace: kagent
spec:
  apiKeySecret: kagent-openai
  apiKeySecretKey: OPENAI_API_KEY
  model: gpt-3.5-turbo
  provider: OpenAI
  openAI: {}
---
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: openai-smart
  namespace: kagent
spec:
  apiKeySecret: kagent-openai
  apiKeySecretKey: OPENAI_API_KEY
  model: gpt-4o
  provider: OpenAI
  openAI: {}
---
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: anthropic-reasoning
  namespace: kagent
spec:
  apiKeySecret: kagent-anthropic
  apiKeySecretKey: ANTHROPIC_API_KEY
  model: claude-3-opus-20240229
  provider: Anthropic
  anthropic: {}
```

### Reference in Agents

```yaml
# Fast agent for simple queries
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: quick-helper
spec:
  declarative:
    modelConfig: openai-fast  # Uses GPT-3.5
    # ...

---
# Smart agent for complex reasoning
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: deep-analyzer
spec:
  declarative:
    modelConfig: anthropic-reasoning  # Uses Claude Opus
    # ...
```

---

## Provider Comparison

| Provider | Latency | Cost | Tool Calling | Context |
|----------|---------|------|--------------|---------|
| OpenAI GPT-4o | Medium | $$$ | Excellent | 128K |
| OpenAI GPT-3.5 | Fast | $ | Good | 16K |
| Anthropic Claude 3 | Medium | $$$ | Good | 200K |
| Gemini Flash | Fast | $ | Good | 1M |
| Ollama | Varies | Free | Depends | Varies |

---

## Troubleshooting

### Check ModelConfig Status

```bash
kubectl get modelconfigs -n kagent
kubectl describe modelconfig openai-config -n kagent
```

### Verify Secret

```bash
kubectl get secrets -n kagent
kubectl describe secret kagent-openai -n kagent
```

### Common Issues

**"Model not found" error:**
- Verify model name spelling
- Check model availability in your region
- Ensure API key has access to the model

**"Authentication failed" error:**
- Verify secret exists and contains correct key
- Check secret key name matches `apiKeySecretKey`
- Ensure API key is valid and not expired

**"Rate limited" error:**
- Check provider rate limits
- Consider using a different model
- Implement retry logic in applications

---

## Next Steps

- [A2A Communication](./06-a2a-communication.md) - Inter-agent protocols
- [Integrations](./07-integrations.md) - Slack, Discord integration
- [CLI Reference](./08-cli-reference.md) - Command-line usage
