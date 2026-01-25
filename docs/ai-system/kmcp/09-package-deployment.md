# Package Deployment: npx, uvx, and bunx

Deploy MCP servers directly from package managers without building custom Docker images. This is ideal for using existing community MCP servers or quickly testing tools.

## Overview

KMCP supports deploying MCP servers using package managers:

| Manager | Language | Registry | Use Case |
|---------|----------|----------|----------|
| **npx** | Node.js/TypeScript | npm | JavaScript/TypeScript MCP servers |
| **uvx** | Python | PyPI | Python MCP servers |
| **bunx** | Node.js/TypeScript | npm | Faster JS/TS execution via Bun |

## Quick Start

### Deploy with npx

```bash
kmcp deploy package \
  --deployment-name my-server \
  --manager npx \
  --args "@modelcontextprotocol/server-everything"
```

### Deploy with uvx

```bash
kmcp deploy package \
  --deployment-name fetch-server \
  --manager uvx \
  --args "mcp-server-fetch"
```

### Deploy with bunx

```bash
kmcp deploy package \
  --deployment-name bun-server \
  --manager bunx \
  --args "@modelcontextprotocol/server-memory"
```

## npx Deployments

### Basic npx Deployment

**CLI:**

```bash
kmcp deploy package \
  --deployment-name everything-server \
  --manager npx \
  --args "@modelcontextprotocol/server-everything"
```

**MCPServer Resource:**

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: everything-server
  namespace: default
spec:
  deployment:
    cmd: npx
    args:
      - "@modelcontextprotocol/server-everything"
    port: 3000
  stdioTransport: {}
  transportType: stdio
```

### npx with Arguments

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: filesystem-server
  namespace: default
spec:
  deployment:
    cmd: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-filesystem"
      - "/data"
    port: 3000
    env:
      - name: NODE_ENV
        value: "production"
  stdioTransport: {}
  transportType: stdio
```

### Popular npx MCP Servers

| Package | Description |
|---------|-------------|
| `@modelcontextprotocol/server-everything` | Demo server with all features |
| `@modelcontextprotocol/server-filesystem` | File system operations |
| `@modelcontextprotocol/server-memory` | In-memory key-value store |
| `@modelcontextprotocol/server-puppeteer` | Browser automation |
| `@modelcontextprotocol/server-github` | GitHub API operations |
| `@modelcontextprotocol/server-slack` | Slack integration |
| `@modelcontextprotocol/server-google-drive` | Google Drive access |

### Example: GitHub Server

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: github-server
  namespace: default
spec:
  deployment:
    cmd: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-github"
    port: 3000
    env:
      - name: GITHUB_TOKEN
        valueFrom:
          secretKeyRef:
            name: github-secrets
            key: token
  stdioTransport: {}
  transportType: stdio
```

## uvx Deployments

### Basic uvx Deployment

**CLI:**

```bash
kmcp deploy package \
  --deployment-name fetch-server \
  --manager uvx \
  --args "mcp-server-fetch"
```

**MCPServer Resource:**

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: fetch-server
  namespace: default
spec:
  deployment:
    cmd: uvx
    args:
      - "mcp-server-fetch"
    port: 3000
  stdioTransport: {}
  transportType: stdio
```

### uvx with Dependencies

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: kubernetes-server
  namespace: default
spec:
  deployment:
    cmd: uvx
    args:
      - "--with"
      - "kubernetes"
      - "mcp-kubernetes-server"
    port: 3000
  stdioTransport: {}
  transportType: stdio
```

### Popular uvx MCP Servers

| Package | Description |
|---------|-------------|
| `mcp-server-fetch` | HTTP fetch operations |
| `mcp-kubernetes-server` | Kubernetes cluster operations |
| `mcp-server-sqlite` | SQLite database operations |
| `mcp-server-git` | Git repository operations |
| `mcp-server-time` | Time and timezone utilities |

### Example: Kubernetes Server

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: k8s-server
  namespace: default
spec:
  deployment:
    cmd: uvx
    args:
      - "mcp-kubernetes-server"
      - "--transport"
      - "sse"
    port: 8080
  transportType: http
  httpTransport:
    path: "/sse"
  serviceAccount: k8s-server-sa
```

## bunx Deployments

Bun provides faster startup times than Node.js for JavaScript/TypeScript servers.

### Basic bunx Deployment

**MCPServer Resource:**

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: memory-server
  namespace: default
spec:
  deployment:
    cmd: bunx
    args:
      - "@modelcontextprotocol/server-memory"
    port: 3000
  stdioTransport: {}
  transportType: stdio
```

### bunx with Custom Image

Since bunx requires Bun runtime, you may need a custom base image:

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: bun-server
  namespace: default
spec:
  deployment:
    image: "oven/bun:latest"
    cmd: bunx
    args:
      - "@modelcontextprotocol/server-everything"
    port: 3000
  stdioTransport: {}
  transportType: stdio
```

## Transport Configuration

### stdio Transport (Default)

Most package-based MCP servers use stdio transport:

```yaml
spec:
  transportType: stdio
  stdioTransport: {}
```

### Converting to HTTP with mcp-proxy

For HTTP access, wrap the stdio server with mcp-proxy:

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: http-wrapped-server
  namespace: default
spec:
  deployment:
    cmd: uvx
    args:
      - "mcp-proxy"
      - "--"
      - "npx"
      - "-y"
      - "@modelcontextprotocol/server-everything"
    port: 3000
  transportType: http
  httpTransport:
    path: "/mcp"
```

## Environment Variables

### From Secrets

```yaml
spec:
  deployment:
    env:
      - name: API_KEY
        valueFrom:
          secretKeyRef:
            name: api-secrets
            key: key
      - name: DATABASE_URL
        valueFrom:
          secretKeyRef:
            name: db-secrets
            key: url
```

### Inline Values

```yaml
spec:
  deployment:
    env:
      - name: LOG_LEVEL
        value: "debug"
      - name: MAX_CONNECTIONS
        value: "100"
```

### From ConfigMap

```yaml
spec:
  deployment:
    env:
      - name: CONFIG_PATH
        valueFrom:
          configMapKeyRef:
            name: server-config
            key: path
```

## Volume Mounts

### Persistent Volume

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: filesystem-server
  namespace: default
spec:
  deployment:
    cmd: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-filesystem"
      - "/data"
    port: 3000
    volumeMounts:
      - name: data-volume
        mountPath: /data
  volumes:
    - name: data-volume
      persistentVolumeClaim:
        claimName: data-pvc
  stdioTransport: {}
  transportType: stdio
```

### ConfigMap Volume

```yaml
spec:
  deployment:
    volumeMounts:
      - name: config
        mountPath: /etc/config
        readOnly: true
  volumes:
    - name: config
      configMap:
        name: server-config
```

## Resource Configuration

```yaml
spec:
  deployment:
    resources:
      limits:
        cpu: "500m"
        memory: "512Mi"
      requests:
        cpu: "100m"
        memory: "128Mi"
```

## Security Considerations

### Understanding the Risks

Package managers like npx and uvx download and execute code from public registries:

| Risk | Description | Mitigation |
|------|-------------|------------|
| **Supply chain attacks** | Malicious packages | Use official packages only |
| **Full host access** | Containers have cluster access | Use restrictive RBAC |
| **Network access** | Unrestricted outbound | Use NetworkPolicies |

### Network Policy

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: mcp-server-policy
  namespace: default
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/managed-by: kmcp
  policyTypes:
    - Ingress
    - Egress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: kagent
      ports:
        - port: 3000
  egress:
    - to:
        - namespaceSelector: {}
      ports:
        - port: 443  # HTTPS only
```

### Service Account

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: mcp-server-sa
  namespace: default
---
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: secure-server
spec:
  serviceAccount: mcp-server-sa
  deployment:
    cmd: npx
    args:
      - "@modelcontextprotocol/server-everything"
```

## Complete Examples

### Example 1: GitHub MCP Server

```yaml
# Create secret first
# kubectl create secret generic github-secrets --from-literal=token=ghp_xxxx

apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: github-mcp
  namespace: default
  labels:
    app: github-mcp
spec:
  deployment:
    cmd: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-github"
    port: 3000
    env:
      - name: GITHUB_TOKEN
        valueFrom:
          secretKeyRef:
            name: github-secrets
            key: token
    resources:
      limits:
        cpu: "200m"
        memory: "256Mi"
  stdioTransport: {}
  transportType: stdio
```

### Example 2: Multi-Server Deployment

```yaml
---
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: fetch-tools
spec:
  deployment:
    cmd: uvx
    args: ["mcp-server-fetch"]
    port: 3000
  transportType: stdio
---
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: filesystem-tools
spec:
  deployment:
    cmd: npx
    args: ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]
    port: 3000
    volumeMounts:
      - name: workspace
        mountPath: /workspace
  volumes:
    - name: workspace
      persistentVolumeClaim:
        claimName: workspace-pvc
  transportType: stdio
---
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: memory-tools
spec:
  deployment:
    cmd: npx
    args: ["-y", "@modelcontextprotocol/server-memory"]
    port: 3000
  transportType: stdio
```

### Example 3: Python Server with Dependencies

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: data-tools
  namespace: default
spec:
  deployment:
    cmd: uvx
    args:
      - "--with"
      - "pandas"
      - "--with"
      - "numpy"
      - "mcp-server-data-analysis"
    port: 3000
    resources:
      limits:
        cpu: "1"
        memory: "2Gi"
  stdioTransport: {}
  transportType: stdio
```

## Verification

### Check Deployment

```bash
kubectl get mcpserver
kubectl get pods -l app.kubernetes.io/managed-by=kmcp
```

### View Logs

```bash
kubectl logs -l app.kubernetes.io/name=my-server
```

### Test with Inspector

```bash
kubectl port-forward deploy/my-server 3000:3000
npx @modelcontextprotocol/inspector
```

## Troubleshooting

### Package Download Fails

```bash
# Check if pod can reach registry
kubectl exec -it deploy/my-server -- curl https://registry.npmjs.org

# Check DNS resolution
kubectl exec -it deploy/my-server -- nslookup registry.npmjs.org
```

### Permission Denied

```bash
# Check pod security context
kubectl get pod -l app.kubernetes.io/name=my-server -o yaml | grep -A5 securityContext
```

### Memory Issues

Node.js and Python can be memory-intensive. Increase limits:

```yaml
resources:
  limits:
    memory: "1Gi"
```

## Next Steps

- [HTTP Transport](./10-http-transport.md) - Configure HTTP-based servers
- [Secrets Management](./11-secrets-management.md) - Manage API keys and credentials
- [MCPServer CRD](./12-mcpserver-crd.md) - Full API reference
