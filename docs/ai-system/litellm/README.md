# LiteLLM (governance layer)

LiteLLM runs beside agentgateway as a narrow governance layer: per-consumer
virtual keys with allow-lists and budgets, fallback chains, a complexity-tier
auto-router, and a Claude Code subscription pass-through. Internal route only,
never the public listener, and never agentgateway's `/v1`.

- Agent knowledge (tripwires, procedures, evidence by PR): skill
  `litellm-proxy` ([`.agents/skills/litellm-proxy/SKILL.md`](../../../.agents/skills/litellm-proxy/SKILL.md)).
- Manifests, prerequisites and the Jev grant runbook:
  [`kubernetes/apps/base/ai/litellm/README.md`](../../../kubernetes/apps/base/ai/litellm/README.md).
- Human runbooks here: [`claude-code-subscription.md`](claude-code-subscription.md)
  (Claude Code client setup) and [`request-logs.md`](request-logs.md)
  (reading a request's prompt, response and cost).
