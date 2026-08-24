# Advanced features (what is actually configured)

## Failover

Live failover is `spec.ai.groups` on `AgentgatewayBackend`, not `priorityGroups.providers` on a kgateway `Backend`.

[`backends/vllm.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/backends/vllm.yaml) defines `llm-chat-failover`:

- Group 1: local llama.cpp `qwen3.6-35b-a3b` at `vllm-app.ai.svc.cluster.local:8000`
- Group 2: OpenCode Go `kimi-k2.6` at `opencode.ai` with `pathPrefix: /zen/go/v1` (must include `/v1` or requests hit the marketing site)

The unified router sends **exact** model id `qwen3.6-35b-a3b` to this backend, so every client of the local model gets cloud failover. Embeddings are excluded (dimension mismatch would corrupt agentmemory).

Health policy `llm-chat-failover-health` (same file) is required: without it, providers are never evicted and group 2 is never tried. `unhealthyCondition: "response.code >= 500 || response.code == 429"`, eviction 60s after 2 consecutive failures. Connection failures count; a 429 Retry-After overrides duration.

## Embeddings

Unified `/v1/embeddings` rules (see `httproute-unified.yaml`):

- vendor-slug ids -> OpenRouter (`/api/v1/embeddings`)
- `text-embedding-*` -> OpenAI
- default -> `vllm-embed` (scaled to 0; unused ids 503)

## Dormant backends

`zai`, `togetherai`, `opencodeai` stay defined so credentials stay wired. They have **no** unified-route rule. Re-enable with one rule. OpenCode Zen ids can collide with direct-provider ids (`claude-*`, `gemini-*`); see the pricing note in `rules/cost.yaml`.

## Rate limiting / retries / prompt guards

No live `AgentgatewayPolicy` for those. Do not copy old `TrafficPolicy` snippets from kgateway docs into this namespace without a new design.

## Streaming

Chat completions stream. Tracing policy deliberately skips `response.body` so the dataplane does not buffer the stream.
