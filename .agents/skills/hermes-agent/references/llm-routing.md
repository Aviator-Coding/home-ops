# Hermes LLM routing

Read the live model ids from `app/resources/config.yaml`. This page is the
invariants. The ids move; the shape does not.

## One provider

`providers.gateway` (`custom:gateway`) is the only LLM provider. Its base
URL is agentgateway's keyless ClusterIP,
`http://internal-noauth.ai.svc.cluster.local/v1`. The gateway routes by
model id and injects the real provider keys. `api_key: no-auth` is a
sentinel so Hermes does not log "no resolvable api_key". It is never sent
on the wire.

Never set `OPENAI_BASE_URL`. It overrides every provider. Per-provider
`api` in the `providers:` dict is the layer that matters.

Never rename `gateway` to a built-in provider id. Hermes' auxiliary-client
resolver then uses the native integration and silently skips this base URL.

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
deepseek-v4-flash. Do not point Hermes at a provider directly. Cost
metering and Tempo only see traffic that enters the unified `/v1`.

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

## Web search

`web.search_backend: searxng`, URL from `SEARXNG_URL`. SearXNG itself is
skill `ai-stack`.
