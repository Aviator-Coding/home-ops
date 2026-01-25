# Examples

Practical implementation examples and use cases for kagent.

## Table of Contents

1. [Basic Kubernetes Agent](#basic-kubernetes-agent)
2. [Multi-Tool Platform Agent](#multi-tool-platform-agent)
3. [Documentation Search Agent](#documentation-search-agent)
4. [Slack-Integrated Operations Bot](#slack-integrated-operations-bot)
5. [Multi-Agent Workflow](#multi-agent-workflow)
6. [Custom MCP Tool Integration](#custom-mcp-tool-integration)
7. [Ollama Local Model Setup](#ollama-local-model-setup)

---

## Basic Kubernetes Agent

A simple agent for Kubernetes cluster queries.

### Configuration

```yaml
# k8s-simple-agent.yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: k8s-simple
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a helpful Kubernetes assistant.

      Guidelines:
      - Answer questions about cluster resources
      - Format responses in Markdown
      - Use code blocks for YAML/commands
      - If unsure, say so honestly

    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
            - k8s_describe_resource
```

### Deployment

```bash
kubectl apply -f k8s-simple-agent.yaml
```

### Testing

```bash
kagent invoke -a k8s-simple -t "List all pods in the default namespace"
kagent invoke -a k8s-simple -t "Show me logs from the nginx pod"
kagent invoke -a k8s-simple -t "What deployments are failing?"
```

---

## Multi-Tool Platform Agent

A comprehensive agent combining Kubernetes, Helm, and monitoring capabilities.

### Configuration

```yaml
# platform-agent.yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: platform-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a comprehensive platform engineering assistant with expertise in:
      - Kubernetes cluster management
      - Helm chart operations
      - Prometheus monitoring
      - Service mesh configuration

      Capabilities:
      - Query and manage Kubernetes resources
      - List and analyze Helm releases
      - Query Prometheus metrics and alerts
      - Provide troubleshooting guidance

      Guidelines:
      - Always explain your reasoning
      - Provide context with answers
      - Suggest next steps when appropriate
      - Use Markdown formatting

    tools:
      # Kubernetes
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
            - k8s_describe_resource
            - k8s_get_events

      # Helm
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - helm_list_releases
            - helm_get_values
            - helm_get_history

      # Prometheus
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - prometheus_query
            - prometheus_alerts
            - prometheus_rules

    a2aConfig:
      skills:
        - id: platform-ops
          name: Platform Operations
          description: Comprehensive platform engineering assistance
          inputModes: [text]
          outputModes: [text]
          tags: [k8s, helm, prometheus, platform]
          examples:
            - "What's the overall health of my cluster?"
            - "Show me all failing pods and their logs"
            - "What Helm releases need updates?"
```

### Usage Examples

```bash
# Cluster health check
kagent invoke -a platform-agent -t "Give me an overview of cluster health"

# Troubleshooting
kagent invoke -a platform-agent -t "Why is the api-server pod crashing?"

# Combined query
kagent invoke -a platform-agent -t "Show me high CPU pods and related alerts"
```

---

## Documentation Search Agent

An agent that searches documentation using vector embeddings.

### Prerequisites

1. Build documentation database using doc2vec
2. Deploy MCP server with database

### Step 1: Create Documentation Database

```bash
git clone https://github.com/kagent-dev/doc2vec
cd doc2vec

export OPENAI_API_KEY="sk-..."

python crawl.py \
  --url "https://docs.example.com" \
  --product "my-product" \
  --version "latest" \
  --output mcp.db
```

### Step 2: Build MCP Server Image

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install mcp-server-sqlite-vec

COPY mcp.db /data/mcp.db

ENV SQLITE_DB_DIR=/data
ENV PORT=3001

ENTRYPOINT ["python", "-m", "mcp_server_sqlite_vec"]
```

```bash
docker build -t my-registry/doc-mcp:v1 .
docker push my-registry/doc-mcp:v1
```

### Step 3: Deploy to Kubernetes

```yaml
# doc-search-deployment.yaml
---
apiVersion: v1
kind: Secret
metadata:
  name: doc-search-secrets
  namespace: kagent
stringData:
  OPENAI_API_KEY: "sk-..."
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
          image: my-registry/doc-mcp:v1
          ports:
            - containerPort: 3001
          envFrom:
            - secretRef:
                name: doc-search-secrets
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
---
apiVersion: kagent.dev/v1alpha1
kind: RemoteMCPServer
metadata:
  name: doc-search-server
  namespace: kagent
spec:
  url: http://doc-search-svc.kagent.svc.cluster.local:3001
  transportType: sse
```

### Step 4: Create Documentation Agent

```yaml
# doc-agent.yaml
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
      You're a documentation assistant for my-product.

      Guidelines:
      - Always use the search tool to find relevant documentation
      - Cite sources when providing information
      - If documentation doesn't cover a topic, say so
      - Provide code examples when relevant

      Parameters for the query tool:
      - product: "my-product"
      - version: "latest"

    tools:
      - type: McpServer
        mcpServer:
          name: doc-search-server
          kind: RemoteMCPServer
          toolNames:
            - query-documentation
```

### Testing

```bash
kagent invoke -a doc-agent -t "How do I configure authentication?"
kagent invoke -a doc-agent -t "What are the API rate limits?"
```

---

## Slack-Integrated Operations Bot

Complete setup for a Slack-integrated Kubernetes operations bot.

### Step 1: Create Slack App

See [Integrations Guide](./07-integrations.md) for detailed Slack app setup.

### Step 2: Deploy Agent

```yaml
# slack-k8s-agent.yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: slack-k8s-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a Kubernetes assistant accessible via Slack.

      Guidelines:
      - Keep responses concise for chat readability
      - Use emoji sparingly for status (✅ ❌ ⚠️)
      - Format code in backticks
      - Provide actionable information

    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
            - k8s_describe_resource

    a2aConfig:
      skills:
        - id: k8s-ops
          name: Kubernetes Operations
          description: Manage and query Kubernetes cluster
          inputModes: [text]
          outputModes: [text]
          examples:
            - "Show me all pods"
            - "What's failing in the cluster?"
```

### Step 3: Deploy Slack Bot

```bash
git clone https://github.com/kagent-dev/a2a-slack-template.git
cd a2a-slack-template

cat > .env << EOF
SLACK_BOT_TOKEN=xoxb-your-token
SLACK_APP_TOKEN=xapp-your-token
KAGENT_A2A_URL=http://127.0.0.1:8083/api/a2a/kagent/slack-k8s-agent/
EOF

uv sync
uv run main.py
```

### Step 4: Add Slack Notifications (Optional)

```yaml
# slack-mcp.yaml
---
apiVersion: v1
kind: Secret
metadata:
  name: slack-credentials
  namespace: kagent
stringData:
  SLACK_BOT_TOKEN: "xoxb-..."
  SLACK_TEAM_ID: "T0123456789"
  SLACK_CHANNEL_IDS: "C0123456789"
---
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
    args: ["-y", "@modelcontextprotocol/server-slack"]
    secretRefs:
      - name: slack-credentials
  transportType: stdio
  stdioTransport: {}
```

Update agent to include Slack tool:

```yaml
tools:
  # ... existing tools ...
  - type: McpServer
    mcpServer:
      name: slack-mcp
      kind: MCPServer
      toolNames:
        - send_message_to_slack
```

---

## Multi-Agent Workflow

An orchestrator agent that coordinates specialized agents.

### Specialized Agents

```yaml
# specialized-agents.yaml
---
# Security-focused agent
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: security-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a Kubernetes security specialist.
      Focus on RBAC, network policies, and security contexts.
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - cilium_get_policies
---
# Performance-focused agent
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: performance-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a performance and monitoring specialist.
      Focus on metrics, resource usage, and optimization.
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - prometheus_query
            - prometheus_query_range
            - k8s_get_resources
```

### Orchestrator Agent

```yaml
# orchestrator-agent.yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: orchestrator
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're an orchestration agent that coordinates specialized agents.

      Available specialists:
      - security-agent: RBAC, network policies, security contexts
      - performance-agent: Metrics, resource optimization

      Guidelines:
      - Analyze the user's request
      - Delegate to appropriate specialist(s)
      - Synthesize responses into a coherent answer
      - Provide recommendations based on combined analysis

    tools:
      # Use other agents as tools
      - type: Agent
        agent:
          name: security-agent
          namespace: kagent
      - type: Agent
        agent:
          name: performance-agent
          namespace: kagent

    a2aConfig:
      skills:
        - id: comprehensive-analysis
          name: Comprehensive Analysis
          description: Coordinate multiple specialists for thorough analysis
          inputModes: [text]
          outputModes: [text]
          examples:
            - "Analyze my application for security and performance issues"
            - "Give me a complete health check of the api service"
```

### Testing

```bash
kagent invoke -a orchestrator -t "Analyze the api-server deployment for security and performance"
```

---

## Custom MCP Tool Integration

Create a custom tool for external API integration.

### Example: GitHub Integration

#### Step 1: Create MCP Server

```python
# github_mcp.py
from mcp.server import Server
from mcp.types import Tool, TextContent
import httpx

server = Server("github-mcp")

@server.tool("github_list_issues")
async def list_issues(repo: str, state: str = "open") -> list:
    """List issues from a GitHub repository."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.github.com/repos/{repo}/issues",
            params={"state": state},
            headers={"Authorization": f"token {os.environ['GITHUB_TOKEN']}"}
        )
        return response.json()

@server.tool("github_create_issue")
async def create_issue(repo: str, title: str, body: str) -> dict:
    """Create a new issue in a GitHub repository."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://api.github.com/repos/{repo}/issues",
            json={"title": title, "body": body},
            headers={"Authorization": f"token {os.environ['GITHUB_TOKEN']}"}
        )
        return response.json()

if __name__ == "__main__":
    server.run()
```

#### Step 2: Containerize

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install mcp httpx

COPY github_mcp.py .

ENTRYPOINT ["python", "github_mcp.py"]
```

#### Step 3: Deploy

```yaml
# github-mcp.yaml
---
apiVersion: v1
kind: Secret
metadata:
  name: github-credentials
  namespace: kagent
stringData:
  GITHUB_TOKEN: "ghp_..."
---
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: github-mcp
  namespace: kagent
spec:
  deployment:
    image: my-registry/github-mcp:v1
    port: 3000
    secretRefs:
      - name: github-credentials
  transportType: stdio
  stdioTransport: {}
```

#### Step 4: Create Agent

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: devops-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a DevOps assistant with GitHub integration.
      You can query issues and create new ones.
    tools:
      - type: McpServer
        mcpServer:
          name: github-mcp
          kind: MCPServer
          toolNames:
            - github_list_issues
            - github_create_issue
```

---

## Ollama Local Model Setup

Run kagent with local models using Ollama.

### Step 1: Deploy Ollama

```yaml
# ollama-deployment.yaml
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
      app: ollama
  template:
    metadata:
      labels:
        app: ollama
    spec:
      containers:
        - name: ollama
          image: ollama/ollama:latest
          ports:
            - containerPort: 11434
          resources:
            requests:
              memory: "8Gi"
              cpu: "4"
            limits:
              memory: "16Gi"
              cpu: "8"
              # Optional: GPU support
              # nvidia.com/gpu: 1
          volumeMounts:
            - name: ollama-data
              mountPath: /root/.ollama
      volumes:
        - name: ollama-data
          persistentVolumeClaim:
            claimName: ollama-pvc
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: ollama-pvc
  namespace: ollama
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 50Gi
---
apiVersion: v1
kind: Service
metadata:
  name: ollama
  namespace: ollama
spec:
  selector:
    app: ollama
  ports:
    - port: 11434
      targetPort: 11434
```

### Step 2: Pull Model

```bash
# Port-forward
kubectl port-forward -n ollama svc/ollama 11434:11434

# Pull llama3 (supports function calling)
curl http://localhost:11434/api/pull -d '{"name": "llama3"}'

# Verify
curl http://localhost:11434/api/tags
```

### Step 3: Configure ModelConfig

```yaml
# ollama-model-config.yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: ollama-llama3
  namespace: kagent
spec:
  # Placeholder - Ollama doesn't need API key
  apiKeySecret: kagent-openai
  apiKeySecretKey: OPENAI_API_KEY
  model: llama3
  provider: Ollama
  ollama:
    host: http://ollama.ollama.svc.cluster.local:11434
```

### Step 4: Create Agent with Ollama

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: local-k8s-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: ollama-llama3  # Use Ollama model
    systemMessage: |
      You're a Kubernetes assistant running on local models.
      Keep responses concise due to context limits.
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
```

### Testing

```bash
kagent invoke -a local-k8s-agent -t "List pods in default namespace"
```

---

## Next Steps

- [FAQ](./11-faq.md) - Common questions and answers
- [CLI Reference](./08-cli-reference.md) - Complete CLI documentation
- [Core Concepts](./02-core-concepts.md) - Understand the fundamentals
