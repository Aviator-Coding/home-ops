# KMCP Documentation

Comprehensive documentation for **KMCP** (Kubernetes Model Context Protocol) - a toolkit for developing and deploying MCP servers to Kubernetes.

## What is KMCP?

KMCP is a comprehensive platform to accelerate the local development of Model Context Protocol (MCP) servers and manage their lifecycle in cloud-native environments, such as Kubernetes. It provides:

- **Rapid Project Scaffolding** - Create MCP projects with built-in boilerplates
- **Container Build Pipeline** - Build optimized Docker images
- **Kubernetes Deployment** - Deploy MCP servers as native Kubernetes resources
- **Secrets Management** - Configure environment variables from Kubernetes secrets
- **Transport Flexibility** - Support for stdio and HTTP Streamable transports

## Documentation Index

### Getting Started

| Document | Description |
|----------|-------------|
| [Introduction](./01-introduction.md) | Overview, architecture, and core concepts |
| [Installation](./02-installation.md) | Installing the KMCP CLI and controller |
| [Quickstart](./03-quickstart.md) | End-to-end tutorial from project creation to deployment |

### Development Guides

| Document | Description |
|----------|-------------|
| [FastMCP Python](./04-fastmcp-python.md) | Python-based MCP server development |
| [MCP Go](./05-mcp-go.md) | Go-based MCP server development |
| [Adding Tools](./06-adding-tools.md) | Creating custom MCP tools |

### Deployment Guides

| Document | Description |
|----------|-------------|
| [Controller Setup](./07-controller-setup.md) | Installing the KMCP controller in Kubernetes |
| [Deploying Servers](./08-deploying-servers.md) | Deploy MCP servers to Kubernetes |
| [Package Deployment](./09-package-deployment.md) | Deploy using npx, uvx, and bunx |
| [HTTP Transport](./10-http-transport.md) | Configuring HTTP-based MCP servers |

### Configuration

| Document | Description |
|----------|-------------|
| [Secrets Management](./11-secrets-management.md) | Managing environment variables and secrets |
| [MCPServer CRD](./12-mcpserver-crd.md) | Complete API reference for MCPServer resources |

### CLI Reference

| Document | Description |
|----------|-------------|
| [CLI Commands](./13-cli-reference.md) | Complete CLI command reference |

## Quick Reference

### Install KMCP CLI

```bash
curl -fsSL https://raw.githubusercontent.com/kagent-dev/kmcp/refs/heads/main/scripts/get-kmcp.sh | bash
```

### Create a New Project

```bash
# Python project
kmcp init python my-mcp-server

# Go project
kmcp init go my-mcp-server --go-module-name my-mcp-server
```

### Deploy to Kubernetes

```bash
# Install controller (once per cluster)
helm install kmcp-crds oci://ghcr.io/kagent-dev/kmcp/helm/kmcp-crds \
  --namespace kmcp-system --create-namespace
kmcp install

# Build and deploy
kmcp build --project-dir my-mcp-server -t my-mcp-server:latest
kmcp deploy --file my-mcp-server/kmcp.yaml --image my-mcp-server:latest
```

## Resources

- **Official Documentation**: [kagent.dev/docs/kmcp](https://kagent.dev/docs/kmcp)
- **GitHub Repository**: [github.com/kagent-dev/kmcp](https://github.com/kagent-dev/kmcp)
- **Discord Community**: [kagent Discord](https://kagent.dev)
- **License**: Apache 2.0

## Version

This documentation covers KMCP as of January 2026.
