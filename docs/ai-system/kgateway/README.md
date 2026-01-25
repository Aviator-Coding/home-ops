# Kgateway Agent Gateway Documentation

> **Complete reference documentation for deploying and configuring the AI-first data plane for agents, MCP tools, LLMs, and inference workloads in Kubernetes.**

## Overview

Kgateway Agentgateway is an open-source, highly available, highly scalable, and enterprise-grade platform that provides AI connectivity for autonomous components and tools across any environment. Originally created by Solo.io (formerly known as Gloo), it is now a CNCF Sandbox project.

### Key Capabilities

| Feature | Description |
|---------|-------------|
| **LLM Consumption** | Route requests to multiple LLM providers (OpenAI, Anthropic, Gemini, Bedrock, Azure OpenAI, Vertex AI) |
| **MCP Connectivity** | Connect to Model Context Protocol servers for tool access |
| **Agent-to-Agent (A2A)** | Enable communication between autonomous AI agents |
| **Inference Routing** | Route to local LLM inference workloads in Kubernetes |
| **Security** | CEL-based RBAC, prompt guards, API key management |
| **Observability** | OpenTelemetry integration with metrics, logs, and traces |

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Control Plane                                │
│  (Manages proxy lifecycle, translates Gateway API to config)        │
├─────────────────────────────────────────────────────────────────────┤
│                         Data Plane                                   │
│  (AgentGateway proxies processing traffic)                          │
├──────────────┬──────────────┬──────────────┬───────────────────────┤
│   Agents     │  MCP Tools   │    LLMs      │    Inference          │
└──────────────┴──────────────┴──────────────┴───────────────────────┘
```

## Documentation Structure

| Document | Description |
|----------|-------------|
| [01-quickstart.md](./01-quickstart.md) | Get started in 5 minutes |
| [02-installation.md](./02-installation.md) | Detailed installation options |
| [03-gateway-setup.md](./03-gateway-setup.md) | Gateway resource configuration |
| [04-llm-providers.md](./04-llm-providers.md) | LLM provider configuration |
| [05-mcp-connectivity.md](./05-mcp-connectivity.md) | MCP server integration |
| [06-agent-connectivity.md](./06-agent-connectivity.md) | A2A agent communication |
| [07-security.md](./07-security.md) | RBAC, prompt guards, API keys |
| [08-observability.md](./08-observability.md) | Metrics, logs, and traces |
| [09-advanced-features.md](./09-advanced-features.md) | Failover, function calling, inference |
| [10-api-reference.md](./10-api-reference.md) | Complete API and CRD reference |

## Prerequisites

- **Kubernetes cluster** (v1.25+)
- **kubectl** (within one minor version of cluster)
- **Helm** (v3.x)
- **API keys** for LLM providers you want to use

## Quick Links

- **GitHub**: [kgateway-dev/kgateway](https://github.com/kgateway-dev/kgateway)
- **Documentation**: [kgateway.dev/docs/agentgateway](https://kgateway.dev/docs/agentgateway/latest/)
- **CNCF**: Sandbox Project

## Version

This documentation covers **Kgateway Agentgateway v2.1.2** (latest stable release).

---

*Last updated: 2026-01-24*
