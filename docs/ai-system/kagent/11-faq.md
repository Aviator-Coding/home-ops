# FAQ

Frequently asked questions about kagent.

---

## General Questions

### What is kagent?

Kagent is an open-source AI agent platform designed specifically for Kubernetes environments. Created by Solo.io in 2025, it's a Cloud Native Computing Foundation (CNCF) sandbox project that enables AI agents to automate DevOps and platform engineering tasks.

### What makes kagent different from other LLM frameworks?

Three core differentiators:

1. **Declarative Design**: Define agents, tools, and instructions in YAML; kagent handles orchestration
2. **Kubernetes-Native**: Purpose-built for Kubernetes with native CRD support
3. **Abstraction**: Handles agent complexity so you focus on business logic

### What LLM providers are supported?

- OpenAI (GPT-4, GPT-3.5)
- Anthropic (Claude 3 family)
- Azure OpenAI
- Google Gemini
- Amazon Bedrock
- Ollama (local models)
- Any OpenAI-compatible endpoint

### Is kagent free to use?

Yes, kagent is open-source under the Apache 2.0 license. You'll need API keys from your chosen LLM provider, which may have associated costs.

---

## Installation

### What are the minimum requirements?

- Kubernetes cluster (kind, minikube, or production)
- kubectl configured
- Helm 3.x
- API key for your LLM provider

### How do I install kagent?

**Quick install:**
```bash
export OPENAI_API_KEY="sk-..."
brew install kagent
kagent install --profile demo
kagent dashboard
```

**Via Helm:**
```bash
helm install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds -n kagent --create-namespace
helm install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent -n kagent --set providers.openAI.apiKey=$OPENAI_API_KEY
```

### What's the difference between `--profile demo` and `--profile minimal`?

| Profile | Pre-built Agents | Pre-loaded Tools | Best For |
|---------|------------------|------------------|----------|
| `demo` | Yes | Yes | Getting started, evaluation |
| `minimal` | No | No | Production, custom setup |

### How do I uninstall kagent?

```bash
# Via CLI
kagent uninstall

# Via Helm
helm uninstall kagent -n kagent
helm uninstall kagent-crds -n kagent
kubectl delete namespace kagent
```

---

## Agents

### How do I create an agent?

**Via Dashboard:**
1. Open `kagent dashboard`
2. Click **+ Create > New Agent**
3. Fill in configuration

**Via YAML:**
```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: my-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      Your agent instructions here...
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
```

```bash
kubectl apply -f my-agent.yaml
```

### How do I write good system instructions?

Structure your instructions:

```yaml
systemMessage: |
  # Role
  You're a [ROLE] that helps with [PURPOSE].

  # Guidelines
  - Clear behavioral rules
  - Safety guardrails
  - When to ask for clarification

  # Response Format
  - Use Markdown
  - Code in code blocks
  - Be concise
```

### Can agents use other agents?

Yes, you can reference agents as tools:

```yaml
tools:
  - type: Agent
    agent:
      name: specialized-agent
      namespace: kagent
```

---

## Tools

### What tools are available?

Kagent provides 137+ pre-built tools across categories:

| Category | Count |
|----------|-------|
| Kubernetes | 21 |
| Prometheus | 21 |
| Cilium | 58 |
| Istio | 13 |
| Grafana | 9 |
| Helm | 6 |
| Argo | 7 |

List available tools:
```bash
kagent get tool
```

### What is MCP?

MCP (Model Context Protocol) is a standardized protocol for providing tools to AI agents. Created by Anthropic, it enables:
- Standardized tool discovery
- Schema auto-detection
- Reusable tool servers
- Community ecosystem

### How do I create a custom tool?

1. Create an MCP server (Python, Node.js, etc.)
2. Package as container
3. Deploy MCPServer CRD
4. Reference in agent configuration

See [Tools & MCP](./04-tools-and-mcp.md) for detailed instructions.

### Can I use community MCP servers?

Yes, but verify them before use:
- Review source code
- Check for security issues
- Use private registries
- Apply NetworkPolicies

---

## Providers

### How do I switch LLM providers?

Create a new ModelConfig:

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

Reference in agent:
```yaml
spec:
  declarative:
    modelConfig: anthropic-config  # Use new provider
```

### Can I use local models with Ollama?

Yes. Deploy Ollama to your cluster and configure:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: ollama-config
spec:
  model: llama3
  provider: Ollama
  ollama:
    host: http://ollama.ollama.svc.cluster.local:11434
```

**Note:** Use models that support function calling (llama3, mixtral).

### How do I use Azure OpenAI?

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: azure-config
spec:
  apiKeySecret: kagent-azureopenai
  apiKeySecretKey: AZURE_OPENAI_API_KEY
  model: gpt-4o-mini
  provider: AzureOpenAI
  azureOpenAI:
    azureEndpoint: "https://your-resource.openai.azure.com/"
    apiVersion: "2025-03-01-preview"
    azureDeployment: "gpt-4o-mini"
```

---

## A2A Protocol

### What is A2A?

A2A (Agent-to-Agent) is a protocol enabling agent interoperability. Every kagent agent exposes an A2A endpoint that can be invoked by:
- Other agents
- External clients
- Chat platforms (Slack, Discord)

### How do I invoke an agent via A2A?

```bash
# Port-forward
kubectl port-forward -n kagent svc/kagent-controller 8083:8083

# Get agent card
curl http://localhost:8083/api/a2a/kagent/k8s-agent/.well-known/agent.json

# Invoke
curl -X POST http://localhost:8083/api/a2a/kagent/k8s-agent/invoke \
  -H "Content-Type: application/json" \
  -d '{"query": "List all pods"}'
```

### What are skills?

Skills are declared capabilities that describe what an agent can do. They enable discovery and help other systems understand agent capabilities.

```yaml
a2aConfig:
  skills:
    - id: get-pods
      name: Get Pods
      description: List pods in the cluster
      inputModes: [text]
      outputModes: [text]
      examples:
        - "List all pods"
```

---

## Integrations

### How do I integrate with Slack?

1. Create Slack app with required permissions
2. Deploy kagent agent with A2A config
3. Run Slack bot connecting to A2A endpoint

See [Integrations](./07-integrations.md) for step-by-step guide.

### How do I integrate with Discord?

1. Create Discord application and bot
2. Enable Message Content Intent
3. Deploy kagent agent
4. Run Discord bot

See [Integrations](./07-integrations.md) for details.

---

## Troubleshooting

### Dashboard not loading

```bash
# Check service
kubectl get svc -n kagent

# Direct port-forward
kubectl port-forward -n kagent svc/kagent 8001:80
```

### API key errors

```bash
# Verify secret exists
kubectl get secrets -n kagent

# Check secret content
kubectl describe secret kagent-openai -n kagent
```

### Agent not responding

```bash
# Check agent status
kubectl describe agent my-agent -n kagent

# View controller logs
kubectl logs -n kagent deploy/kagent-controller

# Enable verbose mode
kagent invoke -a my-agent -t "test" -v
```

### Tool not working

```bash
# List available tools
kagent get tool

# Check MCP server
kubectl get pods -n kagent -l app=mcp-server
kubectl logs -n kagent <mcp-pod>
```

---

## Contributing

### How do I report bugs?

Create an issue on GitHub: [github.com/kagent-dev/kagent/issues](https://github.com/kagent-dev/kagent/issues)

Include:
- kagent version (`kagent version`)
- Kubernetes version
- Steps to reproduce
- Error messages/logs

Generate a bug report:
```bash
kagent bug-report > bug-report.txt
```

### How do I contribute?

1. Review [CONTRIBUTING.md](https://github.com/kagent-dev/kagent/blob/main/CONTRIBUTING.md)
2. Fork the repository
3. Create feature branch
4. Submit pull request

### Where can I get help?

- **Discord**: [discord.gg/Fu3k65f2k3](https://discord.gg/Fu3k65f2k3)
- **GitHub Discussions**: [github.com/kagent-dev/kagent/discussions](https://github.com/kagent-dev/kagent/discussions)
- **Documentation**: [kagent.dev/docs](https://kagent.dev/docs)

---

## Best Practices

### Security

1. Store credentials in Kubernetes Secrets
2. Use RBAC to limit agent capabilities
3. Apply NetworkPolicies for MCP servers
4. Audit agent invocations
5. Review community tools before use

### Performance

1. Use appropriate model sizes for tasks
2. Enable streaming for long responses
3. Limit tool scope per agent
4. Cache frequently accessed data

### Agent Design

1. Single responsibility per agent
2. Clear, structured instructions
3. Include safety guardrails
4. Provide good examples
5. Test thoroughly before deployment

---

## Resources

- **Documentation**: [kagent.dev/docs](https://kagent.dev/docs)
- **GitHub**: [github.com/kagent-dev/kagent](https://github.com/kagent-dev/kagent)
- **Discord**: [discord.gg/Fu3k65f2k3](https://discord.gg/Fu3k65f2k3)
- **Blog**: [kagent.dev/blog](https://kagent.dev/blog)
- **Roadmap**: [github.com/orgs/kagent-dev/projects/3](https://github.com/orgs/kagent-dev/projects/3)
