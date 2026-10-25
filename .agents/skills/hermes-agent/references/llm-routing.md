# Hermes LLM routing

Read the live model ids from `app/resources/config.yaml`. This page is the
invariants. The ids move; the shape does not.

## Providers

`providers.litellm` (`custom:litellm`) is the only LLM provider. Its base URL
is `http://litellm.ai.svc.cluster.local:4000/v1` and its `key_env` is
`LITELLM_HERMES_API_KEY` from the hermes Secret (1Password
`litellm-consumer-hermes`, written by the `hermes` LiteLLMVirtualKey
PushSecret). The key is Hermes' alone, so LiteLLM spend and traffic are
attributable to it ($50/30d covers the metered routes; `chat-local` and the
`go-*` models are $0). The env name is not a built-in provider credential,
which Hermes scrubs. A dummy (`no-auth`, empty) is not sent and LiteLLM
answers 401. Skill `litellm-proxy` owns the key and its allow-list: a model
used in `config.yaml` that is not on the `hermes` key is a 403.

`session_affinity_header: x-opencode-session` is load-bearing for the `go-*`
models. OpenCode Go answers HTTP 400 `MissingSessionID` to any request
without it and wants one stable value per conversation. Hermes sends a
conversation-stable id on main and auxiliary calls; LiteLLM forwards it
upstream for the `go-*` model groups only
(`litellmSettings.model_group_settings`). Other models ignore it. The
commit-watcher script sends its own per run. A stateless call with no
conversation id gets no header and Go would answer 400.

Never set `OPENAI_BASE_URL`. It overrides every provider. Per-provider
`api` in the `providers:` dict is the layer that matters.

Never rename `litellm` to a built-in provider id. Hermes' auxiliary-client
resolver then uses the native integration and silently skips that entry's
base URL.

The default model is the local chat role alias `chat-local`, never a model
name. LiteLLM maps it to the loaded model, whose real name is `CHAT_MODEL_ID`
in ConfigMap `chat-model`, so a model swap does not touch Hermes. Fan-out
above 4 concurrent calls queues on that server's unified KV. Skill
`b70-llm-serving`.

## Fallback shape

`chat-local` is a terminal alias in LiteLLM (no cloud fallback, by design),
so the whole failover is Hermes' `fallback_providers`, all `custom:litellm`:

1. `go-kimi-k2.6` (OpenCode Go)
2. `go-glm-5.1` (OpenCode Go, the tool-calling tier)
3. `claude-haiku-5-5` (metered, off the Go subscription, so Go-quota
   exhaustion still has a last resort; spends the hermes key's $50/30d)

Effective order: local, kimi, glm-5.1, haiku.

## Auxiliary tasks

`web_extract`, `session_search`, and `title_generator` run on a cloud model,
not the local one. `web_extract` fires one call per page; that fan-out
exceeds the 4-slot local server.

Auxiliary fallback is the per-task `fallback_chain`, not the global
`fallback_providers`. Without the per-task chain, Hermes falls back to the
main model and re-queues on the live session.

`compression` is the exception: its **primary** is an off-Go metered model
(`gpt-oss-20b` at last read), not a `go-*` model. On a provider error the
compressor falls back to the main model, not to that task's
`fallback_chain`. A Go primary would dump a quota failure onto the local
server that is already serving the session.

`vision` is `claude-haiku-5-5`, a vision-capable cloud model. The local 35B
is text-only.

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
