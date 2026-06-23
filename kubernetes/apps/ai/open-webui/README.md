# Open-WebUI

Chat UI at `https://chat.${SECRET_DOMAIN}` (OIDC via Authentik).

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

agentgateway does **not** aggregate `/v1/models` across providers, so the model
dropdown will not auto-populate. Add the model ids you want under the connection
(or **Admin Settings → Models**), for example:

`qwen3.6-35b-a3b` (local), `gpt-4o`, `gpt-5`, `claude-sonnet-4-6`,
`claude-opus-4-6`, `gemini-2.5-flash`, `grok-4`, `deepseek-chat`,
`llama-3.3-70b-versatile`.

Notes:

- The `internal-noauth` gateway **injects the real provider API key**
  (per-provider `AgentgatewayBackend` + ExternalSecret from the 1Password
  `ai-keys` item). The key entered in Open-WebUI is a placeholder — it just
  has to be non-empty.
- agentgateway lives in the `ai` namespace (consolidated from `ai-system`).
- Model→provider routing rules live in
  `kubernetes/apps/ai/agentgateway/app/httproute-unified.yaml`; backend
  definitions in `kubernetes/apps/ai/agentgateway/app/backends/`. Adding a model
  family there (GitOps) exposes it on the same unified connection.
- Aggregators with colliding model names (OpenRouter, Together AI, Z.AI,
  OpenCode, Perplexity) keep their own `/<provider>` paths — point a separate
  connection at e.g. `http://internal-noauth.ai.svc.cluster.local/openrouter/v1`
  if you need them.
