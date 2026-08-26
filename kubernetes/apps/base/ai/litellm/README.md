# LiteLLM (governance-only)

[BerriAI/litellm](https://github.com/BerriAI/litellm) proxy, redeployed 2026-08-26
per captain decisions B4/D4 as a narrow **governance layer**, not a return to its
pre-2026-06-07 role as this cluster's unified LLM proxy (removed in #941 - see
`docs/ai-system/agentgateway/GLOSSARY.md`). Full rationale, the six-line lab
design, and the knobs to raise budgets later: `docs/ai-system/litellm/README.md`.

**Scope, non-negotiable per B4:**
- Does **not** front the public listener, or any Gateway API listener at all -
  no `HTTPRoute`, no `AgentgatewayBackend`. In-cluster consumers only, via
  `http://litellm.ai.svc.cluster.local:4000`.
- Zero changes to `agentgateway/` (the existing envoy AI gateway) or to
  `vllm/` (`vllm-app.ai.svc.cluster.local:8000`, which this app points at as
  a backend and never modifies).
- Model list is exactly the lab-proven six lines in
  [`app/resources/config.yaml`](app/resources/config.yaml) plus the
  Prometheus metrics callback - nothing else.

**Per-consumer governance (D4):** virtual keys with a model allow-list and a
deliberately tiny spend/rate budget, defined in
[`app/resources/consumers.json`](app/resources/consumers.json) and minted by
the `provision-keys` Helm hook Job
([`app/resources/provision_keys.py`](app/resources/provision_keys.py)) against
the proxy's `/key` API - LiteLLM has no way to declare a budgeted key from
config.yaml alone, which is why this app carries a Postgres dependency (see
`docs/ai-system/litellm/README.md#why-postgres`) unlike the rest of this
namespace's stateless-by-preference apps.

## Prerequisites (before first sync)

1Password item **`litellm`** (`onepassword` ClusterSecretStore vault):

| Field | How to generate |
| --- | --- |
| `LITELLM_MASTER_KEY` | `echo sk-$(openssl rand -hex 32)` |
| `LITELLM_SALT_KEY` | `echo sk-$(openssl rand -hex 32)` |
| `POSTGRES_DB_NAME` | `litellm` |
| `POSTGRES_DB_USER_NAME` | `litellm` |
| `POSTGRES_DB_USER_PASSWORD` | `openssl rand -hex 24` |

The shared `cloudnative-pg` 1Password item (`POSTGRES_SUPER_PASS`) already
exists - every `postgres-17` client app reads it the same way (see
`kubernetes/apps/base/database/cloudnative-pg/Readme.md`).

## Verifying end to end

`docs/ai-system/litellm/README.md#verification-runbook` has the exact
`kubectl port-forward` + `curl` commands to mint-and-call the `demo` consumer
key against `vllm-app` through this proxy, and to confirm the allow-list and
budget are actually enforced (a second model returns 403, a request past
budget returns 429).
