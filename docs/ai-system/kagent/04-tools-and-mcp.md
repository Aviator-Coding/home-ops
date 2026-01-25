# Tools & MCP

Comprehensive guide to tools, Model Context Protocol, and extending agent capabilities.

## Understanding Tools

Tools are functions that agents use to interact with their environment. They transform agents from conversational interfaces into actionable systems.

### Tool Characteristics

| Aspect | Description |
|--------|-------------|
| **Purpose** | Enable environmental interaction |
| **Execution** | Called by LLM during reasoning |
| **Discovery** | Schema sent to LLM with instructions |
| **Results** | Returned to LLM for further processing |

---

## Tool Types

### 1. Built-in Tools

Pre-configured tools provided by kagent for common cloud-native operations.

**Available Categories:**

| Category | Tools | Use Cases |
|----------|-------|-----------|
| **Kubernetes** | 21 | Pod management, resource queries, log retrieval |
| **Prometheus** | 21 | Metric queries, alert management |
| **Cilium** | 58 | Network policies, service mesh |
| **Istio** | 13 | Service mesh configuration |
| **Grafana** | 9 | Dashboard management |
| **Argo** | 7 | Rollouts, workflows |
| **Helm** | 6 | Chart and release management |

**Usage:**
```yaml
tools:
  - type: McpServer
    mcpServer:
      name: kagent-tool-server
      kind: RemoteMCPServer
      toolNames:
        - k8s_get_resources
        - prometheus_query
```

### 2. MCP Tools (Model Context Protocol)

MCP extends agent abilities by calling external services.

**What is MCP?**
- Created by Anthropic
- Flexible protocol for providing tools to agents
- Standardized interface for tool discovery and execution
- Active community maintaining pre-built servers

**Key Benefits:**
- Schema auto-discovery
- Standardized communication
- Reusable across agents
- Community ecosystem

### 3. HTTP Tools

Tools exposed via HTTP endpoints with OpenAPI schemas.

**How it works:**
- Kagent sends user query to URL
- Response returned to agent
- Auto-discovers from OpenAPI specs

### 4. Agents as Tools

Any kagent agent can be used as a tool by other agents.

```yaml
tools:
  - type: Agent
    agent:
      name: specialized-agent
      namespace: kagent
```

---

## Creating MCP Tools

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         KAGENT                                  │
│                                                                 │
│  ┌───────────────┐         ┌───────────────┐                   │
│  │     Agent     │────────►│   MCP Server  │                   │
│  │               │◄────────│   (Tool)      │                   │
│  └───────────────┘         └───────┬───────┘                   │
│                                    │                            │
│                                    ▼                            │
│                          ┌─────────────────┐                   │
│                          │ External System │                   │
│                          │ (API, DB, etc.) │                   │
│                          └─────────────────┘                   │
└─────────────────────────────────────────────────────────────────┘
```

### Step 1: Create MCPServer Resource

Define an MCP server that runs as a Kubernetes deployment:

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: mcp-website-fetcher
  namespace: kagent
spec:
  deployment:
    # Command to run
    cmd: uvx
    args:
      - mcp-server-fetch
    # Port for the MCP server
    port: 3000
    # Optional: custom image
    # image: "my-registry/my-mcp-server:latest"
    # Optional: environment from secrets
    # secretRefs:
    #   - name: mcp-credentials
  # Transport configuration
  transportType: stdio
  stdioTransport: {}
```

### Step 2: Reference in Agent

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: fetch-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're an agent that can fetch and analyze web content.
      Use the fetch tool to retrieve webpage contents.
    tools:
      - type: McpServer
        mcpServer:
          name: mcp-website-fetcher
          kind: MCPServer
          toolNames:
            - fetch
```

### MCPServer Fields

| Field | Required | Description |
|-------|----------|-------------|
| `spec.deployment.cmd` | Yes | Command to execute |
| `spec.deployment.args` | No | Command arguments |
| `spec.deployment.port` | Yes | Server port |
| `spec.deployment.image` | No | Custom container image |
| `spec.deployment.secretRefs` | No | Secrets to mount |
| `spec.transportType` | Yes | `stdio` or `sse` |

---

## Remote MCP Servers

For MCP servers running as separate Kubernetes services.

### RemoteMCPServer Resource

```yaml
apiVersion: kagent.dev/v1alpha1
kind: RemoteMCPServer
metadata:
  name: doc-search-server
  namespace: kagent
spec:
  url: http://doc-mcp.kagent.svc.cluster.local:3001
  transportType: sse
```

### Reference in Agent

```yaml
tools:
  - type: McpServer
    mcpServer:
      name: doc-search-server
      kind: RemoteMCPServer
      toolNames:
        - query-documentation
```

---

## Example: Slack MCP Server

Integrate Slack messaging into your agents.

### Step 1: Create Secret

```bash
kubectl create secret generic slack-credentials -n kagent \
  --from-literal=SLACK_BOT_TOKEN="$SLACK_BOT_TOKEN" \
  --from-literal=SLACK_TEAM_ID="$SLACK_TEAM_ID" \
  --from-literal=SLACK_CHANNEL_IDS="$SLACK_CHANNEL_IDS"
```

### Step 2: Deploy MCPServer

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: slack-mcp
  namespace: kagent
spec:
  deployment:
    image: "node:latest"
    port: 3000
    cmd: "npx"
    args:
      - "-y"
      - "@modelcontextprotocol/server-slack"
    secretRefs:
      - name: slack-credentials
  transportType: stdio
  stdioTransport: {}
```

### Step 3: Use in Agent

```yaml
tools:
  - type: McpServer
    mcpServer:
      name: slack-mcp
      kind: MCPServer
      toolNames:
        - send_message_to_slack
        - list_channels
```

---

## Example: Documentation Search Tool

Create a documentation agent with vector search.

### Architecture

```
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│   doc2vec   │───►│ SQLite-vec   │◄───│ MCP Server  │
│  (crawler)  │    │  (embeddings)│    │  (queries)  │
└─────────────┘    └──────────────┘    └──────┬──────┘
                                              │
                                              ▼
                                       ┌─────────────┐
                                       │    Agent    │
                                       └─────────────┘
```

### Step 1: Create Database (using doc2vec)

```bash
# Clone doc2vec
git clone https://github.com/your-org/doc2vec
cd doc2vec

# Configure for your docs
export OPENAI_API_KEY="sk-..."

# Crawl documentation
python crawl.py --url "https://docs.example.com" --output mcp.db
```

### Step 2: Build Container

```dockerfile
FROM python:3.11-slim

# Install MCP server dependencies
RUN pip install mcp-server-sqlite-vec

# Copy database
COPY mcp.db /data/mcp.db

ENV SQLITE_DB_DIR=/data

ENTRYPOINT ["python", "-m", "mcp_server_sqlite_vec"]
```

### Step 3: Deploy to Kubernetes

```yaml
---
apiVersion: v1
kind: Secret
metadata:
  name: doc-search-secrets
  namespace: kagent
stringData:
  OPENAI_API_KEY: "sk-..."
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: doc-search-config
  namespace: kagent
data:
  SQLITE_DB_DIR: "/data"
  PORT: "3001"
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: doc-search-mcp
  namespace: kagent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: doc-search-mcp
  template:
    metadata:
      labels:
        app: doc-search-mcp
    spec:
      containers:
        - name: mcp-server
          image: my-registry/doc-search-mcp:latest
          ports:
            - containerPort: 3001
          envFrom:
            - secretRef:
                name: doc-search-secrets
            - configMapRef:
                name: doc-search-config
---
apiVersion: v1
kind: Service
metadata:
  name: doc-search-svc
  namespace: kagent
spec:
  selector:
    app: doc-search-mcp
  ports:
    - port: 3001
      targetPort: 3001
```

### Step 4: Create RemoteMCPServer

```yaml
apiVersion: kagent.dev/v1alpha1
kind: RemoteMCPServer
metadata:
  name: doc-search-server
  namespace: kagent
spec:
  url: http://doc-search-svc.kagent.svc.cluster.local:3001
  transportType: sse
```

### Step 5: Create Documentation Agent

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: doc-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a documentation assistant. Use the query-documentation
      tool to answer questions about our product documentation.

      Guidelines:
      - Always search the documentation before answering
      - Cite sources when possible
      - If information isn't found, say so clearly
    tools:
      - type: McpServer
        mcpServer:
          name: doc-search-server
          kind: RemoteMCPServer
          toolNames:
            - query-documentation
```

---

## Tool Selection Best Practices

### Selecting Tools for Agents

1. **Minimal Set**: Only include tools the agent needs
2. **Logical Grouping**: Group related tools together
3. **Clear Naming**: Tool names should indicate function
4. **Documentation**: Ensure tool descriptions are clear

### Example: Focused vs. Broad Agent

**Focused (Better for specific tasks):**
```yaml
tools:
  - type: McpServer
    mcpServer:
      name: kagent-tool-server
      kind: RemoteMCPServer
      toolNames:
        - k8s_get_pod_logs
        - k8s_describe_resource
```

**Broad (Better for general assistance):**
```yaml
tools:
  - type: McpServer
    mcpServer:
      name: kagent-tool-server
      kind: RemoteMCPServer
      toolNames:
        - k8s_get_resources
        - k8s_get_pod_logs
        - k8s_describe_resource
        - prometheus_query
        - helm_list_releases
```

---

## kmcp: MCP Development Tool

### What is kmcp?

A subproject of kagent providing:
- CLI for MCP server development
- Built-in boilerplates
- Deployment utilities
- Local testing capabilities

**Note:** As of v0.7, kmcp is included by default with kagent.

### Basic Commands

```bash
# Initialize new MCP project
kmcp init my-mcp-server

# Run locally
kmcp run

# Deploy to Kubernetes
kmcp deploy
```

### Separate Installation

To use kmcp separately:

```yaml
# values.yaml
kmcp:
  enabled: false
```

---

## Security Considerations

### Community MCP Servers

> ⚠️ **Warning**: Double-check any community servers before running them in your environment.

**Best Practices:**
- Review source code before deployment
- Use private registries for images
- Limit network access with NetworkPolicies
- Use least-privilege RBAC

### Secret Management

```yaml
# Good: Reference existing secrets
spec:
  deployment:
    secretRefs:
      - name: my-credentials

# Avoid: Inline credentials (never do this)
# spec:
#   deployment:
#     env:
#       - name: API_KEY
#         value: "sk-..."  # DON'T DO THIS
```

---

## Debugging Tools

### Check Tool Availability

```bash
# List all tools
kagent get tool

# Filter by category
kagent get tool | grep k8s
```

### Test Tool Execution

Via dashboard:
1. Open agent chat
2. Ask question that uses the tool
3. Check **Arguments** and **Results** tabs

Via CLI:
```bash
kagent invoke -t "List pods" --agent k8s-agent --verbose
```

### Check MCP Server Logs

```bash
# Get MCP server pods
kubectl get pods -n kagent -l app=mcp-server

# View logs
kubectl logs -n kagent <mcp-pod-name>
```

---

## Next Steps

- [LLM Providers](./05-llm-providers.md) - Configure AI providers
- [A2A Communication](./06-a2a-communication.md) - Inter-agent protocols
- [Examples](./10-examples.md) - Practical implementations
