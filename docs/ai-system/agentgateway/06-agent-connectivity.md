# Agent Connectivity (A2A)

> **Enable Agent-to-Agent (A2A) communication for multi-agent orchestration.**

## Overview

The Agent-to-Agent (A2A) protocol enables:

- **Capability Discovery** - Agents discover each other's capabilities
- **Modality Negotiation** - Negotiate interaction formats (text, forms, media)
- **Secure Collaboration** - Work on long-running tasks without exposing internal state
- **Session Management** - Maintain context across interactions

---

## A2A Protocol

A2A uses JSON-RPC 2.0 over HTTP for communication:

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "method": "tasks/send",
  "params": {
    "id": "task-123",
    "message": {
      "role": "user",
      "parts": [
        {
          "type": "text",
          "text": "Analyze the Kubernetes deployment status"
        }
      ]
    }
  }
}
```

---

## A2A Service Configuration

Services must use the `kgateway.dev/a2a` appProtocol:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: analysis-agent
  namespace: ai-system
spec:
  ports:
    - protocol: TCP
      port: 9090
      targetPort: 9090
      appProtocol: kgateway.dev/a2a  # REQUIRED for A2A protocol
  selector:
    app: analysis-agent
```

---

## A2A Agent Deployment

### Step 1: Deploy Agent

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: analysis-agent
  namespace: ai-system
spec:
  replicas: 2
  selector:
    matchLabels:
      app: analysis-agent
  template:
    metadata:
      labels:
        app: analysis-agent
        a2a-agent: "true"
    spec:
      containers:
        - name: agent
          image: gcr.io/solo-public/docs/test-a2a-agent:latest
          ports:
            - containerPort: 9090
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: analysis-agent
  namespace: ai-system
spec:
  ports:
    - protocol: TCP
      port: 9090
      targetPort: 9090
      appProtocol: kgateway.dev/a2a
  selector:
    app: analysis-agent
```

### Step 2: Create Backend

For A2A agents, use a Static backend:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: a2a-agents
  namespace: ai-system
spec:
  type: Static
  static:
    hosts:
      - host: analysis-agent.ai-system.svc.cluster.local
        port: 9090
```

### Step 3: Create HTTPRoute

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: a2a-route
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /agents
      backendRefs:
        - name: a2a-agents
          group: gateway.kgateway.dev
          kind: Backend
```

---

## Multi-Agent Federation

Connect multiple agents through a single gateway:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: agent-federation
  namespace: ai-system
spec:
  type: Static
  static:
    hosts:
      - host: analysis-agent.ai-system.svc.cluster.local
        port: 9090
      - host: planning-agent.ai-system.svc.cluster.local
        port: 9090
      - host: execution-agent.ai-system.svc.cluster.local
        port: 9090
```

---

## Using kagent for Agent Management

Deploy agents using kagent CRDs:

### Agent Resource

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: kubernetes-ops-agent
  namespace: ai-system
spec:
  description: "Platform engineering AI agent for Kubernetes operations"
  systemPrompt: |
    You are a Kubernetes operations assistant. Help with cluster
    management, troubleshooting, and deployment tasks.
  modelConfigRef: litellm-gpt4
  tools:
    - name: kubectl-tool
      mcpServerRef: kubernetes-mcp-server
    - name: helm-tool
      mcpServerRef: helm-mcp-server
```

### ModelConfig for kagent

```yaml
apiVersion: kagent.dev/v1alpha2
kind: ModelConfig
metadata:
  name: litellm-gpt4
  namespace: ai-system
spec:
  provider: OpenAI
  model: gpt-4-turbo
  apiKeySecretRef: litellm-api-key
  apiKeySecretKey: key
  openAI:
    # Route through LiteLLM or AgentGateway
    baseUrl: http://litellm.ai.svc.cluster.local:4000/v1
```

---

## A2A RBAC with CEL

Control agent access with CEL expressions:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: a2a-rbac
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: a2a-route
  rbac:
    policy:
      matchExpressions:
        # Only allow authenticated agents
        - 'jwt.sub != ""'
        # Restrict to specific agent types
        - 'jwt.agent_type in ["analysis", "planning", "execution"]'
```

---

## Testing A2A Communication

### Send Task to Agent

```bash
# Port forward to gateway
kubectl port-forward svc/agentgateway -n ai-system 8080:8080 &

# Send A2A message
curl "http://localhost:8080/agents" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tasks/send",
    "params": {
      "id": "task-001",
      "message": {
        "role": "user",
        "parts": [
          {
            "type": "text",
            "text": "List all failing pods in the cluster"
          }
        ]
      }
    }
  }' | jq
```

### Check Task Status

```bash
curl "http://localhost:8080/agents" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "2",
    "method": "tasks/get",
    "params": {
      "id": "task-001"
    }
  }' | jq
```

---

## Agent Observability

Track agent interactions with OpenTelemetry:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: ai-system
spec:
  rawConfig:
    config:
      tracing:
        otlpEndpoint: http://opentelemetry-collector.monitoring.svc.cluster.local:4317
        otlpProtocol: grpc
        randomSampling: true
        fields:
          add:
            agent.name: "a2a.agent.name"
            agent.task.id: "a2a.task.id"
            agent.operation: "a2a.operation"
```

---

## Framework Integration

AgentGateway works with popular agent frameworks:

| Framework | Integration |
|-----------|-------------|
| **kagent** | Native CRD-based deployment |
| **LangGraph** | A2A protocol support |
| **AutoGen** | A2A protocol support |
| **CrewAI** | A2A protocol support |
| **Claude Desktop** | MCP client support |

### LangGraph Agent Example

```python
from langgraph.graph import StateGraph
from langchain_openai import ChatOpenAI

# Configure to use AgentGateway
llm = ChatOpenAI(
    base_url="http://agentgateway.ai-system.svc.cluster.local:8080/openai",
    api_key="not-used-with-passthrough"
)

# Build LangGraph workflow
graph = StateGraph()
# ... configure graph
```

---

## Troubleshooting

### Agent Not Responding

```bash
# Check agent pods
kubectl get pods -n ai-system -l a2a-agent=true

# Check agent logs
kubectl logs -n ai-system -l app=analysis-agent

# Test direct connection
kubectl port-forward svc/analysis-agent -n ai-system 9090:9090 &
curl http://localhost:9090/health
```

### A2A Protocol Errors

```bash
# Check appProtocol is set
kubectl get svc analysis-agent -n ai-system -o yaml | grep appProtocol

# Should show: appProtocol: kgateway.dev/a2a
```

---

## References

- [A2A Protocol Documentation](https://kgateway.dev/docs/agentgateway/latest/agent/a2a/)
- [kagent Documentation](../kagent/README.md)
- [Google A2A Specification](https://google.github.io/a2a/)
- [Agent Connectivity Guide](https://kgateway.dev/docs/agentgateway/latest/agent/)

---

*See [07-security.md](./07-security.md) for securing agent routes.*
