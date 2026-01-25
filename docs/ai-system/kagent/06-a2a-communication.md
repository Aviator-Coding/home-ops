# Agent-to-Agent (A2A) Communication

Complete guide to the A2A protocol and inter-agent communication.

## Overview

Every kagent agent implements the A2A (Agent-to-Agent) protocol, enabling:
- Agent interoperability across systems
- External client invocation
- Multi-agent workflows
- Standardized agent discovery

---

## How A2A Works

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        A2A Protocol                                 │
│                                                                     │
│  ┌─────────────┐         ┌─────────────┐         ┌─────────────┐   │
│  │ A2A Client  │────────►│   kagent    │────────►│    Agent    │   │
│  │ (External)  │◄────────│ Controller  │◄────────│  (Internal) │   │
│  └─────────────┘         └─────────────┘         └─────────────┘   │
│                                │                                    │
│                                │ Port 8083                          │
│                                ▼                                    │
│                    ┌─────────────────────┐                         │
│                    │  A2A Endpoints      │                         │
│                    │ /.well-known/       │                         │
│                    │    agent.json       │                         │
│                    └─────────────────────┘                         │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Concepts

| Concept | Description |
|---------|-------------|
| **Agent Card** | JSON describing agent capabilities |
| **Skills** | Declared agent capabilities |
| **Endpoint** | Standardized URL pattern |
| **Transport** | HTTP/SSE communication |

---

## Agent Card Discovery

### Endpoint Pattern

```
/api/a2a/{namespace}/{agent-name}/.well-known/agent.json
```

### Example Request

```bash
# Port-forward kagent controller
kubectl port-forward -n kagent svc/kagent-controller 8083:8083

# Get agent card
curl http://localhost:8083/api/a2a/kagent/k8s-agent/.well-known/agent.json
```

### Agent Card Response

```json
{
  "name": "k8s-agent",
  "description": "Kubernetes cluster management agent",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "notifications": true,
    "stateHistory": true
  },
  "skills": [
    {
      "id": "get-resources",
      "name": "Get Resources",
      "description": "List Kubernetes resources",
      "inputModes": ["text"],
      "outputModes": ["text"],
      "tags": ["k8s", "resources"],
      "examples": [
        "List all pods in the default namespace",
        "Show me running deployments"
      ]
    }
  ]
}
```

---

## Configuring A2A

### Agent Configuration

Add `a2aConfig` to your Agent spec:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: k8s-a2a-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a Kubernetes management assistant.
      Help users query and manage cluster resources.
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
    # A2A Configuration
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
            - query
          examples:
            - "Get all resources in the Kubernetes cluster"
            - "List pods in the kube-system namespace"
            - "Show me all deployments"
        - id: analyze-logs-skill
          name: Analyze Logs
          description: Retrieve and analyze pod logs
          inputModes:
            - text
          outputModes:
            - text
          tags:
            - k8s
            - logs
            - debugging
          examples:
            - "Show me logs from the nginx pod"
            - "What errors are in the api-server logs?"
```

### Skills Configuration

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique skill identifier |
| `name` | Yes | Human-readable name |
| `description` | Yes | What the skill does |
| `inputModes` | Yes | Accepted input formats |
| `outputModes` | Yes | Output formats |
| `tags` | No | Categorization labels |
| `examples` | No | Sample requests |

---

## Invoking Agents

### Method 1: Kagent Dashboard

1. Open `kagent dashboard`
2. Navigate to your agent
3. Use the chat interface

### Method 2: Kagent CLI

```bash
# Basic invocation
kagent invoke -t "Get all pods" --agent k8s-a2a-agent

# With streaming
kagent invoke -t "Analyze cluster health" --agent k8s-a2a-agent --stream

# Specify namespace
kagent invoke -t "List deployments" --agent k8s-a2a-agent -n kagent
```

### Method 3: A2A Host CLI

Using the official A2A client:

```bash
# Install A2A client
pip install a2a-client

# Invoke agent
uv run a2a-client --agent http://127.0.0.1:8083/api/a2a/kagent/k8s-a2a-agent
```

### Method 4: Direct HTTP

```bash
# Port-forward
kubectl port-forward -n kagent svc/kagent-controller 8083:8083

# Send request
curl -X POST http://localhost:8083/api/a2a/kagent/k8s-a2a-agent/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "query": "List all pods in the kagent namespace"
  }'
```

---

## Multi-Agent Workflows

### Agent Composition

Agents can use other agents as tools:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: orchestrator-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're an orchestrator that coordinates specialized agents.
      Use the appropriate agent for each subtask.
    tools:
      # Use k8s-agent as a tool
      - type: Agent
        agent:
          name: k8s-agent
          namespace: kagent
      # Use observability-agent as a tool
      - type: Agent
        agent:
          name: observability-agent
          namespace: kagent
```

### Workflow Example

```
┌─────────────────┐
│  User Request   │
│ "Debug my app"  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Orchestrator   │
│     Agent       │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌───────┐ ┌───────────┐
│ K8s   │ │Observ-    │
│ Agent │ │ability    │
└───┬───┘ │Agent      │
    │     └─────┬─────┘
    │           │
    └─────┬─────┘
          │
          ▼
   ┌──────────────┐
   │ Consolidated │
   │   Response   │
   └──────────────┘
```

---

## External Integrations

### Exposing Agents Externally

For production deployments, expose A2A endpoint via ingress:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: kagent-a2a
  namespace: kagent
  annotations:
    nginx.ingress.kubernetes.io/proxy-read-timeout: "3600"
spec:
  rules:
    - host: agents.example.com
      http:
        paths:
          - path: /api/a2a
            pathType: Prefix
            backend:
              service:
                name: kagent-controller
                port:
                  number: 8083
```

### Client Libraries

| Language | Library |
|----------|---------|
| Python | `a2a-client` |
| JavaScript | `@kagent/a2a-client` |
| Go | `github.com/kagent-dev/a2a-go` |

### Python Client Example

```python
from a2a_client import A2AClient

# Initialize client
client = A2AClient("http://localhost:8083/api/a2a/kagent/k8s-agent")

# Discover agent
agent_card = client.discover()
print(f"Agent: {agent_card['name']}")
print(f"Skills: {[s['name'] for s in agent_card['skills']]}")

# Invoke agent
response = client.invoke("List all pods")
print(response.result)
```

---

## Response Format

### Task Response

```json
{
  "taskId": "task-123",
  "status": "completed",
  "result": {
    "content": "Here are the pods in your cluster:\n\n| Name | Namespace | Status |\n|------|-----------|--------|\n| nginx-1 | default | Running |\n| api-2 | default | Running |"
  },
  "artifacts": [
    {
      "type": "text",
      "content": "..."
    }
  ]
}
```

### Streaming Response

For long-running tasks, use streaming:

```bash
curl -N http://localhost:8083/api/a2a/kagent/k8s-agent/invoke/stream \
  -H "Content-Type: application/json" \
  -d '{"query": "Analyze all pods"}'
```

---

## Best Practices

### Skill Design

1. **Be Specific**: One skill per distinct capability
2. **Clear Descriptions**: Help other agents understand what you do
3. **Good Examples**: Diverse, realistic usage examples
4. **Appropriate Tags**: Enable discovery

### Security

1. **Authentication**: Use API keys or tokens for external access
2. **Rate Limiting**: Protect against abuse
3. **Audit Logging**: Track agent invocations
4. **Network Policies**: Restrict inter-agent communication

### Performance

1. **Skill Caching**: Agent cards are cached
2. **Connection Pooling**: Reuse connections
3. **Timeouts**: Set appropriate timeouts
4. **Retries**: Handle transient failures

---

## Debugging A2A

### Check Agent Availability

```bash
# List agents with A2A
kagent get agent

# Get agent details
kubectl describe agent k8s-a2a-agent -n kagent
```

### Test Discovery

```bash
# Verify agent card
curl http://localhost:8083/api/a2a/kagent/k8s-a2a-agent/.well-known/agent.json | jq
```

### View Logs

```bash
# Controller logs
kubectl logs -n kagent deploy/kagent-controller

# Agent invocation logs
kubectl logs -n kagent deploy/kagent-controller | grep "a2a"
```

---

## Next Steps

- [Integrations](./07-integrations.md) - Slack and Discord integration
- [CLI Reference](./08-cli-reference.md) - Complete CLI documentation
- [Examples](./10-examples.md) - Practical A2A examples
