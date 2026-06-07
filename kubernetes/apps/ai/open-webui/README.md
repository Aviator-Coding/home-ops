# Open-WebUI

Chat UI at `https://chat.${SECRET_DOMAIN}` (OIDC via Authentik).

## Model connections (manual UI step)

Open-WebUI stores API connections in its database (PersistentConfig), so they
are **not** managed by GitOps. Configure them once in
**Admin Settings → Connections → OpenAI API**.

All LLM traffic goes through **agentgateway** (the single model gateway).
agentgateway has no aggregated `/v1/models`, so add **one connection per
provider path** — each connection contributes its own model list:

| Provider   | API Base URL                                                          | API Key       |
| ---------- | --------------------------------------------------------------------- | ------------- |
| OpenAI     | `http://internal-noauth.ai-system.svc.cluster.local/openai/v1`        | any non-empty |
| Anthropic  | `http://internal-noauth.ai-system.svc.cluster.local/anthropic/v1`     | any non-empty |
| Groq       | `http://internal-noauth.ai-system.svc.cluster.local/groq/v1`          | any non-empty |
| Gemini     | `http://internal-noauth.ai-system.svc.cluster.local/gemini/v1`        | any non-empty |
| DeepSeek   | `http://internal-noauth.ai-system.svc.cluster.local/deepseek/v1`      | any non-empty |
| OpenRouter | `http://internal-noauth.ai-system.svc.cluster.local/openrouter/v1`    | any non-empty |

Notes:

- The `internal-noauth` gateway **injects the real provider API key**
  (per-provider `AgentgatewayBackend` + ExternalSecret from the 1Password
  `ai-keys` item). The key entered in Open-WebUI is a placeholder — it just
  has to be non-empty.
- Provider passthrough model lists are unfiltered; disable unwanted models
  under **Admin Settings → Models**.
- After the agentgateway namespace move (`ai-system` → `ai`), update the URLs
  to `internal-noauth.ai.svc.cluster.local`.
- Backend definitions live in
  `kubernetes/apps/ai-system/agentgateway/app/backends/` — adding a Backend
  there (GitOps) and a matching connection here exposes a new provider.
