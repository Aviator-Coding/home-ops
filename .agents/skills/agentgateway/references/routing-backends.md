# Routing and backends

The LLM entry point is `app/httproute-unified.yaml`, not the Gateway
manifests. One OpenAI-style `/v1` on all three Gateways. The
`model-routing` policy extracts `model` into `x-model` at PreRouting. Rules
match `x-model`. Model ids pass through unchanged, and `rules/cost.yaml`
keys on exactly what the client sent. There is no second copy of the
model-to-backend table.

## Rule order

Regexes are mutually exclusive, except groq. Groq's family regex also
matches OpenRouter `moonshotai/...` slugs. Gateway API breaks a tie toward
the earlier rule, so the groq rule stays **above** the vendor-slug
catch-all. Moving it "next to the other aggregators" sends moonshot
traffic to Groq.

Aggregator base paths are URLRewrites on the rule, not a client prefix:

| Upstream | Rewrite |
|---|---|
| OpenRouter | `/api` |
| OpenCode Go | `/zen/go` |
| Groq | `/openai` (their API is `api.groq.com/openai/v1`) |

## Adding a model

1. If the id's family has no rule, add one to `httproute-unified.yaml`.
2. Add the price row in `rules/cost.yaml`. Metering is wrong, not absent,
   when the row is missing: the request still routes.
3. Add the catalog entry in `httproute-models.yaml` (`GET /v1/models`).
   Clients that pick from the catalog will not see an id that only exists
   as a route.

Dormant backends (`zai`, `togetherai`, `opencodeai`) are defined under
`app/backends/` and have no rule. Re-enabling one is step 1 only, plus
the price and catalog rows if a model will actually be called.

Provider credentials are 1Password item `ai-keys`. The Kubernetes secret
**key must be `Authorization`**. Consumer bearer keys are item
`ai-gateway-keys`. Never put either value in git.

## Failover

`app/backends/vllm.yaml` defines `llm-chat-failover`: local llama.cpp,
then `kimi-k2.6` on OpenCode Go. Two requirements, both easy to drop
while "cleaning up" the backend:

- `AgentgatewayPolicy/llm-chat-failover-health` targets that backend.
  `unhealthyCondition: "response.code >= 500 || response.code == 429"`,
  eviction `duration: 60s`, `consecutiveFailures: 2`. Connection failures
  count. A 429 `Retry-After` overrides the eviction duration. Without the
  policy, group 2 is never tried. Verified live before the policy existed.
- Each member's `pathPrefix` replaces the default `/v1`. OpenCode Go's
  must be `/zen/go/v1`. `/zen/go` returns the website.

Hermes' own `fallback_providers` runs only after this gateway failover
fails. Skill `hermes-agent`.

## Local backends

`vllm` is the B70 chat server (llama.cpp, keeping the Service name).
`embedding-local` is `ai/embedding-gpu`. `vllm-embed` stays `replicas: 0`
and is not the live embedder. Do not cite ComfyUI or agentmemory as live
consumers of either backend. Skill `b70-llm-serving` and skill `ai-stack`.
