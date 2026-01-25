# Introduction to KMCP

## What is KMCP?

KMCP (Kubernetes Model Context Protocol) is a comprehensive toolkit designed to simplify MCP server development and production deployment. It provides a clear path from initialization to deployment without the need to write Dockerfiles, patch together Kubernetes manifests, or reverse engineer the MCP spec.

## What is MCP?

The **Model Context Protocol (MCP)** is an open protocol developed by Anthropic that standardizes how Large Language Model (LLM) applications connect to various external data sources and tools.

### The Problem MCP Solves

Without MCP, integrations require custom implementations for each tool, making systems difficult to maintain and scale. MCP servers expose data sources and tools through a standardized interface, allowing LLM applications to access them consistently.

## Core Problems KMCP Addresses

| Problem | Solution |
|---------|----------|
| **Ad-hoc scaffolding** | Configuring MCP servers and integrating them into Kubernetes requires custom work → Built-in project templates |
| **Transport fragmentation** | Supporting multiple protocols (HTTP, WebSocket, SSE) demands ongoing maintenance → Unified transport adapter |
| **Disconnected context** | Maintaining consistent security and governance across agent-to-tool communication → Agentgateway integration |

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        KMCP Architecture                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   kmcp CLI   │    │  Controller  │    │  Transport   │       │
│  │              │    │              │    │   Adapter    │       │
│  │ • init       │    │ • CRD Watch  │    │              │       │
│  │ • build      │    │ • Reconcile  │    │ • HTTP       │       │
│  │ • deploy     │    │ • Lifecycle  │    │ • WebSocket  │       │
│  │ • run        │    │   Management │    │ • SSE        │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│         │                   │                   │                │
│         └───────────────────┼───────────────────┘                │
│                             │                                    │
│                    ┌────────▼────────┐                          │
│                    │   Kubernetes    │                          │
│                    │    Cluster      │                          │
│                    │                 │                          │
│                    │  ┌───────────┐  │                          │
│                    │  │ MCPServer │  │                          │
│                    │  │    CRD    │  │                          │
│                    │  └───────────┘  │                          │
│                    └─────────────────┘                          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Key Components

### 1. CLI Tool

The CLI is your primary tool for development:

- **Scaffold projects** - Generate complete project structures
- **Manage tools** - Add and configure MCP tools
- **Build container images** - Create optimized Docker images
- **Run locally** - Test MCP servers with the MCP Inspector
- **Deploy to Kubernetes** - Push servers to your cluster

### 2. Kubernetes Controller

The Controller manages the lifecycle of your MCP server deployments:

- Watches for MCPServer Custom Resources
- Creates and manages Pods and Services
- Handles scaling and health monitoring
- Manages secrets injection

### 3. Transport Adapter

Handles external traffic routing with built-in support for multiple protocols:

- **stdio** - Standard input/output for local execution
- **HTTP Streamable** - RESTful HTTP endpoints
- **Server-Sent Events (SSE)** - Real-time event streaming
- **WebSocket** - Bidirectional communication

## Supported Frameworks

| Framework | Language | Best For |
|-----------|----------|----------|
| **FastMCP** | Python | Rapid prototyping, data science tools |
| **MCP Go** | Go | High-performance, system-level integrations |

## Core Capabilities

- ✨ Rapid project scaffolding (FastMCP Python and MCP Go SDK)
- 🚀 Single-command Kubernetes deployment
- 🔄 Consistent local-to-production workflow
- 🌐 HTTP, WebSocket, and Server-Sent Events support
- ☸️ Kubernetes-native CRD implementation
- 🔐 Integrated Kubernetes secrets management

## Integration with kagent

KMCP is a subproject of **kagent** - an AI agent platform for Kubernetes. When used with kagent:

- MCP servers become tools that agents can discover and use
- agentgateway provides security policies and observability
- Agents can reference MCP servers in their tool configurations

## When to Use KMCP

**Use KMCP when you need to:**

- Build custom MCP tools for your organization
- Deploy MCP servers to Kubernetes in production
- Manage the lifecycle of multiple MCP servers
- Integrate MCP tools with AI agents

**Consider alternatives when:**

- Running MCP servers locally for personal use (direct npx/uvx)
- Using existing public MCP servers without customization
- Working outside of Kubernetes environments

## Next Steps

- [Installation Guide](./02-installation.md) - Set up the KMCP CLI
- [Quickstart](./03-quickstart.md) - Build and deploy your first MCP server
- [FastMCP Python Guide](./04-fastmcp-python.md) - Deep dive into Python development
