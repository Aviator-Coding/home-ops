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
- Direct local model is still the lab-proven six-line `qwen3.6-35b-a3b`
  entry in [`app/resources/config.yaml`](app/resources/config.yaml). D3
  adds the additive `auto` router alias (classifier + Anthropic tier
  backends) on top - see `docs/ai-system/litellm/auto-router.md`. Local
  entries keep `model_info` governance-accounting prices; cloud tiers use
  LiteLLM's built-in cost map. Prometheus metrics callback is on.

**Per-consumer governance (D4):** virtual keys with a model allow-list and a
deliberately tiny spend/rate budget, defined in
[`app/resources/consumers.json`](app/resources/consumers.json) and minted by
`provision_keys.py` (Helm install hook + 15m CronJob - see helmrelease.yaml)
against the proxy's `/key` API - LiteLLM has no way to declare a budgeted key from
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
`kubernetes/apps/base/database/cloudnative-pg/Readme.md`). So does the
`ai-keys` item, which this app's `ExternalSecret` extracts `ANTHROPIC_API_KEY`
from for the auto-router's cloud tier - the same item every
`agentgateway/app/backends/*.yaml` already reads. **No new 1Password item is
needed for the router**; `litellm` above remains the only one to create.

> Until the `litellm` item exists, this app's `ExternalSecret` reports
> `SecretSyncedError` / `key not found in 1Password Vaults: litellm` and the
> pod stays in `Init:CreateContainerConfigError`. That is this prerequisite
> being unmet, not a manifest bug.

## Verifying end to end

`docs/ai-system/litellm/README.md#verification-runbook` has the exact
`kubectl port-forward` + `curl` commands to mint-and-call the `demo` consumer
key against `vllm-app` through this proxy, and to confirm the allow-list and
budget are actually enforced (a second model is rejected; a request past
budget fails once spend is exhausted). Spend budgets require the non-zero
`model_info` per-token prices on the model in `app/resources/config.yaml` -
see that file's comment.

The complexity-tier auto-router (`auto` alias) has its own design, tuning and
verification notes in `docs/ai-system/litellm/auto-router.md`.
