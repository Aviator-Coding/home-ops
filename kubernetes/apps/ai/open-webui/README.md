# Open-WebUI

Chat UI at `https://chat.${SECRET_DOMAIN}` (OIDC via Authentik). The HTTPRoute
also publishes `https://open-webui.${SECRET_DOMAIN}` (`{{ .Release.Name }}`).

## Model connections (manual UI step)

Open-WebUI stores API connections in its database (PersistentConfig), so they
are **not** managed by GitOps. Configure them once in
**Admin Settings → Connections → OpenAI API**.

All LLM traffic goes through **agentgateway** (the single model gateway). Use
**one** connection pointed at the unified endpoint — it routes to every provider
by the request `model` field:

| API Base URL                                       | API Key       |
| -------------------------------------------------- | ------------- |
| `http://internal-noauth.ai.svc.cluster.local/v1`   | any non-empty |

`GET /v1/models` serves the gateway's static model catalog (the
`models-catalog` policy in `httproute-models.yaml`), so the model dropdown
auto-populates with every routable id — local, first-party (`gpt-*`,
`claude-*`, `gemini-*`, `grok-*`, …), OpenCode Go bare ids (`kimi-k2.6`,
`glm-5.1`, …) and OpenRouter vendor slugs (`x-ai/grok-4.3`, …).

Notes:

- The `internal-noauth` gateway **injects the real provider API key**
  (per-provider `AgentgatewayBackend` + ExternalSecret from the 1Password
  `ai-keys` item). The key entered in Open-WebUI is a placeholder — it just
  has to be non-empty.
- agentgateway lives in the `ai` namespace (consolidated from `ai-system`).
- Model→provider routing rules live in
  `kubernetes/apps/ai/agentgateway/app/httproute-unified.yaml`; backend
  definitions in `kubernetes/apps/ai/agentgateway/app/backends/`. Adding a model
  there (GitOps) = a routing rule + a price row in `rules/cost.yaml` + a catalog
  entry in `httproute-models.yaml`, all exposed on this same unified connection.
- There are **no per-provider paths anymore** — the unified `/v1` is the single
  entry point on every listener; aggregator models are addressed purely by
  model id (vendor slugs → OpenRouter, bare Go ids → OpenCode Go).
