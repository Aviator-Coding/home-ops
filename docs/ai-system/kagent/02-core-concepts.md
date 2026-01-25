# Core Concepts

Understanding kagent's fundamental architecture and components.

## What is an AI Agent?

An AI agent is an application that:
- Interacts with users in natural language
- Uses LLMs to generate responses
- Executes actions on behalf of users
- Reasons through multi-step problems autonomously

Unlike traditional chatbots, kagent agents transform AI insights into concrete actions through advanced reasoning and iterative planning.

---

## Architecture Components

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         User Interfaces                             │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────┐   │
│  │     CLI       │  │   Dashboard   │  │    A2A Endpoint       │   │
│  │ (kagent cmd)  │  │   (Web UI)    │  │  (Agent Protocol)     │   │
│  └───────┬───────┘  └───────┬───────┘  └───────────┬───────────┘   │
│          │                  │                      │               │
│          └──────────────────┼──────────────────────┘               │
│                             │                                       │
│                             ▼                                       │
│               ┌─────────────────────────┐                          │
│               │      App/Engine         │                          │
│               │   (Python + ADK)        │                          │
│               │  - Agent reasoning      │                          │
│               │  - Tool execution       │                          │
│               │  - Conversation loop    │                          │
│               └─────────────┬───────────┘                          │
│                             │                                       │
│          ┌──────────────────┼──────────────────┐                   │
│          │                  │                  │                   │
│          ▼                  ▼                  ▼                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐             │
│  │   Agents     │  │    Tools     │  │     MCP      │             │
│  │   (CRDs)     │  │  (Built-in)  │  │   Servers    │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
│                                                                     │
├─────────────────────────────────────────────────────────────────────┤
│                  Kubernetes Controller (Go)                         │
│         Watches CRDs, Manages Agent Lifecycle, Reconciles State    │
└─────────────────────────────────────────────────────────────────────┘
```

### Component Descriptions

| Component | Language | Purpose |
|-----------|----------|---------|
| **Controller** | Go | Kubernetes controller managing custom CRDs for AI agent creation and deployment |
| **App/Engine** | Python | Central application executing agent conversation loops, built on Google's ADK framework |
| **CLI** | Go | Primary entry point for resource management and agent interaction |
| **Dashboard** | TypeScript | Web interface for managing and interacting with agents |

---

## Core Building Blocks

### 1. Agents

Agents are the primary entities in kagent. Each agent consists of:

```
┌─────────────────────────────────────────┐
│               AI AGENT                  │
├─────────────────────────────────────────┤
│  ┌───────────────────────────────────┐  │
│  │        System Instructions        │  │
│  │  "You're a Kubernetes agent..."   │  │
│  └───────────────────────────────────┘  │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │             Tools                 │  │
│  │  - k8s_get_resources             │  │
│  │  - k8s_get_pod_logs              │  │
│  │  - helm_list_releases            │  │
│  └───────────────────────────────────┘  │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │             Skills                │  │
│  │  - Capability descriptions        │  │
│  │  - Execution logic               │  │
│  └───────────────────────────────────┘  │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │          Model Config             │  │
│  │  - Provider: OpenAI              │  │
│  │  - Model: gpt-4o-mini            │  │
│  └───────────────────────────────────┘  │
└─────────────────────────────────────────┘
```

#### Agent Instructions

System prompts defining agent behavior:

```yaml
systemMessage: |
  You're a Kubernetes agent that can help users manage their
  Kubernetes resources. Your responses should be:
  - Clear and concise
  - Formatted in Markdown
  - Honest about limitations

  When you don't know something, say "I don't know" rather
  than making up information.
```

**Best Practices:**
- Write instructions as if guiding a new colleague
- Be specific about tool usage scenarios
- Include safety guardrails
- Define response format requirements

### 2. Tools

Tools are functions that agents use to interact with their environment.

#### Tool Types

| Type | Description | Example |
|------|-------------|---------|
| **Built-in** | Pre-configured Kubernetes tools | `k8s_get_resources`, `k8s_get_pod_logs` |
| **MCP Tools** | External tools via Model Context Protocol | Custom MCP servers |
| **HTTP Tools** | URL + schema-based tools | OpenAPI-compliant services |
| **Agents as Tools** | Other agents used as tools | Hierarchical agent composition |

#### Tool Categories

Kagent provides 137+ pre-built tools across categories:

| Category | Count | Examples |
|----------|-------|----------|
| **Kubernetes** | 21 | Pod management, resource listing, log retrieval |
| **Prometheus** | 21 | Metric queries, alert management |
| **Cilium** | 58 | Network policy, service mesh |
| **Istio** | 13 | Service mesh configuration |
| **Grafana** | 9 | Dashboard management |
| **Argo** | 7 | Rollouts, workflows |
| **Helm** | 6 | Chart management |
| **Documentation** | 1 | Doc search |

### 3. Skills

Skills describe capabilities that enable autonomous operation.

#### Skill Types

**A2A Skills (Metadata-based):**
```yaml
a2aConfig:
  skills:
  - id: get-resources-skill
    name: Get Resources
    description: Get resources in the Kubernetes cluster
    inputModes:
      - text
    outputModes:
      - text
    tags:
      - k8s
      - resources
    examples:
      - "Get all resources in the Kubernetes cluster"
      - "List pods in the default namespace"
```

**Container-based Skills:**
- Executable implementations packaged as container images
- Include code snippets, behavior modules, validation logic
- Loaded from registries for reuse

#### Skill vs Tool vs Instruction

| Component | Purpose | Example |
|-----------|---------|---------|
| **Tool** | Specific function with defined output | `k8s_get_pod_logs(pod_name)` |
| **Skill** | Strategic capability guiding planning | "Can diagnose networking issues" |
| **Instruction** | Universal behavioral rules | "Always format responses in Markdown" |

---

## Custom Resource Definitions (CRDs)

Kagent uses Kubernetes CRDs for declarative configuration.

### Agent CRD

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: k8s-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a helpful Kubernetes assistant...
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
    a2aConfig:
      skills:
        - id: k8s-management
          name: Kubernetes Management
          description: Manage Kubernetes resources
          inputModes: [text]
          outputModes: [text]
```

### ModelConfig CRD

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: default-model-config
  namespace: kagent
spec:
  apiKeySecret: kagent-openai
  apiKeySecretKey: OPENAI_API_KEY
  model: gpt-4o-mini
  provider: OpenAI
  openAI: {}
```

### MCPServer CRD

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: custom-mcp-server
  namespace: kagent
spec:
  deployment:
    cmd: uvx
    args:
      - mcp-server-fetch
    port: 3000
  transportType: stdio
  stdioTransport: {}
```

### RemoteMCPServer CRD

```yaml
apiVersion: kagent.dev/v1alpha1
kind: RemoteMCPServer
metadata:
  name: remote-tool-server
  namespace: kagent
spec:
  url: http://mcp-service.kagent.svc.cluster.local:3001
  transportType: sse
```

---

## Interaction Model

### Request Flow

```
User Request
     │
     ▼
┌─────────────┐
│ CLI / UI /  │
│ A2A Client  │
└─────┬───────┘
      │
      ▼
┌─────────────┐
│   Engine    │──────────────────────┐
│  (Python)   │                      │
└─────┬───────┘                      │
      │                              │
      ▼                              │
┌─────────────┐                      │
│     LLM     │◄─────────────────────┤
│  Provider   │                      │
└─────┬───────┘                      │
      │                              │
      │ Tool Call Decision           │
      ▼                              │
┌─────────────┐                      │
│    Tools    │──── Results ─────────┘
│  Execution  │
└─────────────┘
```

### Conversation Loop

1. **User Input**: Natural language query received
2. **Context Assembly**: System message + tools + history
3. **LLM Reasoning**: Model decides on response or tool use
4. **Tool Execution**: If needed, execute tools and collect results
5. **Response Generation**: Final response to user
6. **Loop**: Return to step 3 if more tools needed

---

## Access Methods

### Dashboard

The web UI provides:
- Agent management (create, edit, delete)
- Interactive chat interface
- Tool discovery and configuration
- Session history

Access:
```bash
kagent dashboard
# or
kubectl port-forward -n kagent svc/kagent 8001:80
```

### CLI

Command-line interface for automation:
```bash
# List agents
kagent get agent

# Invoke agent
kagent invoke -t "Get pods" --agent k8s-agent

# Stream response
kagent invoke -t "Diagnose issues" --agent k8s-agent --stream
```

### A2A Protocol

Programmatic agent invocation:
```
Endpoint: /api/a2a/{namespace}/{agent-name}/.well-known/agent.json
Port: 8083
```

---

## Declarative vs Procedural

Kagent's declarative design differs from procedural frameworks:

| Aspect | Kagent (Declarative) | Procedural Frameworks |
|--------|---------------------|----------------------|
| **Configuration** | Define desired state | Write step-by-step code |
| **Tool Integration** | Reference by name | Manual wiring |
| **State Management** | Handled by controller | Manual implementation |
| **Updates** | Change YAML, reapply | Redeploy code |

---

## Next Steps

- [Agent Configuration](./03-agent-configuration.md) - Create your first custom agent
- [Tools & MCP](./04-tools-and-mcp.md) - Extend agent capabilities
- [LLM Providers](./05-llm-providers.md) - Configure different AI providers
