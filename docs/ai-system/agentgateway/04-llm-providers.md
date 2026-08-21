# LLM providers (unified `/v1`)

Provider configuration is the YAML under [`app/backends/`](../../../kubernetes/apps/ai/agentgateway/app/backends/). Routing is **not** per-provider URL prefixes. It is model-name routing on a single OpenAI-style `/v1`.

Read these two files before adding or changing a provider:

1. [`httproute-unified.yaml`](../../../kubernetes/apps/ai/agentgateway/app/httproute-unified.yaml) - header comments are the routing design
2. [`policies/model-routing-policy.yaml`](../../../kubernetes/apps/ai/agentgateway/app/policies/model-routing-policy.yaml) - copies JSON `model` to `x-model` before route selection

## How a request is routed

1. Client POSTs `/v1/chat/completions` with `{"model":"<id>", ...}` to `internal-noauth`, `internal`, or `public`.
2. `AgentgatewayPolicy/model-routing` (phase PreRouting, only for `/v1/chat/completions` and `/v1/embeddings`) sets header `x-model`.
3. `HTTPRoute/llm-unified` matches `x-model` and `backendRefs` an `AgentgatewayBackend`.
4. Model ids pass through **unchanged** (cost metering keys on the same string).

Native Anthropic SDK clients use `/v1/messages` (no `x-model` match); that rule always goes to the `anthropic` backend.

`GET /v1/models` is a static catalog (`httproute-models.yaml` + `AgentgatewayPolicy/models-catalog`), not an aggregation across backends.

## Live backends

| Backend | Provider shape | Selected by (chat) | Notes |
|---------|----------------|--------------------|-------|
| `openai` | native `openai: {}` | `gpt-*`, `o1/o3/o4`, `chatgpt*`, unmatched default | |
| `anthropic` | native `anthropic: {}` | `claude-*`; also `/v1/messages` | Completions translates OpenAI->Messages |
| `gemini` | native `gemini: {}` | `gemini-*` | |
| `xai` | OpenAI-compat `api.x.ai` | `grok-*` | `policies.tls: {}` |
| `deepseek` | OpenAI-compat `api.deepseek.com` | `deepseek-chat`, `deepseek-reasoner` | bare `deepseek-v4-*` is Go; `deepseek/...` is OpenRouter |
| `mistral` | OpenAI-compat `api.mistral.ai` | `mistral-*`, magistral, ... | |
| `groq` | OpenAI-compat `api.groq.com` | llama/mixtral/gemma/moonshotai/... | unified rule prepends `/openai`; **must stay above** vendor-slug catch-all |
| `perplexity` | OpenAI-compat `api.perplexity.ai` | `sonar*` | `provider.path: /chat/completions` (no rewrite) |
| `openrouter` | OpenAI-compat `openrouter.ai` | vendor slugs `vendor/model` | unified rule prepends `/api` |
| `opencodego` | OpenAI-compat `opencode.ai` | kimi-, glm-, minimax-, mimo-, hy3-, deepseek-v4-, qwen3.x-max/plus | rewrite to `/zen/go` |
| `vllm` | in-cluster `vllm-app.ai:8000` | (models catalog fallback) | no TLS, no auth |
| `vllm-embed` | in-cluster `vllm-embed.ai:8000` | default `/v1/embeddings` | scaled to 0; unused ids 503 |
| `llm-chat-failover` | groups: local qwen then OpenCode Go kimi-k2.6 | exact `qwen3.6-35b-a3b` | see `09-advanced-features.md` |

Dormant (backend + secret present, **no** unified-route rule): `zai`, `togetherai`, `opencodeai`. Re-enable with one rule in `httproute-unified.yaml`.

Not in this cluster: Bedrock, Azure OpenAI, Vertex, Ollama-as-backend. Local inference is vLLM/llama.cpp via `vllm` / `llm-chat-failover`.

## Secrets

Each backend file pairs an ExternalSecret with the `AgentgatewayBackend`. Pattern:

- ClusterSecretStore `onepassword`
- item `ai-keys`
- template key **`Authorization`** (required by the dataplane even when the upstream header is different; see testing-report item 6)

Custom-host OpenAI-compat backends need `policies.tls: {}` or they send plain HTTP to :443.

## LiteLLM

Not deployed. Grafana and Hermes comments say it was removed. Unified `/v1` is AgentGateway itself. Do not create a LiteLLM `AgentgatewayBackend` or tell clients to use `http://litellm.ai.svc:4000/v1`.
