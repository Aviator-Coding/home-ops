# Hermes LLM routing

Read the live model ids from `app/resources/config.yaml`. This page is the
invariants. The ids move; the shape does not.

## Providers

`providers.gateway` (`custom:gateway`) is the default LLM provider. Its base
URL is agentgateway's keyless ClusterIP,
`http://internal-noauth.ai.svc.cluster.local/v1`. The gateway routes by
model id and injects the real provider keys. `api_key: no-auth` is a
sentinel so Hermes does not log "no resolvable api_key". It is never sent
on the wire. Do not use that sentinel on a provider that must authenticate.

`providers.litellm` (`custom:litellm`) is a second named custom provider for
selectable chat models. Its `key_env` is `LITELLM_PROJECTS_API_KEY` from the
hermes Secret (1Password `litellm-consumer-litellm-consume-projects`). That
name is not a built-in provider credential, which Hermes scrubs. Do not move
`model.default`, `fallback_providers`, or `auxiliary` onto it. The key's
$100/30d budget is one pool shared with OpenCode.

Never set `OPENAI_BASE_URL`. It overrides every provider. Per-provider
`api` in the `providers:` dict is the layer that matters.

Never rename `gateway` or `litellm` to a built-in provider id. Hermes'
auxiliary-client resolver then uses the native integration and silently
skips that entry's base URL.

The default model is the local B70 chat id (`qwen3.6-35b-a3b` at last
read). Fan-out above 4 concurrent calls queues on that server's unified KV.
Skill `b70-llm-serving`.

## Fallback shape

The gateway's own `llm-chat-failover` backend fails the local model over
to `kimi-k2.6` before Hermes' `fallback_providers` runs. Hermes' chain is
the next two tiers, both still `custom:gateway`:

1. `glm-5.1` (OpenCode Go, the tool-calling tier)
2. `deepseek/deepseek-v4-flash` (off the Go subscription, so Go-quota
   exhaustion still has a last resort)

Effective order: local, then kimi (gateway), then glm-5.1, then
deepseek-v4-flash. Keep that chain on `custom:gateway`. An explicit
`custom:litellm` selection is metered by LiteLLM, not by agentgateway.

## Auxiliary tasks

`web_extract`, `session_search`, and `title_generator` run on a cloud model,
not the local one. `web_extract` fires one call per page; that fan-out
exceeds the 4-slot local server.

Auxiliary fallback is the per-task `fallback_chain`, not the global
`fallback_providers`. Without the per-task chain, Hermes falls back to the
main model and re-queues on the live session.

`compression` is the exception: its **primary** is a cloud model
(`openai/gpt-oss-120b` at last read), not kimi. On a provider error the
compressor falls back to the main model, not to that task's
`fallback_chain`. A kimi primary would dump a quota failure onto the local
server that is already serving the session.

`vision` stays on a vision-capable cloud model. The local 35B is text-only.

## Memory and MCP

`memory.provider: holographic` activates Hermes' bundled local SQLite fact
store (in-process only, no shared service). Without the key Hermes shows
`provider: none` and captures nothing. The shared agentmemory service is
retired (skill `ai-stack`, references/retirement.md).

MCP is one endpoint: the ToolHive VirtualMCPServer `mcp-gateway-internal` at
`/mcp`, reached via the LAN route with a bearer token (`MCP_GATEWAY_URL`,
`MCP_GATEWAY_TOKEN` in the hermes Secret); a NetworkPolicy closes the vmcp
Service to everything but envoy-internal. Tools are prefixed `{workload}_`.
LiteLLM is not an MCP gateway here. The `${VAR}` refs only expand through a
`secrets:` source under multiplexing: [mcp-gateway.md](mcp-gateway.md).

## Web search

`web.search_backend: searxng`, URL from `SEARXNG_URL`. SearXNG itself is
skill `ai-stack`.
