# KMCP Installation Guide

## Prerequisites

Before installing KMCP, ensure you have the following tools installed:

| Tool | Purpose | Installation |
|------|---------|--------------|
| **Docker** | Building container images | [docker.com](https://docker.com) |
| **Kind** | Local Kubernetes clusters | `brew install kind` or [kind.sigs.k8s.io](https://kind.sigs.k8s.io) |
| **Helm** | Installing KMCP charts | `brew install helm` or [helm.sh](https://helm.sh) |
| **kubectl** | Kubernetes CLI | `brew install kubectl` |

### Framework-Specific Prerequisites

**For Python (FastMCP):**

```bash
# Install uv - Rust-powered Python package manager
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**For Go (MCP Go):**

```bash
# Go 1.23 or later required
# Check version
go version
```

### MCP Inspector (Testing Tool)

```bash
# Install globally for testing MCP servers
npm install -g @modelcontextprotocol/inspector
```

## Installing the KMCP CLI

### Quick Install (Recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/kagent-dev/kmcp/refs/heads/main/scripts/get-kmcp.sh | bash
```

### Verify Installation

```bash
kmcp --help
```

Expected output:

```
kmcp is a CLI tool for developing and deploying MCP servers

Usage:
  kmcp [command]

Available Commands:
  add-tool    Add a tool to your MCP project
  build       Build a Docker image for your MCP server
  completion  Generate autocompletion scripts
  deploy      Deploy your MCP server to Kubernetes
  help        Help about any command
  init        Create a scaffold for your MCP server project
  install     Install the KMCP controller
  run         Run an MCP server locally
  secrets     Manage Kubernetes secrets for MCP servers

Flags:
  -h, --help      help for kmcp
  -v, --verbose   verbose output

Use "kmcp [command] --help" for more information about a command.
```

## Installing the KMCP Controller

The controller must be installed once per Kubernetes cluster.

### Step 1: Create or Switch to a Cluster

**Create a new Kind cluster:**

```bash
kind create cluster --name mcp-cluster
```

**Or switch to an existing cluster:**

```bash
kubectl config use-context <your-cluster-context>
```

### Step 2: Install Custom Resource Definitions

```bash
helm install kmcp-crds oci://ghcr.io/kagent-dev/kmcp/helm/kmcp-crds \
  --namespace kmcp-system \
  --create-namespace
```

### Step 3: Deploy the Controller

```bash
kmcp install
```

This command installs three components:

| Component | Description |
|-----------|-------------|
| **MCPServer CRD** | Custom Resource Definition for MCP servers |
| **RBAC Configuration** | ClusterRole and ClusterRoleBinding |
| **Controller Deployment** | The kmcp-controller-manager Pod |

### Step 4: Verify Installation

```bash
# Check controller pod status
kubectl get pods -n kmcp-system
```

Expected output:

```
NAME                                      READY   STATUS    RESTARTS   AGE
kmcp-controller-manager-xxxxx-xxxxx       1/1     Running   0          30s
```

### Step 5: View Controller Logs (Optional)

```bash
kubectl logs -l app.kubernetes.io/name=kmcp -n kmcp-system
```

## Installation Options

### Custom Namespace

```bash
kmcp install --namespace my-custom-namespace
```

### Specific Version

```bash
kmcp install --version v1.2.3
```

### Verbose Output

```bash
kmcp install --verbose
```

## Shell Autocompletion

Enable shell autocompletion for easier CLI usage:

**Bash:**

```bash
kmcp completion bash > /etc/bash_completion.d/kmcp
```

**Zsh:**

```bash
kmcp completion zsh > "${fpath[1]}/_kmcp"
```

**Fish:**

```bash
kmcp completion fish > ~/.config/fish/completions/kmcp.fish
```

**PowerShell:**

```powershell
kmcp completion powershell > kmcp.ps1
```

## Uninstalling KMCP

### Remove the Controller

```bash
kubectl delete namespace kmcp-system
```

### Remove CRDs

```bash
helm uninstall kmcp-crds -n kmcp-system
```

### Remove CLI (if installed via script)

```bash
rm -f /usr/local/bin/kmcp
```

## Troubleshooting

### Controller Not Starting

Check for resource constraints:

```bash
kubectl describe pod -l app.kubernetes.io/name=kmcp -n kmcp-system
```

### CRD Installation Failed

Ensure Helm has OCI registry support:

```bash
helm version
# Requires Helm 3.8.0 or later
```

### Permission Issues

Verify RBAC configuration:

```bash
kubectl get clusterrolebinding | grep kmcp
```

## Next Steps

- [Quickstart Guide](./03-quickstart.md) - Build and deploy your first MCP server
- [FastMCP Python](./04-fastmcp-python.md) - Python-based development
- [MCP Go](./05-mcp-go.md) - Go-based development
