# MCP Connectivity Guide

> **Connect to Model Context Protocol (MCP) servers through agentgateway for secure tool access.**

## Overview

Model Context Protocol (MCP) is an open protocol that standardizes how Large Language Model (LLM) applications connect to external data sources and tools. MCP eliminates the need for custom integrations, enabling standardization and scalability.

### MCP vs A2A

| Aspect | MCP | A2A |
|--------|-----|-----|
| **Focus** | Context retrieval and LLM-to-tool connection | Long-running tasks and state management |
| **Use Case** | Agent-to-tool communication | Agent-to-agent handoffs |
| **Protocol** | JSON-RPC | JSON-RPC |

### Enterprise Benefits

Agentgateway addresses enterprise MCP challenges:

- **Security**: Authentication, authorization, and auditing across interactions
- **Governance**: Policy enforcement (data residency, access control)
- **Observability**: Visibility into agent actions and workflows
- **Scalability**: Low-latency handling with retry, timeout, and failure management

## Prerequisites

- Agentgateway proxy deployed (see [03-gateway-setup.md](./03-gateway-setup.md))
- MCP server accessible from the cluster

---

## Static MCP Configuration

Use static MCP when you know the MCP server addresses ahead of time.

### Internal MCP Server (Kubernetes Service)

#### Deploy an MCP Server

Example MCP server deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-mcp-server
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mcp-server
  template:
    metadata:
      labels:
        app: mcp-server
    spec:
      containers:
      - name: mcp-server
        image: your-mcp-server:latest
        ports:
        - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: my-mcp-server
  namespace: default
spec:
  selector:
    app: mcp-server
  ports:
  - port: 8080
    targetPort: 8080
    appProtocol: kgateway.dev/mcp  # Required for MCP routing
```

**Important**: The `appProtocol: kgateway.dev/mcp` annotation tells agentgateway to use the MCP protocol.

#### Create Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-internal
  namespace: kgateway-system
spec:
  type: MCP
  mcp:
    targets:
    - name: internal-mcp
      service:
        name: my-mcp-server
        namespace: default
        port: 8080
```

#### Create HTTPRoute

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-internal
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /mcp
    backendRefs:
    - name: mcp-internal
      group: gateway.kgateway.dev
      kind: Backend
```

---

## MCP over HTTPS

Connect to external MCP servers over HTTPS with TLS validation.

### Example: GitHub Copilot MCP

#### Step 1: Create Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-github
  namespace: kgateway-system
spec:
  type: MCP
  mcp:
    targets:
    - name: mcp-target
      static:
        host: api.githubcopilot.com
        port: 443
        path: /mcp/
```

#### Step 2: Create BackendTLSPolicy

Validate the MCP server's TLS certificate:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: BackendTLSPolicy
metadata:
  name: mcp-github-tls
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.kgateway.dev
    kind: Backend
    name: mcp-github
  validation:
    hostname: api.githubcopilot.com
    wellKnownCACertificates: System
```

**Requirements**: Kubernetes Gateway API version 1.4 or later.

#### Step 3: Create HTTPRoute with Authentication

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-github
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /mcp-github
    filters:
    # CORS configuration
    - type: ResponseHeaderModifier
      responseHeaderModifier:
        set:
        - name: Access-Control-Allow-Origin
          value: "http://localhost:8080"
        - name: Access-Control-Allow-Methods
          value: "*"
        - name: Access-Control-Allow-Headers
          value: "*"
    # Authentication header injection
    - type: RequestHeaderModifier
      requestHeaderModifier:
        set:
        - name: Authorization
          value: "Bearer ${GH_PAT}"  # Your GitHub PAT
    backendRefs:
    - name: mcp-github
      group: gateway.kgateway.dev
      kind: Backend
```

#### Step 4: Apply with Token

```bash
export GH_PAT="ghp_your_github_token"

envsubst < mcp-github-route.yaml | kubectl apply -f -
```

---

## Multiple MCP Targets

Route to multiple MCP servers through a single backend:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-multi
  namespace: kgateway-system
spec:
  type: MCP
  mcp:
    targets:
    # Internal Kubernetes service
    - name: internal-tools
      service:
        name: tools-mcp-server
        namespace: tools
        port: 8080
    # External static server
    - name: external-data
      static:
        host: mcp.example.com
        port: 443
        path: /api/mcp/
    # Another internal service
    - name: internal-search
      service:
        name: search-mcp-server
        namespace: search
        port: 9090
```

---

## Testing MCP Connections

### Using MCP Inspector

Install and run the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
```

Configuration:
- **Transport Type**: Streamable HTTP
- **URL**: `http://localhost:8080/mcp`

### Manual JSON-RPC Test

List available tools:

```bash
curl "localhost:8080/mcp" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {}
  }' | jq
```

Call a tool:

```bash
curl "localhost:8080/mcp" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "get_weather",
      "arguments": {
        "location": "San Francisco"
      }
    }
  }' | jq
```

---

## MCP with Authentication

### API Key Authentication

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: mcp-api-key
  namespace: kgateway-system
type: Opaque
stringData:
  api-key: "your-mcp-api-key"
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-authenticated
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /mcp-secure
    filters:
    - type: RequestHeaderModifier
      requestHeaderModifier:
        set:
        - name: X-API-Key
          value: "your-mcp-api-key"  # Ideally injected from secret
    backendRefs:
    - name: mcp-secure-backend
      group: gateway.kgateway.dev
      kind: Backend
```

### Bearer Token Authentication

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-bearer
  namespace: kgateway-system
spec:
  parentRefs:
    - name: agentgateway-proxy
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /mcp-oauth
    filters:
    - type: RequestHeaderModifier
      requestHeaderModifier:
        set:
        - name: Authorization
          value: "Bearer eyJ..."  # OAuth/JWT token
    backendRefs:
    - name: mcp-oauth-backend
      group: gateway.kgateway.dev
      kind: Backend
```

---

## MCP Server Examples

### Filesystem MCP Server

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: filesystem-mcp
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: filesystem-mcp
  template:
    metadata:
      labels:
        app: filesystem-mcp
    spec:
      containers:
      - name: mcp
        image: modelcontextprotocol/server-filesystem:latest
        args:
        - /data
        ports:
        - containerPort: 8080
        volumeMounts:
        - name: data
          mountPath: /data
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: mcp-data-pvc
---
apiVersion: v1
kind: Service
metadata:
  name: filesystem-mcp
  namespace: default
spec:
  selector:
    app: filesystem-mcp
  ports:
  - port: 8080
    targetPort: 8080
    appProtocol: kgateway.dev/mcp
```

### Database MCP Server

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres-mcp
  namespace: default
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres-mcp
  template:
    metadata:
      labels:
        app: postgres-mcp
    spec:
      containers:
      - name: mcp
        image: modelcontextprotocol/server-postgres:latest
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: postgres-credentials
              key: url
        ports:
        - containerPort: 8080
---
apiVersion: v1
kind: Service
metadata:
  name: postgres-mcp
  namespace: default
spec:
  selector:
    app: postgres-mcp
  ports:
  - port: 8080
    targetPort: 8080
    appProtocol: kgateway.dev/mcp
```

---

## Troubleshooting

### MCP Server Not Responding

Check pod logs:
```bash
kubectl logs -l app=mcp-server --tail=100
```

Verify service connectivity:
```bash
kubectl run -it --rm debug --image=curlimages/curl -- \
  curl -v http://my-mcp-server.default.svc.cluster.local:8080/
```

### TLS Certificate Issues

Check BackendTLSPolicy status:
```bash
kubectl describe backendtlspolicy mcp-github-tls -n kgateway-system
```

### JSON-RPC Errors

Verify JSON-RPC format:
```bash
curl "localhost:8080/mcp" \
  -H "content-type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}' \
  -v 2>&1 | head -50
```

---

*See [06-agent-connectivity.md](./06-agent-connectivity.md) for agent-to-agent communication.*
