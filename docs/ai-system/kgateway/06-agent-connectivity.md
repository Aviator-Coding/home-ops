# Agent Connectivity Guide

> **Enable Agent-to-Agent (A2A) communication through agentgateway for autonomous AI agent collaboration.**

## Overview

Agent-to-Agent (A2A) is an open protocol developed by Google that enables communication and interoperability between autonomous AI agents. A2A supports:

- **Capability Discovery**: Agents discover what other agents can do
- **Modality Negotiation**: Agents agree on interaction formats (text, forms, media)
- **Secure Collaboration**: Agents work together on long-running tasks
- **State Isolation**: Agents interact without exposing internal state, memory, or tools

### What Are Agents?

An agent is an application that:
- Interacts with users in natural language
- Uses LLMs to generate responses
- Decides when to invoke external tools
- Can delegate tasks to other agents

### Agent Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│                     Agent Workflow                              │
├─────────────────────────────────────────────────────────────────┤
│  User Question → Agent → LLM (with tools) → Tool Calls →       │
│  Tool Execution → LLM Response → User Answer                   │
└─────────────────────────────────────────────────────────────────┘
```

Detailed flow:
1. User poses a question with available tools listed
2. Agent forwards request + tool list to LLM
3. LLM suggests appropriate tool calls with parameters
4. Agent executes suggested tools
5. Results returned to LLM for natural language response generation
6. Final response delivered to user

## Prerequisites

- Agentgateway proxy deployed (see [03-gateway-setup.md](./03-gateway-setup.md))
- A2A server accessible from the cluster

---

## A2A Server Configuration

### Deploy an A2A Server

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-a2a-agent
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: a2a-agent
  template:
    metadata:
      labels:
        app: a2a-agent
    spec:
      containers:
      - name: agent
        image: your-a2a-agent:latest
        ports:
        - containerPort: 9090
---
apiVersion: v1
kind: Service
metadata:
  name: my-a2a-agent
  namespace: default
spec:
  selector:
    app: a2a-agent
  ports:
  - port: 9090
    targetPort: 9090
    appProtocol: kgateway.dev/a2a  # Required for A2A protocol
  type: ClusterIP
```

**Important**: The `appProtocol: kgateway.dev/a2a` annotation tells agentgateway to use the A2A protocol for traffic routing.

### Create HTTPRoute for A2A

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: a2a-route
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
      namespace: kgateway-system
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /agent
    backendRefs:
    - name: my-a2a-agent
      namespace: default
      port: 9090
```

---

## Sending A2A Requests

### JSON-RPC Format

A2A uses JSON-RPC 2.0 format. Send requests to the gateway:

```bash
curl "localhost:8080/agent" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "request-123",
    "method": "tasks/send",
    "params": {
      "message": {
        "role": "user",
        "text": "What is the weather in San Francisco?"
      }
    }
  }' | jq
```

### Response Format

```json
{
  "jsonrpc": "2.0",
  "id": "request-123",
  "result": {
    "message": {
      "role": "assistant",
      "text": "The current weather in San Francisco is 65°F with partly cloudy skies."
    },
    "status": "completed"
  }
}
```

---

## Multiple Agent Configuration

### Agent Registry Pattern

Deploy multiple agents and route based on path:

```yaml
# Weather Agent
apiVersion: v1
kind: Service
metadata:
  name: weather-agent
  namespace: agents
spec:
  selector:
    app: weather-agent
  ports:
  - port: 9090
    appProtocol: kgateway.dev/a2a
---
# Search Agent
apiVersion: v1
kind: Service
metadata:
  name: search-agent
  namespace: agents
spec:
  selector:
    app: search-agent
  ports:
  - port: 9090
    appProtocol: kgateway.dev/a2a
---
# Calendar Agent
apiVersion: v1
kind: Service
metadata:
  name: calendar-agent
  namespace: agents
spec:
  selector:
    app: calendar-agent
  ports:
  - port: 9090
    appProtocol: kgateway.dev/a2a
---
# HTTPRoute for all agents
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: agent-routes
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /agent/weather
    backendRefs:
    - name: weather-agent
      namespace: agents
      port: 9090
  - matches:
    - path:
        type: PathPrefix
        value: /agent/search
    backendRefs:
    - name: search-agent
      namespace: agents
      port: 9090
  - matches:
    - path:
        type: PathPrefix
        value: /agent/calendar
    backendRefs:
    - name: calendar-agent
      namespace: agents
      port: 9090
```

### Agent Discovery

Query agent capabilities:

```bash
# Discover weather agent capabilities
curl "localhost:8080/agent/weather" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "agent/discover",
    "params": {}
  }' | jq
```

Expected response:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "name": "Weather Agent",
    "description": "Provides weather forecasts and current conditions",
    "capabilities": [
      {
        "name": "current_weather",
        "description": "Get current weather for a location"
      },
      {
        "name": "forecast",
        "description": "Get 5-day weather forecast"
      }
    ]
  }
}
```

---

## Agent-to-Agent Communication

### Orchestrator Pattern

An orchestrator agent can delegate to specialized agents:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orchestrator-agent
  namespace: agents
spec:
  replicas: 1
  selector:
    matchLabels:
      app: orchestrator-agent
  template:
    metadata:
      labels:
        app: orchestrator-agent
    spec:
      containers:
      - name: agent
        image: your-orchestrator:latest
        env:
        # Configure sub-agent endpoints through agentgateway
        - name: WEATHER_AGENT_URL
          value: "http://agentgateway-proxy.kgateway-system.svc.cluster.local/agent/weather"
        - name: SEARCH_AGENT_URL
          value: "http://agentgateway-proxy.kgateway-system.svc.cluster.local/agent/search"
        - name: CALENDAR_AGENT_URL
          value: "http://agentgateway-proxy.kgateway-system.svc.cluster.local/agent/calendar"
        ports:
        - containerPort: 9090
```

### Request Flow

```
User → Orchestrator Agent → Gateway → Weather Agent
                         ↓
                    Gateway → Search Agent
                         ↓
                    Gateway → Calendar Agent
                         ↓
User ← Orchestrator Agent ← Aggregated Response
```

---

## Long-Running Tasks

### Task Status Tracking

For long-running operations, use task IDs:

```bash
# Start a long-running task
curl "localhost:8080/agent/research" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tasks/send",
    "params": {
      "message": {
        "role": "user",
        "text": "Research the latest AI trends and compile a report"
      }
    }
  }' | jq
```

Response with task ID:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "taskId": "task-abc-123",
    "status": "in_progress",
    "message": {
      "role": "assistant",
      "text": "Starting research on AI trends..."
    }
  }
}
```

Check task status:
```bash
curl "localhost:8080/agent/research" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tasks/status",
    "params": {
      "taskId": "task-abc-123"
    }
  }' | jq
```

---

## Security Configuration

### Add RBAC to Agent Routes

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: agent-rbac
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: HTTPRoute
    name: agent-routes
  rbac:
    policy:
      matchExpressions:
        - "request.headers['x-agent-token'] == 'valid-token'"
```

### Agent Authentication Header

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: authenticated-agent
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /agent/secure
      headers:
      - name: x-agent-token
        value: "valid-token"
    backendRefs:
    - name: secure-agent
      namespace: agents
      port: 9090
```

---

## Health Checks

### Configure Agent Health Probes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-agent
  namespace: agents
spec:
  template:
    spec:
      containers:
      - name: agent
        image: your-agent:latest
        ports:
        - containerPort: 9090
        livenessProbe:
          httpGet:
            path: /health
            port: 9090
          initialDelaySeconds: 10
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /ready
            port: 9090
          initialDelaySeconds: 5
          periodSeconds: 5
```

### Gateway Health Check Endpoint

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: agent-health
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: Exact
        value: /health
    filters:
    - type: ResponseHeaderModifier
      responseHeaderModifier:
        set:
        - name: Content-Type
          value: "application/json"
    backendRefs:
    - name: my-agent
      namespace: agents
      port: 9090
```

---

## Troubleshooting

### Agent Not Responding

Check agent pod status:
```bash
kubectl get pods -n agents -l app=my-agent
kubectl logs -n agents -l app=my-agent --tail=100
```

Verify service endpoint:
```bash
kubectl get endpoints -n agents my-agent
```

### A2A Protocol Errors

Check for protocol issues:
```bash
kubectl logs -n kgateway-system -l gateway=agentgateway-proxy --tail=100 | grep -i error
```

### Connection Timeouts

Increase timeout in HTTPRoute:
```yaml
spec:
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /agent
    timeouts:
      request: 120s
    backendRefs:
    - name: my-agent
      port: 9090
```

---

*See [07-security.md](./07-security.md) for RBAC and access control configuration.*
