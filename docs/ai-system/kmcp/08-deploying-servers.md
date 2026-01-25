# Deploying MCP Servers to Kubernetes

This guide covers deploying custom MCP servers to Kubernetes using KMCP.

## Prerequisites

- KMCP controller installed ([Controller Setup](./07-controller-setup.md))
- A built MCP server project ([FastMCP Python](./04-fastmcp-python.md) or [MCP Go](./05-mcp-go.md))
- Docker image built and accessible to the cluster

## Deployment Workflow

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│    Build     │───▶│     Load     │───▶│    Deploy    │───▶│    Test      │
│    Image     │    │   to Cluster │    │   MCPServer  │    │    Access    │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
    kmcp build         --kind-load        kmcp deploy      port-forward
```

## Building the Docker Image

### Basic Build

```bash
kmcp build --project-dir my-mcp-server -t my-mcp-server:latest
```

### Build with Options

| Flag | Description |
|------|-------------|
| `--project-dir, -d` | Project directory |
| `--tag, -t` | Image tag |
| `--platform` | Target platform (e.g., linux/amd64) |
| `--push` | Push to registry |
| `--kind-load` | Load to Kind cluster |
| `--kind-load-cluster` | Specific Kind cluster name |

### Load to Kind Cluster

```bash
kmcp build --project-dir my-mcp-server \
  -t my-mcp-server:latest \
  --kind-load-cluster kind
```

### Push to Registry

```bash
kmcp build --project-dir my-mcp-server \
  -t ghcr.io/myorg/my-mcp-server:v1.0.0 \
  --push
```

### Multi-Platform Build

```bash
kmcp build --project-dir my-mcp-server \
  -t my-mcp-server:latest \
  --platform linux/amd64,linux/arm64
```

## Deployment Methods

### Method 1: Using kmcp deploy (Recommended)

```bash
kmcp deploy --file my-mcp-server/kmcp.yaml --image my-mcp-server:latest
```

**With options:**

```bash
kmcp deploy \
  --file my-mcp-server/kmcp.yaml \
  --image my-mcp-server:latest \
  --namespace tools \
  --environment staging \
  --env "API_KEY=abc123"
```

### Method 2: Using kubectl Apply

Generate the manifest and apply:

```bash
# Generate manifest
kmcp deploy \
  --file my-mcp-server/kmcp.yaml \
  --image my-mcp-server:latest \
  --dry-run \
  --output ./deployment.yaml

# Apply to cluster
kubectl apply -f deployment.yaml
```

### Method 3: Direct kubectl

Create the MCPServer resource manually:

```yaml
# my-mcp-server.yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
  namespace: default
spec:
  deployment:
    image: "my-mcp-server:latest"
    port: 3000
    cmd: "python"
    args: ["src/main.py"]
  transportType: "stdio"
```

```bash
kubectl apply -f my-mcp-server.yaml
```

## MCPServer Resource Examples

### FastMCP Python Server

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: weather-server
  namespace: default
spec:
  deployment:
    image: "weather-server:latest"
    port: 3000
    cmd: "python"
    args: ["src/main.py"]
    env:
      - name: WEATHER_API_KEY
        valueFrom:
          secretKeyRef:
            name: weather-secrets
            key: api-key
    resources:
      limits:
        cpu: "500m"
        memory: "512Mi"
      requests:
        cpu: "100m"
        memory: "128Mi"
  transportType: "stdio"
```

### MCP Go Server

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: database-server
  namespace: default
spec:
  deployment:
    image: "database-server:latest"
    port: 3000
    cmd: "./server"
    env:
      - name: DATABASE_URL
        valueFrom:
          secretKeyRef:
            name: db-secrets
            key: connection-string
  transportType: "stdio"
```

### HTTP Transport Server

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: api-server
  namespace: default
spec:
  deployment:
    image: "api-server:latest"
    port: 8080
    cmd: "python"
    args: ["src/main.py", "--transport", "http"]
  transportType: "http"
  httpTransport:
    path: "/mcp"
```

## Deploy Command Reference

```bash
kmcp deploy [name] [flags]
```

### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--file, -f` | Path to kmcp.yaml | Current directory |
| `--image` | Docker image to deploy | From build |
| `--namespace, -n` | Kubernetes namespace | "default" |
| `--environment` | Target environment | "staging" |
| `--command` | Override command | From project config |
| `--args` | Command arguments | From project config |
| `--port` | Container port | From project config |
| `--target-port` | Target port for HTTP | Same as port |
| `--transport` | Transport type (stdio/http) | "stdio" |
| `--env` | Environment variables (KEY=VALUE) | None |
| `--dry-run` | Generate manifest only | false |
| `--output, -o` | Output file for YAML | stdout |
| `--force` | Force despite validation | false |
| `--no-inspector` | Skip MCP inspector | false |

## Verifying Deployment

### Check MCPServer Status

```bash
kubectl get mcpserver
```

Expected output:

```
NAME              TRANSPORT   STATUS    AGE
my-mcp-server     stdio       Running   1m
weather-server    stdio       Running   5m
api-server        http        Running   10m
```

### Check Pods

```bash
kubectl get pods -l app.kubernetes.io/managed-by=kmcp
```

### Check Services

```bash
kubectl get svc -l app.kubernetes.io/managed-by=kmcp
```

### Describe MCPServer

```bash
kubectl describe mcpserver my-mcp-server
```

## Testing the Deployment

### Port Forward

```bash
kubectl port-forward deploy/my-mcp-server 3000:3000
```

### Connect with MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

Configure:

| Field | Value |
|-------|-------|
| **Transport Type** | Streamable HTTP |
| **URL** | `http://127.0.0.1:3000/mcp` |

### Test with curl

```bash
# Health check (if implemented)
curl http://localhost:3000/health

# MCP endpoint
curl -X POST http://localhost:3000/mcp \
  -H "Content-Type: application/json" \
  -d '{"method": "tools/list"}'
```

## Updating Deployments

### Update Image

```bash
kmcp deploy \
  --file my-mcp-server/kmcp.yaml \
  --image my-mcp-server:v2.0.0
```

### Rolling Update

The controller handles rolling updates automatically when the MCPServer spec changes.

### Manual Update

```bash
kubectl set image deployment/my-mcp-server \
  my-mcp-server=my-mcp-server:v2.0.0
```

## Scaling

### Manual Scaling

MCPServer resources support replica specification:

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
spec:
  deployment:
    replicas: 3
    image: "my-mcp-server:latest"
    # ...
```

### Horizontal Pod Autoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: my-mcp-server-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: my-mcp-server
  minReplicas: 1
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 80
```

## Resource Management

### Setting Limits

```yaml
spec:
  deployment:
    resources:
      limits:
        cpu: "1"
        memory: "1Gi"
      requests:
        cpu: "100m"
        memory: "128Mi"
```

### Node Selection

```yaml
spec:
  deployment:
    nodeSelector:
      node-type: compute
    tolerations:
      - key: "dedicated"
        operator: "Equal"
        value: "mcp-servers"
        effect: "NoSchedule"
```

## Health Checks

### Liveness Probe

```yaml
spec:
  deployment:
    livenessProbe:
      httpGet:
        path: /health
        port: 3000
      initialDelaySeconds: 10
      periodSeconds: 10
```

### Readiness Probe

```yaml
spec:
  deployment:
    readinessProbe:
      httpGet:
        path: /ready
        port: 3000
      initialDelaySeconds: 5
      periodSeconds: 5
```

## Troubleshooting

### Pod Not Starting

```bash
# Check pod events
kubectl describe pod -l app.kubernetes.io/name=my-mcp-server

# Check logs
kubectl logs -l app.kubernetes.io/name=my-mcp-server
```

### Image Pull Errors

```bash
# Check if image exists
docker images | grep my-mcp-server

# For Kind, ensure image is loaded
kind load docker-image my-mcp-server:latest --name kind
```

### Connection Issues

```bash
# Test connectivity
kubectl exec -it deploy/my-mcp-server -- curl localhost:3000/health

# Check service
kubectl get endpoints my-mcp-server
```

### Controller Issues

```bash
# Check controller logs
kubectl logs -l app.kubernetes.io/name=kmcp -n kmcp-system

# Check MCPServer status
kubectl get mcpserver my-mcp-server -o yaml
```

## Deleting Deployments

### Delete MCPServer

```bash
kubectl delete mcpserver my-mcp-server
```

### Delete All Resources

```bash
kubectl delete mcpserver --all
```

## Integration with kagent

When using KMCP with kagent, add the discovery label:

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
  labels:
    kagent.dev/discovery: "disabled"  # Prevent auto-discovery
spec:
  # ...
```

This allows agentgateway to manage routing for agent-to-tool traffic.

## Next Steps

- [Package Deployment](./09-package-deployment.md) - Deploy using npx/uvx/bunx
- [HTTP Transport](./10-http-transport.md) - Configure HTTP-based servers
- [Secrets Management](./11-secrets-management.md) - Manage environment variables
