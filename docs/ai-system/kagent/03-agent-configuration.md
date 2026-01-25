# Agent Configuration

Complete guide to creating and configuring AI agents in kagent.

## Creating Agents

### Via Dashboard (Recommended for Beginners)

1. **Access Dashboard**
   ```bash
   kagent dashboard
   ```

2. **Create New Agent**
   - Click **+ Create > New Agent** from the top menu
   - Complete the Create New Agent form

3. **Configure Fields**

### Via YAML (Recommended for GitOps)

Apply Agent CRD directly:
```bash
kubectl apply -f my-agent.yaml
```

---

## Agent Configuration Fields

### Complete Agent Spec

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: my-k8s-agent
  namespace: kagent
  labels:
    app: my-agent
spec:
  type: Declarative
  declarative:
    # LLM Configuration
    modelConfig: default-model-config

    # Agent Behavior
    systemMessage: |
      You're a friendly and helpful Kubernetes assistant.

      Guidelines:
      - Always ask for clarification if the question is unclear
      - Use Markdown formatting for responses
      - If you don't know something, say so honestly
      - Never fabricate information

      Response format:
      - Use bullet points for lists
      - Use code blocks for commands and YAML
      - Keep responses concise but complete

    # Tools Configuration
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_available_api_resources
            - k8s_get_resources
            - k8s_get_pod_logs
            - k8s_describe_resource

    # A2A Configuration (optional)
    a2aConfig:
      skills:
        - id: cluster-info
          name: Cluster Information
          description: Get information about Kubernetes cluster resources
          inputModes:
            - text
          outputModes:
            - text
          tags:
            - k8s
            - cluster
            - resources
          examples:
            - "What pods are running in the default namespace?"
            - "Show me all services in the cluster"
            - "List deployments in kube-system"
```

### Field Reference

| Field | Required | Description |
|-------|----------|-------------|
| `metadata.name` | Yes | Unique agent identifier |
| `metadata.namespace` | Yes | Kubernetes namespace |
| `spec.type` | Yes | Agent type (usually `Declarative`) |
| `spec.declarative.modelConfig` | Yes | Reference to ModelConfig CRD |
| `spec.declarative.systemMessage` | Yes | System prompt defining behavior |
| `spec.declarative.tools` | No | List of tools agent can use |
| `spec.declarative.a2aConfig` | No | A2A protocol configuration |

---

## System Message Best Practices

### Structure

```yaml
systemMessage: |
  # Role Definition
  You're a [ROLE] that helps users with [PURPOSE].

  # Guidelines
  - [Behavior rule 1]
  - [Behavior rule 2]
  - [Safety guardrail]

  # Response Format
  - [Format instruction 1]
  - [Format instruction 2]

  # Limitations
  - [What agent should NOT do]
```

### Example: Kubernetes Agent

```yaml
systemMessage: |
  You're a Kubernetes operations expert that helps users manage
  their clusters effectively.

  Guidelines:
  - Always verify the namespace before executing operations
  - Ask for confirmation before destructive operations
  - Explain what each command does before executing
  - If a request is ambiguous, ask clarifying questions

  Response Format:
  - Use Markdown formatting
  - Wrap commands in code blocks
  - Use tables for comparing resources
  - Include relevant context in responses

  Safety:
  - Never delete resources without explicit confirmation
  - Don't modify system namespaces (kube-system, kube-public)
  - Warn about potentially dangerous operations
  - If you don't know something, admit it
```

### Example: Documentation Agent

```yaml
systemMessage: |
  You're a documentation assistant that helps users find
  information in technical documentation.

  Guidelines:
  - Use the search tool to find relevant documentation
  - Cite sources with links when possible
  - Summarize complex topics clearly
  - Suggest related topics the user might find helpful

  Response Format:
  - Start with a direct answer to the question
  - Provide supporting details and context
  - Include code examples when relevant
  - End with related topics or next steps
```

---

## Tools Configuration

### Tool Types

#### 1. MCP Server Tools (Most Common)

```yaml
tools:
  - type: McpServer
    mcpServer:
      name: kagent-tool-server      # MCPServer or RemoteMCPServer name
      kind: RemoteMCPServer         # RemoteMCPServer or MCPServer
      toolNames:                    # Specific tools to enable
        - k8s_get_resources
        - k8s_get_pod_logs
```

#### 2. Built-in Tools

Reference the pre-configured tool server:

```yaml
tools:
  - type: McpServer
    mcpServer:
      name: kagent-tool-server
      kind: RemoteMCPServer
      toolNames:
        - k8s_get_available_api_resources
        - k8s_get_resources
        - k8s_describe_resource
        - k8s_get_pod_logs
        - helm_list_releases
        - prometheus_query
```

#### 3. Custom MCPServer

Reference a custom MCPServer you've deployed:

```yaml
tools:
  - type: McpServer
    mcpServer:
      name: my-custom-mcp          # Your MCPServer name
      kind: MCPServer              # Use MCPServer for local deployments
      toolNames:
        - custom_tool_1
        - custom_tool_2
```

#### 4. Agents as Tools

Use another agent as a tool:

```yaml
tools:
  - type: Agent
    agent:
      name: specialized-agent
      namespace: kagent
```

### Available Built-in Tools

#### Kubernetes Tools
| Tool | Description |
|------|-------------|
| `k8s_get_available_api_resources` | List available API resources |
| `k8s_get_resources` | List resources of a specific type |
| `k8s_describe_resource` | Get detailed resource info |
| `k8s_get_pod_logs` | Retrieve pod logs |
| `k8s_apply_resource` | Apply YAML manifests |
| `k8s_delete_resource` | Delete resources |

#### Helm Tools
| Tool | Description |
|------|-------------|
| `helm_list_releases` | List Helm releases |
| `helm_get_values` | Get release values |
| `helm_get_history` | Get release history |
| `helm_rollback` | Rollback a release |

#### Prometheus Tools
| Tool | Description |
|------|-------------|
| `prometheus_query` | Execute PromQL query |
| `prometheus_query_range` | Query over time range |
| `prometheus_alerts` | Get active alerts |
| `prometheus_rules` | List alerting rules |

---

## A2A Configuration

Enable agent-to-agent communication by defining skills.

### Skills Definition

```yaml
a2aConfig:
  skills:
    - id: unique-skill-id
      name: Human Readable Name
      description: What this skill does
      inputModes:
        - text
      outputModes:
        - text
      tags:
        - category1
        - category2
      examples:
        - "Example request 1"
        - "Example request 2"
```

### Skill Field Reference

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier for the skill |
| `name` | Yes | Human-readable name |
| `description` | Yes | What the skill does |
| `inputModes` | Yes | Accepted input formats (`text`) |
| `outputModes` | Yes | Output formats (`text`) |
| `tags` | No | Categorization labels |
| `examples` | No | Sample requests |

### Best Practices for Skills

1. **Be Specific**: Each skill should represent a distinct capability
2. **Provide Examples**: Include diverse, realistic examples
3. **Use Descriptive Tags**: Help with skill discovery
4. **Align with Tools**: Skills should match available tools
5. **Focused Purpose**: One skill per capability

---

## Complete Examples

### Basic Kubernetes Agent

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: k8s-basic
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a Kubernetes assistant. Help users query and
      understand their cluster resources.

      Always use Markdown formatting and be concise.
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
```

### Observability Agent with A2A

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: observability-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're an observability expert specializing in Prometheus,
      Grafana, and Kubernetes monitoring.

      Guidelines:
      - Help users write and debug PromQL queries
      - Explain metric meanings and relationships
      - Suggest relevant dashboards and alerts
      - Identify performance issues from metrics

      Always explain your reasoning and provide context.
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - prometheus_query
            - prometheus_query_range
            - prometheus_alerts
            - grafana_list_dashboards
            - k8s_get_resources
    a2aConfig:
      skills:
        - id: promql-help
          name: PromQL Assistance
          description: Help write and debug PromQL queries
          inputModes: [text]
          outputModes: [text]
          tags: [prometheus, promql, monitoring]
          examples:
            - "Write a query to show CPU usage by pod"
            - "Why isn't my PromQL query returning results?"
        - id: alert-analysis
          name: Alert Analysis
          description: Analyze and explain Prometheus alerts
          inputModes: [text]
          outputModes: [text]
          tags: [prometheus, alerts, debugging]
          examples:
            - "What alerts are currently firing?"
            - "Explain this alert and how to fix it"
```

### Multi-Tool Agent

```yaml
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
      You're a comprehensive platform engineering assistant with
      expertise in Kubernetes, Helm, Istio, and monitoring.

      You can:
      - Query and manage Kubernetes resources
      - Manage Helm releases
      - Configure Istio service mesh
      - Query Prometheus metrics
      - Analyze Grafana dashboards

      Always explain your actions and provide context.
    tools:
      # Kubernetes tools
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
            - k8s_describe_resource
      # Helm tools
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - helm_list_releases
            - helm_get_values
      # Prometheus tools
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - prometheus_query
            - prometheus_alerts
      # Istio tools
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - istio_get_virtual_services
            - istio_get_destination_rules
```

---

## Deployment

### Apply Agent

```bash
kubectl apply -f my-agent.yaml
```

### Verify Deployment

```bash
# Check agent status
kubectl get agents -n kagent

# Describe agent
kubectl describe agent my-k8s-agent -n kagent

# List via CLI
kagent get agent
```

### Update Agent

```bash
# Edit and reapply
kubectl apply -f my-agent.yaml

# Or edit directly
kubectl edit agent my-k8s-agent -n kagent
```

### Delete Agent

```bash
kubectl delete agent my-k8s-agent -n kagent
```

---

## Testing Your Agent

### Via CLI

```bash
# Simple query
kagent invoke -t "What pods are running?" --agent my-k8s-agent

# With streaming
kagent invoke -t "Analyze cluster health" --agent my-k8s-agent --stream

# From file
echo "List all deployments with their replicas" > task.txt
kagent invoke --file task.txt --agent my-k8s-agent
```

### Via Dashboard

1. Open `kagent dashboard`
2. Navigate to your agent
3. Enter test queries in the chat interface
4. Review tool calls in the **Arguments** and **Results** tabs

### Via A2A

```bash
# Port-forward
kubectl port-forward -n kagent svc/kagent-controller 8083:8083

# Invoke via A2A
curl -X POST http://localhost:8083/api/a2a/kagent/my-k8s-agent \
  -H "Content-Type: application/json" \
  -d '{"query": "List all pods"}'
```

---

## Next Steps

- [Tools & MCP](./04-tools-and-mcp.md) - Create custom tools
- [LLM Providers](./05-llm-providers.md) - Configure different providers
- [A2A Communication](./06-a2a-communication.md) - Inter-agent communication
