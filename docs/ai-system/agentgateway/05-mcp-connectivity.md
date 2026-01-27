# MCP Connectivity

> **Connect to Model Context Protocol (MCP) servers for tool access and context retrieval.**

## Overview

The Model Context Protocol (MCP) standardizes how LLM applications connect to external data sources and tools. AgentGateway provides:

- **MCP Server Federation** - Single endpoint aggregating multiple MCP servers
- **Protocol Support** - SSE (Server-Sent Events) and Streamable HTTP transports
- **Dynamic Discovery** - Automatic MCP server discovery via label selectors
- **Tool Filtering** - CEL-based access control for MCP tools

---

## MCP Protocol Transports

| Transport | Protocol | Default Path | Use Case |
|-----------|----------|--------------|----------|
| **SSE** | Server-Sent Events | `/sse` | Legacy, unidirectional |
| **Streamable HTTP** | HTTP/2 | `/mcp` | Recommended, bidirectional |

**Note:** SSE is deprecated in MCP specification (March 2025). Use Streamable HTTP for new implementations.

---

## Static MCP Backend

Explicitly configure MCP server endpoints:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-backend
  namespace: ai-system
spec:
  type: MCP
  mcp:
    targets:
    - name: kubernetes-tools
      static:
        host: kubernetes-mcp-server.ai-system.svc.cluster.local
        port: 8000
        protocol: SSE  # or StreamableHTTP
```

---

## Dynamic MCP Backend (Label Selector)

Automatically discover MCP servers via Kubernetes labels:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-dynamic
  namespace: ai-system
spec:
  type: MCP
  mcp:
    targets:
    - name: dynamic-tools
      selector:
        services:
          matchLabels:
            mcp-server: "true"
```

**Important:** Dynamic discovery only supports Streamable HTTP transport.

---

## MCP Server Service Configuration

Services must be annotated for MCP protocol:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: kubernetes-mcp-server
  namespace: ai-system
  labels:
    mcp-server: "true"  # For dynamic discovery
  annotations:
    kgateway.dev/mcp-path: "/mcp"  # Optional, defaults to /sse or /mcp
spec:
  ports:
    - port: 80
      targetPort: 8000
      appProtocol: kgateway.dev/mcp  # REQUIRED for MCP protocol
  selector:
    app: kubernetes-mcp-server
```

---

## Complete MCP Server Deployment

### Step 1: Deploy MCP Server

```yaml
# mcp-server-deployment.yaml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kubernetes-mcp-server
  namespace: ai-system
spec:
  replicas: 2
  selector:
    matchLabels:
      app: kubernetes-mcp-server
  template:
    metadata:
      labels:
        app: kubernetes-mcp-server
        mcp-server: "true"
    spec:
      serviceAccountName: kubernetes-mcp-server
      containers:
        - name: mcp-server
          image: ghcr.io/kagent-dev/kubernetes-mcp-server:latest
          ports:
            - containerPort: 8000
          env:
            - name: MCP_TRANSPORT
              value: "streamable-http"
          resources:
            requests:
              cpu: "50m"
              memory: "64Mi"
            limits:
              cpu: "200m"
              memory: "256Mi"
---
apiVersion: v1
kind: Service
metadata:
  name: kubernetes-mcp-server
  namespace: ai-system
  labels:
    mcp-server: "true"
spec:
  ports:
    - port: 80
      targetPort: 8000
      appProtocol: kgateway.dev/mcp
  selector:
    app: kubernetes-mcp-server
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: kubernetes-mcp-server
  namespace: ai-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubernetes-mcp-server
rules:
  - apiGroups: [""]
    resources: ["pods", "services", "configmaps", "namespaces"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["apps"]
    resources: ["deployments", "statefulsets", "daemonsets"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kubernetes-mcp-server
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kubernetes-mcp-server
subjects:
  - kind: ServiceAccount
    name: kubernetes-mcp-server
    namespace: ai-system
```

### Step 2: Create MCP Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: kubernetes-mcp
  namespace: ai-system
spec:
  type: MCP
  mcp:
    targets:
    - name: k8s-tools
      static:
        host: kubernetes-mcp-server.ai-system.svc.cluster.local
        port: 80
        protocol: StreamableHTTP
```

### Step 3: Create HTTPRoute

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-route
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /mcp
      backendRefs:
        - name: kubernetes-mcp
          group: gateway.kgateway.dev
          kind: Backend
```

---

## Multiple MCP Servers (Federation)

Federate multiple MCP servers behind a single endpoint:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-federation
  namespace: ai-system
spec:
  type: MCP
  mcp:
    targets:
    - name: kubernetes-tools
      static:
        host: kubernetes-mcp-server.ai-system.svc.cluster.local
        port: 80
        protocol: StreamableHTTP
    - name: github-tools
      static:
        host: github-mcp-server.ai-system.svc.cluster.local
        port: 80
        protocol: StreamableHTTP
    - name: database-tools
      static:
        host: postgres-mcp-server.ai-system.svc.cluster.local
        port: 80
        protocol: StreamableHTTP
```

---

## External MCP Servers (HTTPS)

Connect to external MCP servers with TLS:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: external-mcp
  namespace: ai-system
spec:
  type: MCP
  mcp:
    targets:
    - name: github-copilot-mcp
      static:
        host: api.githubcopilot.com
        port: 443
        path: /mcp/
        policies:
          tls:
            sni: api.githubcopilot.com
```

---

## MCP Tool Access Control

Use CEL expressions to filter tool access:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: mcp-rbac
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mcp-route
  rbac:
    policy:
      matchExpressions:
        # Allow only specific tools
        - 'mcp.tool.name in ["kubectl-get", "kubectl-describe", "list-pods"]'
        # OR restrict by user
        - 'jwt.sub == "admin" || mcp.tool.name != "kubectl-delete"'
```

### CEL Variables for MCP

| Variable | Description |
|----------|-------------|
| `mcp.tool.name` | Name of the MCP tool being invoked |
| `mcp.tool.target` | Target backend for the tool |
| `mcp.prompt.name` | MCP prompt name |
| `mcp.resource.name` | MCP resource name |

---

## MCP Server with Secrets

For MCP servers that need credentials:

### External Secret

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: github-mcp-secret
  namespace: ai-system
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: github-mcp-secret
    template:
      data:
        GITHUB_TOKEN: "{{ .GITHUB_TOKEN }}"
  dataFrom:
    - extract:
        key: github-credentials
```

### MCP Server with Secret

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: github-mcp-server
  namespace: ai-system
spec:
  template:
    spec:
      containers:
        - name: mcp-server
          image: ghcr.io/modelcontextprotocol/server-github:latest
          envFrom:
            - secretRef:
                name: github-mcp-secret
          ports:
            - containerPort: 8000
```

---

## Using kmcp for MCP Servers

Deploy MCP servers using the kmcp toolkit:

### Install kmcp

```bash
curl -fsSL https://raw.githubusercontent.com/kagent-dev/kmcp/refs/heads/main/scripts/get-kmcp.sh | bash
```

### Deploy MCP Server

```bash
# Initialize project
kmcp init python my-mcp-server
cd my-mcp-server

# Add tools
kmcp add-tool my-custom-tool

# Build and deploy
kmcp build -t my-mcp-server:latest
kmcp deploy --file kmcp.yaml --image my-mcp-server:latest
```

### kmcp MCPServer CRD

```yaml
apiVersion: kmcp.kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
  namespace: ai-system
spec:
  image: my-mcp-server:latest
  port: 8000
  transport: StreamableHTTP
  replicas: 2
  resources:
    requests:
      memory: "128Mi"
      cpu: "100m"
    limits:
      memory: "256Mi"
      cpu: "200m"
  secretRef:
    name: my-mcp-secrets
```

---

## Testing MCP Connection

### Test Tool Discovery

```bash
# Port forward to gateway
kubectl port-forward svc/agentgateway -n ai-system 8080:8080 &

# List available tools
curl "http://localhost:8080/mcp" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/list"
  }' | jq
```

### Test Tool Invocation

```bash
curl "http://localhost:8080/mcp" \
  -H "content-type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "2",
    "method": "tools/call",
    "params": {
      "name": "kubectl-get",
      "arguments": {
        "resource": "pods",
        "namespace": "ai-system"
      }
    }
  }' | jq
```

---

## Troubleshooting

### MCP Server Not Responding

```bash
# Check MCP server pods
kubectl get pods -n ai-system -l app=kubernetes-mcp-server

# Check MCP server logs
kubectl logs -n ai-system -l app=kubernetes-mcp-server

# Test direct connection to MCP server
kubectl port-forward svc/kubernetes-mcp-server -n ai-system 8000:80 &
curl http://localhost:8000/mcp -H "content-type: application/json" -d '{"jsonrpc":"2.0","id":"1","method":"tools/list"}'
```

### Backend Not Ready

```bash
# Check Backend status
kubectl get backend kubernetes-mcp -n ai-system -o yaml

# Check for appProtocol annotation
kubectl get svc kubernetes-mcp-server -n ai-system -o yaml | grep appProtocol
```

---

## References

- [Static MCP Configuration](https://kgateway.dev/docs/main/agentgateway/mcp/static-mcp/)
- [Dynamic MCP Configuration](https://kgateway.dev/docs/main/agentgateway/mcp/dynamic-mcp/)
- [MCP Protocol Specification](https://modelcontextprotocol.io/specification/)
- [kmcp Documentation](../kmcp/README.md)

---

*See [06-agent-connectivity.md](./06-agent-connectivity.md) for A2A agent communication.*
