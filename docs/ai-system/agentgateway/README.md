# AgentGateway Documentation

> **Complete reference documentation for deploying and configuring AgentGateway - the AI-first data plane for agents, MCP tools, LLMs, and inference workloads in Kubernetes.**

## Overview

AgentGateway is an open-source, Rust-based AI-first data plane that provides connectivity for agents, MCP tools, LLMs, and inference workloads. It is part of the kgateway ecosystem (CNCF Sandbox project) originally created by Solo.io.

### Key Capabilities

| Feature | Description |
|---------|-------------|
| **LLM Consumption** | Route requests to multiple LLM providers (OpenAI, Anthropic, Gemini, Bedrock, Azure OpenAI, Vertex AI) |
| **MCP Connectivity** | Connect to Model Context Protocol servers for tool access |
| **Agent-to-Agent (A2A)** | Enable communication between autonomous AI agents |
| **Inference Routing** | Route to local LLM inference workloads (Ollama, vLLM, TensorRT-LLM) |
| **Security** | CEL-based RBAC, prompt guards, API key management |
| **Observability** | OpenTelemetry integration with metrics, logs, and traces |

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         Control Plane (kgateway)                         │
│  (Manages proxy lifecycle, translates Gateway API to xDS config)        │
├─────────────────────────────────────────────────────────────────────────┤
│                    Data Plane (AgentGateway - Rust)                      │
│  (Proxies processing AI traffic with MCP/A2A protocol support)          │
├──────────────────┬──────────────────┬──────────────────┬────────────────┤
│     Agents       │    MCP Tools     │      LLMs        │   Inference    │
│   (A2A/JSON-RPC) │   (SSE/HTTP)     │  (REST/Stream)   │  (Ollama/vLLM) │
└──────────────────┴──────────────────┴──────────────────┴────────────────┘
```

### Cluster-Specific Context

This documentation is tailored for the Home-Ops cluster which uses:

- **Flux CD** for GitOps deployment
- **External Secrets Operator** with 1Password as the secret store
- **Envoy Gateway** for general ingress (separate from AgentGateway)
- **LiteLLM** as a unified LLM proxy in the `ai` namespace
- **Cilium** for CNI with `lbipam.cilium.io` for LoadBalancer IPs

---

## Documentation Index

### Getting Started
| Document | Description |
|----------|-------------|
| [01-quickstart.md](./01-quickstart.md) | Get started with AgentGateway in 5 minutes |
| [02-installation.md](./02-installation.md) | Detailed installation with Flux CD |
| [03-gateway-setup.md](./03-gateway-setup.md) | Gateway resource configuration |

### Core Features
| Document | Description |
|----------|-------------|
| [04-llm-providers.md](./04-llm-providers.md) | Configure OpenAI, Anthropic, Gemini, Bedrock |
| [05-mcp-connectivity.md](./05-mcp-connectivity.md) | MCP server integration |
| [06-agent-connectivity.md](./06-agent-connectivity.md) | A2A agent communication |
| [13-function-calling.md](./13-function-calling.md) | LLM tool invocation through MCP |
| [14-session-management.md](./14-session-management.md) | Stateful connections and reconnection |

### Operations
| Document | Description |
|----------|-------------|
| [07-security.md](./07-security.md) | RBAC, prompt guards, tool poisoning protection |
| [08-observability.md](./08-observability.md) | Metrics, logs, and traces |
| [09-advanced-features.md](./09-advanced-features.md) | Failover, rate limiting, streaming |
| [15-optimization.md](./15-optimization.md) | Cost, performance, and tuning |

### Reference
| Document | Description |
|----------|-------------|
| [10-api-reference.md](./10-api-reference.md) | Complete CRD and API reference |
| [11-cluster-deployment.md](./11-cluster-deployment.md) | Home-Ops specific deployment manifests |
| [12-troubleshooting.md](./12-troubleshooting.md) | Common issues and solutions |
| [GLOSSARY.md](./GLOSSARY.md) | Terminology and concept definitions |
| [MIGRATION.md](./MIGRATION.md) | Version upgrades and protocol migrations |

---

## Quick Decision Guides

### Which LLM Provider Should I Use?

```
Start
  │
  ├─ Need highest quality reasoning? ──────── Yes ──▶ Claude Opus / GPT-4
  │
  ├─ Complex coding or analysis? ──────────── Yes ──▶ Claude Sonnet / GPT-4o
  │
  ├─ Working with images/multimodal? ──────── Yes ──▶ GPT-4o / Gemini Pro
  │
  ├─ Cost is the primary concern? ─────────── Yes ──▶ Gemini Flash / GPT-4o-mini
  │
  ├─ Data must stay on-premises? ──────────── Yes ──▶ Ollama (Llama 3.2)
  │
  ├─ Need AWS integration? ────────────────── Yes ──▶ Bedrock
  │
  └─ General purpose / balanced? ─────────────────▶ Claude Haiku / GPT-4o-mini
```

### How Many Gateway Replicas?

| Concurrent Users | Replicas | HPA |
|------------------|----------|-----|
| < 10 | 1-2 | No |
| 10-50 | 2-3 | Optional |
| 50-200 | 3-5 | Recommended |
| 200-1000 | 5-10 | Required |
| > 1000 | 10+ | Required (custom metrics) |
| Production (any size) | ≥ 3 | Recommended for HA |

### Which Timeout Values?

| Request Type | Timeout | Why |
|--------------|---------|-----|
| Simple chat completion | 30s | Fast responses |
| Complex reasoning | 60s | More thinking time |
| Code generation | 90s | Larger outputs |
| Document analysis | 120s | Long context processing |
| Streaming responses | 300s | Continuous connection |
| MCP tool execution | 30-60s | Varies by tool |
| Health checks | 5s | Quick failure detection |

---

## Prerequisites

- **Kubernetes cluster** (v1.25+)
- **kubectl** (within one minor version of cluster)
- **Helm** (v3.x) or **Flux CD** for GitOps
- **API keys** for LLM providers you want to use
- **External Secrets Operator** configured with 1Password

---

## Quick Links

| Resource | URL |
|----------|-----|
| **kgateway Documentation** | https://kgateway.dev/docs/agentgateway/latest/ |
| **AgentGateway Standalone** | https://agentgateway.dev/ |
| **GitHub - kgateway** | https://github.com/kgateway-dev/kgateway |
| **CNCF Project** | Sandbox Project |

---

## Related Documentation

- [kgateway Documentation](../kgateway/README.md) - Envoy-based gateway for non-AI traffic
- [kagent Documentation](../kagent/README.md) - Kubernetes-native AI agent framework
- [kmcp Documentation](../kmcp/README.md) - MCP server development toolkit

---

## Version

This documentation covers **kgateway v2.1.2** with AgentGateway enabled.

---

*Last updated: 2026-01-25*
