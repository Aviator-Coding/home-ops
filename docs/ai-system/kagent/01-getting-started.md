# Getting Started with Kagent

This guide covers installation, prerequisites, and your first steps with kagent.

## Prerequisites

### Required Tools

| Tool | Purpose | Installation |
|------|---------|--------------|
| **kubectl** | Kubernetes CLI | [kubernetes.io/docs/tasks/tools](https://kubernetes.io/docs/tasks/tools/) |
| **Helm** | Package manager | [helm.sh/docs/intro/install](https://helm.sh/docs/intro/install/) |
| **kind** (optional) | Local K8s cluster | [kind.sigs.k8s.io](https://kind.sigs.k8s.io/) |

### LLM Provider API Key

You'll need an API key from one of the supported providers:
- OpenAI: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- Anthropic: [console.anthropic.com](https://console.anthropic.com/)
- Google AI Studio: [ai.google.dev](https://ai.google.dev/)
- Azure OpenAI, Amazon Bedrock, or Ollama (local)

---

## Installation Methods

### Method 1: Kagent CLI (Recommended)

The CLI provides the simplest installation experience.

#### Step 1: Set Environment Variable

```bash
export OPENAI_API_KEY="your-api-key-here"
```

#### Step 2: Install CLI

**Via Homebrew:**
```bash
brew install kagent
```

**Via curl:**
```bash
curl https://raw.githubusercontent.com/kagent-dev/kagent/refs/heads/main/scripts/get-kagent | bash
```

#### Step 3: Deploy to Cluster

```bash
kagent install --profile demo
```

Expected output:
```
kagent installed successfully
```

**Profile Options:**
| Profile | Description |
|---------|-------------|
| `demo` | Full installation with pre-loaded agents and tools |
| `minimal` | Base installation without pre-loaded agents |

#### Step 4: Access Dashboard

```bash
kagent dashboard
```

Opens at: `http://localhost:8082`

---

### Method 2: Helm Charts

For more control over installation, use Helm directly.

#### Step 1: Install CRDs

```bash
helm install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds \
  --namespace kagent \
  --create-namespace
```

#### Step 2: Configure Provider

Set your provider's API key as an environment variable:

```bash
# OpenAI
export OPENAI_API_KEY="sk-..."

# Anthropic
export ANTHROPIC_API_KEY="sk-ant-..."

# Azure OpenAI
export AZURE_OPENAI_API_KEY="..."

# Gemini
export GEMINI_API_KEY="..."
```

#### Step 3: Install kagent

**OpenAI:**
```bash
helm install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --namespace kagent \
  --set providers.default=openAI \
  --set providers.openAI.apiKey=$OPENAI_API_KEY
```

**Anthropic:**
```bash
helm install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --namespace kagent \
  --set providers.default=anthropic \
  --set providers.anthropic.apiKey=$ANTHROPIC_API_KEY
```

**Azure OpenAI:**
```bash
helm install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --namespace kagent \
  --set providers.default=azureOpenAI \
  --set providers.azureOpenAI.apiKey=$AZURE_OPENAI_API_KEY
```

**Gemini:**
```bash
helm install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --namespace kagent \
  --set providers.default=gemini \
  --set providers.gemini.apiKey=$GEMINI_API_KEY
```

**Ollama (local models):**
```bash
helm install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --namespace kagent \
  --set providers.default=ollama
```

#### Step 4: Access UI

```bash
kubectl port-forward -n kagent svc/kagent-ui 8080:8080
```

Open: `http://localhost:8080`

---

## Quickstart Walkthrough

After installation, follow these steps to run your first agent.

### 1. Launch Dashboard

```bash
kagent dashboard
```

### 2. Complete Setup Wizard

The welcome wizard walks you through:

1. **Choose AI Model**: Select `gpt-4.1-mini` or your preferred model
2. **Review Agent Config**: Default Kubernetes agent configuration
3. **Review Tools**: Pre-selected Kubernetes tools
4. **Confirm**: Click "Create kagent/my-first-k8s-agent & Finish"

### 3. Test Your Agent

1. Locate `kagent/my-first-k8s-agent` on the landing page
2. Enter a test message:
   ```
   What API resources are running in my cluster?
   ```
3. View the **Arguments** and **Results** tabs to see tool execution

### 4. CLI Interaction

You can also interact via CLI:

```bash
# List available agents
kagent get agent

# Invoke an agent
kagent invoke -t "What Helm charts are in my cluster?" --agent helm-agent

# Stream response
kagent invoke -t "Get all pods in kagent namespace" --agent k8s-agent --stream
```

---

## Advanced Helm Configuration

### Controller Environment Variables

Configure via values file:

```yaml
# values.yaml
controller:
  env:
    - name: KAGENT_CONTROLLER_NAME
      value: my-kagent
    - name: LOG_LEVEL
      value: debug
```

Apply:
```bash
helm install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent \
  --namespace kagent \
  -f values.yaml
```

### Secrets Integration

Load environment variables from existing secrets:

```yaml
# values.yaml
controller:
  envFrom:
    - secretRef:
        name: controller-secrets
```

### kmcp Configuration

As of v0.7, kmcp is included by default. To use a separate kmcp installation:

```yaml
# values.yaml
kmcp:
  enabled: false
```

Or via command line:
```bash
helm install kagent ... --set kmcp.enabled=false
```

---

## Uninstallation

### Via CLI

```bash
kagent uninstall
```

### Via Helm

```bash
# Remove main installation
helm uninstall kagent -n kagent

# Remove CRDs
helm uninstall kagent-crds -n kagent

# Remove namespace (optional)
kubectl delete namespace kagent
```

---

## Verification

After installation, verify components are running:

```bash
# Check pods
kubectl get pods -n kagent

# Check CRDs
kubectl get crds | grep kagent

# Check agents
kagent get agent

# Check tools
kagent get tool
```

Expected CRDs:
- `agents.kagent.dev`
- `modelconfigs.kagent.dev`
- `mcpservers.kagent.dev`
- `remotemcpservers.kagent.dev`

---

## Troubleshooting

### Common Issues

**Dashboard not loading:**
```bash
# Check service
kubectl get svc -n kagent

# Direct port-forward
kubectl port-forward -n kagent svc/kagent 8001:80
```

**API key errors:**
```bash
# Verify secret exists
kubectl get secrets -n kagent

# Check secret content
kubectl describe secret kagent-openai -n kagent
```

**Pod crash loops:**
```bash
# Check pod logs
kubectl logs -n kagent deploy/kagent-controller

# Describe pod
kubectl describe pod -n kagent -l app=kagent-controller
```

---

## Next Steps

- [Core Concepts](./02-core-concepts.md) - Understand agents, tools, and architecture
- [Agent Configuration](./03-agent-configuration.md) - Create your own agents
- [Tools & MCP](./04-tools-and-mcp.md) - Extend agent capabilities
