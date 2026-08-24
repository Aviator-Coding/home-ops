# Agent / in-cluster clients

**kagent is not deployed.** There is no `kagent/` app under `kubernetes/apps/base/ai/`, and no kagent `Agent` / `ModelConfig` CRs. Do not set `baseUrl: http://litellm.ai.svc.cluster.local:4000/v1` - LiteLLM is gone.

## How workloads should call LLMs

Use the keyless in-cluster OpenAI endpoint:

```
http://internal-noauth.ai.svc.cluster.local/v1
```

Chat: `POST /v1/chat/completions` with a `model` id the unified router knows. Embeddings: `POST /v1/embeddings`. Catalog: `GET /v1/models`.

Hermes, Open WebUI, agentmemory, and similar apps in `ai` should target that base URL (or `llm-api.${SECRET_DOMAIN}` with a Bearer key if they sit outside the cluster). They must **not** use `/openai`, `/openrouter`, or other retired prefixes.

## A2A

No AgentGateway A2A Backends, no `appProtocol: kgateway.dev/a2a`. Agent-to-agent protocol through this gateway is not wired.

## kmcp

Not deployed. MCP server lifecycle is ToolHive (`toolhive.stacklok.dev/v1alpha1` `MCPServer`; see `05-mcp-connectivity.md`). kagent/kmcp tombstones: [kagent](../kagent/README.md), [kmcp](../kmcp/README.md).
