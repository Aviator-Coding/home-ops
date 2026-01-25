# Kagent Documentation

> **Kagent** - An open-source AI agent platform designed specifically for Kubernetes environments. Created by Solo.io in 2025, kagent is a Cloud Native Computing Foundation (CNCF) sandbox project.

## Overview

Kagent is a programming framework that brings agentic AI capabilities to cloud-native environments. Unlike traditional chatbots that provide passive responses, kagent transforms AI insights into concrete actions through advanced reasoning and iterative planning capabilities.

**Key Differentiators:**
- **Declarative Design**: Define agents, tools, and instructions; kagent handles the rest
- **Kubernetes-Native**: Purpose-built for Kubernetes with seamless cluster integration
- **Abstraction Layer**: Handles agent-building complexity so you focus on business logic

## Documentation Structure

| Document | Description |
|----------|-------------|
| [Getting Started](./01-getting-started.md) | Installation, prerequisites, and quickstart guide |
| [Core Concepts](./02-core-concepts.md) | Agents, tools, architecture fundamentals |
| [Agent Configuration](./03-agent-configuration.md) | Creating and configuring AI agents |
| [Tools & MCP](./04-tools-and-mcp.md) | Built-in tools, MCP servers, custom tool creation |
| [LLM Providers](./05-llm-providers.md) | OpenAI, Anthropic, Azure, Ollama, and more |
| [Agent-to-Agent (A2A)](./06-a2a-communication.md) | Inter-agent communication protocol |
| [Integrations](./07-integrations.md) | Slack, Discord, and external system integration |
| [CLI Reference](./08-cli-reference.md) | Complete command-line interface guide |
| [Pre-built Agents](./09-prebuilt-agents.md) | Ready-to-use agent catalog |
| [Examples](./10-examples.md) | Practical implementation examples |

## Quick Links

- **GitHub**: [github.com/kagent-dev/kagent](https://github.com/kagent-dev/kagent)
- **Website**: [kagent.dev](https://kagent.dev)
- **Discord**: [discord.gg/Fu3k65f2k3](https://discord.gg/Fu3k65f2k3)
- **Roadmap**: [GitHub Project Board](https://github.com/orgs/kagent-dev/projects/3)

## Key Features

| Feature | Description |
|---------|-------------|
| **AI-Powered Automation** | Intelligent agents for sophisticated Kubernetes tasks |
| **Multi-Provider Support** | OpenAI, Anthropic, Google, Azure, Ollama, Bedrock |
| **Tool Integration** | MCP tools, Kubernetes built-in tools, HTTP tools |
| **A2A Communication** | Agent-to-agent interactions for complex workflows |
| **Observability** | Built-in tracing and monitoring capabilities |
| **Cloud Native** | Kubernetes CRDs, Helm charts, native deployment |

## Use Cases

Kagent addresses operational complexity by automating:

- **Connectivity Diagnostics**: Troubleshoot service mesh issues
- **Performance Troubleshooting**: Debug application performance problems
- **Alert Generation**: Create alerts from Prometheus metrics
- **Gateway Configuration**: Debug Gateway and HTTPRoute configs
- **Progressive Rollouts**: Manage Argo Rollouts deployments
- **Documentation Search**: Query internal documentation via AI

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        KAGENT PLATFORM                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────┐    ┌───────────┐    ┌───────────┐               │
│  │    CLI    │    │ Dashboard │    │   A2A     │               │
│  │           │    │   (UI)    │    │ Endpoint  │               │
│  └─────┬─────┘    └─────┬─────┘    └─────┬─────┘               │
│        │                │                │                      │
│        └────────────────┼────────────────┘                      │
│                         │                                       │
│                         ▼                                       │
│              ┌─────────────────────┐                            │
│              │    App/Engine       │                            │
│              │  (Python + ADK)     │                            │
│              └──────────┬──────────┘                            │
│                         │                                       │
│         ┌───────────────┼───────────────┐                       │
│         │               │               │                       │
│         ▼               ▼               ▼                       │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐                │
│  │   Agents   │  │   Tools    │  │    MCP     │                │
│  │   (CRDs)   │  │ (Built-in) │  │  Servers   │                │
│  └────────────┘  └────────────┘  └────────────┘                │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│                    Kubernetes Controller (Go)                   │
│                    Manages CRDs & Agent Lifecycle               │
└─────────────────────────────────────────────────────────────────┘
```

## Minimum Requirements

- Kubernetes cluster (kind, minikube, or production)
- kubectl configured
- Helm 3.x
- API key for your chosen LLM provider

## Quick Install

```bash
# Set your API key
export OPENAI_API_KEY="your-api-key-here"

# Install kagent CLI
brew install kagent
# OR
curl https://raw.githubusercontent.com/kagent-dev/kagent/refs/heads/main/scripts/get-kagent | bash

# Deploy to cluster
kagent install --profile demo

# Open dashboard
kagent dashboard
```

The dashboard opens at `http://localhost:8082`.

## Version Information

- **Current Version**: Check with `kagent version`
- **kmcp Integration**: Included by default as of v0.7
- **CNCF Status**: Sandbox project

---

*For detailed documentation, proceed to [Getting Started](./01-getting-started.md).*
