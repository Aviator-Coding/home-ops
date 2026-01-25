# HTTP Transport Configuration

Configure MCP servers to communicate over HTTP instead of stdio for production deployments, multi-agent architectures, and remote access.

## Transport Types Overview

| Transport | Protocol | Use Case | Complexity |
|-----------|----------|----------|------------|
| **stdio** | Standard I/O | Local development, single agent | Simple |
| **http** | HTTP/REST | Production, multi-agent | Medium |
| **sse** | Server-Sent Events | Real-time streaming | Medium |
| **websocket** | WebSocket | Bidirectional streaming | Complex |

## HTTP Transport Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Kubernetes Cluster                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌────────────────┐         ┌────────────────┐                 │
│   │    Ingress     │─────────│    Service     │                 │
│   │   (optional)   │         │  (ClusterIP)   │                 │
│   └────────────────┘         └───────┬────────┘                 │
│                                      │                           │
│                                      ▼                           │
│                         ┌────────────────────┐                  │
│                         │     MCPServer      │                  │
│                         │      (HTTP)        │                  │
│                         │                    │                  │
│                         │  ┌──────────────┐  │                  │
│                         │  │   /mcp       │  │                  │
│                         │  │   endpoint   │  │                  │
│                         │  └──────────────┘  │                  │
│                         └────────────────────┘                  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

External Access:
┌──────────┐   HTTPS    ┌─────────┐   HTTP    ┌───────────┐
│  Client  │──────────▶ │ Ingress │─────────▶ │ MCPServer │
└──────────┘            └─────────┘           └───────────┘
```

## Basic HTTP Configuration

### MCPServer with HTTP Transport

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: http-server
  namespace: default
spec:
  deployment:
    image: "my-mcp-server:latest"
    port: 8080
    cmd: "python"
    args: ["src/main.py", "--transport", "http"]
  transportType: http
  httpTransport:
    path: "/mcp"
```

### Deploy with CLI

```bash
kmcp deploy \
  --file my-mcp-server/kmcp.yaml \
  --image my-mcp-server:latest \
  --transport http \
  --port 8080 \
  --target-port 8080
```

## Server Implementation

### FastMCP Python HTTP Server

**src/main.py:**

```python
import argparse
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("http-server")

@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back."""
    return f"Echo: {message}"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run()
    elif args.transport == "http":
        mcp.run_http(host=args.host, port=args.port, path="/mcp")
    elif args.transport == "sse":
        mcp.run_sse(host=args.host, port=args.port, path="/sse")

if __name__ == "__main__":
    main()
```

### MCP Go HTTP Server

**main.go:**

```go
package main

import (
    "flag"
    "log"
    "net/http"
    "os"

    "github.com/mark3labs/mcp-go/server"
    "my-mcp-server/tools"
)

func main() {
    transport := flag.String("transport", "stdio", "Transport type (stdio, http, sse)")
    host := flag.String("host", "0.0.0.0", "HTTP host")
    port := flag.String("port", "8080", "HTTP port")
    flag.Parse()

    s := server.NewMCPServer("http-server", "1.0.0")
    tools.RegisterAllTools(s)

    switch *transport {
    case "stdio":
        if err := s.ServeStdio(); err != nil {
            log.Fatal(err)
        }
    case "http":
        addr := *host + ":" + *port
        log.Printf("Starting HTTP server on %s", addr)
        http.HandleFunc("/mcp", s.HandleHTTP)
        http.HandleFunc("/health", healthHandler)
        if err := http.ListenAndServe(addr, nil); err != nil {
            log.Fatal(err)
        }
    case "sse":
        addr := *host + ":" + *port
        log.Printf("Starting SSE server on %s", addr)
        http.HandleFunc("/sse", s.HandleSSE)
        http.HandleFunc("/health", healthHandler)
        if err := http.ListenAndServe(addr, nil); err != nil {
            log.Fatal(err)
        }
    }
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
    w.WriteHeader(http.StatusOK)
    w.Write([]byte("OK"))
}
```

## MCPServer HTTP Examples

### Streamable HTTP

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: streamable-http-server
  namespace: default
spec:
  deployment:
    image: "my-server:latest"
    port: 8080
    cmd: "python"
    args: ["src/main.py", "--transport", "http"]
  transportType: http
  httpTransport:
    path: "/mcp"
    streamable: true
```

### Server-Sent Events (SSE)

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: sse-server
  namespace: default
spec:
  deployment:
    image: "my-server:latest"
    port: 8080
    cmd: "python"
    args: ["src/main.py", "--transport", "sse"]
  transportType: http
  httpTransport:
    path: "/sse"
    sse: true
```

### With Health Check

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: http-server-with-health
  namespace: default
spec:
  deployment:
    image: "my-server:latest"
    port: 8080
    cmd: "python"
    args: ["src/main.py", "--transport", "http"]
    livenessProbe:
      httpGet:
        path: /health
        port: 8080
      initialDelaySeconds: 10
      periodSeconds: 10
    readinessProbe:
      httpGet:
        path: /health
        port: 8080
      initialDelaySeconds: 5
      periodSeconds: 5
  transportType: http
  httpTransport:
    path: "/mcp"
```

## Service Configuration

### ClusterIP Service (Default)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: http-server
  namespace: default
spec:
  type: ClusterIP
  selector:
    app.kubernetes.io/name: http-server
  ports:
    - port: 8080
      targetPort: 8080
      protocol: TCP
```

### NodePort Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: http-server-nodeport
  namespace: default
spec:
  type: NodePort
  selector:
    app.kubernetes.io/name: http-server
  ports:
    - port: 8080
      targetPort: 8080
      nodePort: 30080
```

### LoadBalancer Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: http-server-lb
  namespace: default
  annotations:
    service.beta.kubernetes.io/aws-load-balancer-type: "nlb"
spec:
  type: LoadBalancer
  selector:
    app.kubernetes.io/name: http-server
  ports:
    - port: 443
      targetPort: 8080
```

## Ingress Configuration

### Basic Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mcp-ingress
  namespace: default
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: nginx
  rules:
    - host: mcp.example.com
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: http-server
                port:
                  number: 8080
```

### With TLS

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mcp-ingress-tls
  namespace: default
  annotations:
    cert-manager.io/cluster-issuer: "letsencrypt-prod"
spec:
  ingressClassName: nginx
  tls:
    - hosts:
        - mcp.example.com
      secretName: mcp-tls-secret
  rules:
    - host: mcp.example.com
      http:
        paths:
          - path: /mcp
            pathType: Prefix
            backend:
              service:
                name: http-server
                port:
                  number: 8080
```

### Multiple MCP Servers

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mcp-multi-ingress
  namespace: default
spec:
  ingressClassName: nginx
  rules:
    - host: mcp.example.com
      http:
        paths:
          - path: /weather
            pathType: Prefix
            backend:
              service:
                name: weather-server
                port:
                  number: 8080
          - path: /database
            pathType: Prefix
            backend:
              service:
                name: database-server
                port:
                  number: 8080
          - path: /github
            pathType: Prefix
            backend:
              service:
                name: github-server
                port:
                  number: 8080
```

## Converting stdio to HTTP

For packages that only support stdio, use mcp-proxy:

### Using mcp-proxy

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: stdio-to-http
  namespace: default
spec:
  deployment:
    cmd: uvx
    args:
      - "mcp-proxy"
      - "--port"
      - "8080"
      - "--"
      - "npx"
      - "-y"
      - "@modelcontextprotocol/server-everything"
    port: 8080
  transportType: http
  httpTransport:
    path: "/mcp"
```

### Custom Wrapper Script

**wrapper.sh:**

```bash
#!/bin/bash
# Start the stdio MCP server with mcp-proxy
exec uvx mcp-proxy --port ${PORT:-8080} -- npx -y "$@"
```

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: wrapped-server
spec:
  deployment:
    image: "my-wrapper:latest"
    cmd: "/app/wrapper.sh"
    args: ["@modelcontextprotocol/server-github"]
    port: 8080
    env:
      - name: PORT
        value: "8080"
  transportType: http
```

## Authentication

### Basic Authentication

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: mcp-auth
  namespace: default
type: Opaque
data:
  auth: dXNlcm5hbWU6cGFzc3dvcmQ=  # base64(username:password)
---
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: mcp-auth-ingress
  annotations:
    nginx.ingress.kubernetes.io/auth-type: basic
    nginx.ingress.kubernetes.io/auth-secret: mcp-auth
    nginx.ingress.kubernetes.io/auth-realm: "MCP Authentication"
spec:
  # ...
```

### API Key Authentication

Implement in your server:

```python
from fastapi import Header, HTTPException

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != os.environ.get("API_KEY"):
        raise HTTPException(status_code=401, detail="Invalid API key")
```

## CORS Configuration

For browser-based clients:

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://app.example.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
```

## Testing HTTP Servers

### curl Testing

```bash
# Port forward
kubectl port-forward deploy/http-server 8080:8080

# Health check
curl http://localhost:8080/health

# List tools
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "method": "tools/list", "id": 1}'

# Call tool
curl -X POST http://localhost:8080/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "echo",
      "arguments": {"message": "Hello"}
    },
    "id": 2
  }'
```

### MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

Configure:
- **Transport Type**: Streamable HTTP
- **URL**: http://localhost:8080/mcp

## Troubleshooting

### Connection Refused

```bash
# Check pod is running
kubectl get pods -l app.kubernetes.io/name=http-server

# Check service endpoints
kubectl get endpoints http-server

# Check logs
kubectl logs -l app.kubernetes.io/name=http-server
```

### 502 Bad Gateway

```bash
# Check ingress controller logs
kubectl logs -l app.kubernetes.io/name=ingress-nginx -n ingress-nginx

# Verify backend service
kubectl describe ingress mcp-ingress
```

### Timeout Errors

Increase timeouts in ingress:

```yaml
annotations:
  nginx.ingress.kubernetes.io/proxy-read-timeout: "300"
  nginx.ingress.kubernetes.io/proxy-send-timeout: "300"
```

## Next Steps

- [Secrets Management](./11-secrets-management.md) - Configure API keys
- [MCPServer CRD](./12-mcpserver-crd.md) - Full API reference
- [CLI Reference](./13-cli-reference.md) - All CLI commands
