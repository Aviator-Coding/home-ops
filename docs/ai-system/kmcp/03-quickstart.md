# KMCP Quickstart Guide

This guide walks you through creating, testing, and deploying your first MCP server in under 10 minutes.

## Prerequisites

Ensure you have completed the [Installation Guide](./02-installation.md):

- ✅ Docker installed
- ✅ Kind installed
- ✅ Helm installed
- ✅ KMCP CLI installed (`kmcp --help`)
- ✅ uv installed (for Python projects)
- ✅ MCP Inspector installed (`npx @modelcontextprotocol/inspector`)

## Step 1: Create Your Project

```bash
# Create a new FastMCP Python project
kmcp init python my-mcp-server
```

You'll see prompts for optional metadata:

```
? Description (optional): My first MCP server
? Author (optional): Your Name
```

This generates a complete project structure:

```
my-mcp-server/
├── src/
│   ├── core/           # MCP server utilities
│   ├── tools/          # Tool implementations
│   │   └── echo.py     # Sample echo tool
│   └── main.py         # Server entry point
├── tests/              # Test suite
├── Dockerfile          # Container definition
├── kmcp.yaml           # Project configuration
├── pyproject.toml      # Python dependencies
├── .env.example        # Environment template
└── README.md           # Documentation
```

## Step 2: Run Locally

```bash
kmcp run --project-dir my-mcp-server
```

This command:
1. Builds the Docker image
2. Opens the MCP Inspector automatically
3. Displays a Proxy Session Token

**Note:** Use `--no-inspector` to skip the inspector.

## Step 3: Test with MCP Inspector

Configure the inspector with these settings:

| Field | Value |
|-------|-------|
| **Transport Type** | STDIO |
| **Command** | `uv` |
| **Arguments** | `run python src/main.py` |
| **Proxy Session Token** | (paste from Step 2 output) |

Click **Connect**, then:

1. Navigate to the **Tools** tab
2. Click **List Tools**
3. Select the **echo** tool
4. Enter "Hello World" in the message field
5. Click **Run Tool**

You should see the echo response in the output.

## Step 4: Deploy to Kubernetes

### 4.1 Create a Cluster

```bash
kind create cluster --name mcp-cluster
```

### 4.2 Install the Controller

```bash
# Install CRDs
helm install kmcp-crds oci://ghcr.io/kagent-dev/kmcp/helm/kmcp-crds \
  --namespace kmcp-system \
  --create-namespace

# Install controller
kmcp install
```

### 4.3 Verify Controller

```bash
kubectl get pods -n kmcp-system
```

Wait until the controller is `Running`:

```
NAME                                      READY   STATUS    RESTARTS   AGE
kmcp-controller-manager-xxxxx-xxxxx       1/1     Running   0          30s
```

### 4.4 Build and Load Image

```bash
kmcp build --project-dir my-mcp-server -t my-mcp-server:latest --kind-load-cluster mcp-cluster
```

This builds the Docker image and loads it directly into your Kind cluster.

### 4.5 Deploy the Server

```bash
kmcp deploy --file my-mcp-server/kmcp.yaml --image my-mcp-server:latest
```

### 4.6 Verify Deployment

```bash
kubectl get pods
kubectl get mcpserver
```

## Step 5: Test the Deployed Server

### Port Forward

```bash
kubectl port-forward deploy/my-mcp-server 3000:3000
```

### Connect via Inspector

Open the MCP Inspector with **Streamable HTTP** transport:

| Field | Value |
|-------|-------|
| **Transport Type** | Streamable HTTP |
| **URL** | `http://127.0.0.1:3000/mcp` |

Test the echo tool as before.

## Complete Workflow Summary

```bash
# 1. Install CLI
curl -fsSL https://raw.githubusercontent.com/kagent-dev/kmcp/refs/heads/main/scripts/get-kmcp.sh | bash

# 2. Create project
kmcp init python my-mcp-server --non-interactive

# 3. Test locally
kmcp run --project-dir my-mcp-server

# 4. Create cluster
kind create cluster --name mcp-cluster

# 5. Install controller
helm install kmcp-crds oci://ghcr.io/kagent-dev/kmcp/helm/kmcp-crds \
  --namespace kmcp-system --create-namespace
kmcp install

# 6. Build and deploy
kmcp build --project-dir my-mcp-server -t my-mcp-server:latest --kind-load-cluster mcp-cluster
kmcp deploy --file my-mcp-server/kmcp.yaml --image my-mcp-server:latest

# 7. Test
kubectl port-forward deploy/my-mcp-server 3000:3000
npx @modelcontextprotocol/inspector
```

## Adding Custom Tools

Create a new tool:

```bash
kmcp add-tool weather --project-dir my-mcp-server
```

Edit `my-mcp-server/src/tools/weather.py`:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

@mcp.tool()
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    # Add your weather API logic here
    return f"Weather in {city}: Sunny, 72°F"
```

Rebuild and redeploy:

```bash
kmcp build --project-dir my-mcp-server -t my-mcp-server:v2 --kind-load-cluster mcp-cluster
kmcp deploy --file my-mcp-server/kmcp.yaml --image my-mcp-server:v2
```

## Next Steps

- [FastMCP Python Guide](./04-fastmcp-python.md) - Deep dive into Python development
- [MCP Go Guide](./05-mcp-go.md) - Go-based development
- [Adding Tools](./06-adding-tools.md) - Creating custom MCP tools
- [Secrets Management](./11-secrets-management.md) - Configure environment variables
- [Package Deployment](./09-package-deployment.md) - Deploy using npx/uvx
