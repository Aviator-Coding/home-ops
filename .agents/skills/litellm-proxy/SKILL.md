---
name: litellm-proxy
description: "Read before touching ai/litellm, ai/litellm-operator or ai/litellm-pgvector: adding or editing a LiteLLMModel (any new or metered model), LiteLLMVirtualKey or LiteLLMProxy, prices, allow-lists, budgets, rate limits, fallbacks, pass-through routes, spend logs, the auto-router, Claude Code subscription models, or the AI PR reviewer. Also when a LiteLLM CR is stuck or the proxy bills unexpectedly."
---

# LiteLLM proxy, operator CRs, keys and models

LiteLLM is a narrow governance layer beside agentgateway: per-consumer virtual
keys with allow-lists and budgets, fallback chains, an auto-router, and a
Claude Code subscription pass-through. Delivered as
`litellm.home-operations.com/v1alpha1` CRs reconciled by the home-operations
litellm-operator. Internal route only; never the public listener.

## Tripwires

Money and security first. Each has a CI gate; never weaken one to land a change.

1. **A config-declared fallback bypasses the calling key's allow-list.** A
   cloud fallback may only sit on an alias every holder is already
   cloud-entitled to (today `chat-ha` and `auto`, both to
   `claude-sonnet-5-metered`). Never add one to `chat-local`,
   `qwen3.6-35b-a3b`, the classifier, `pr-review-local`, `embedding-local` or a
   pass-through model. [fallbacks.md](references/fallbacks.md)
2. **A `LiteLLMModel` with no `apiKey` is not credential-less.** It falls back
   to the pod's metered `ANTHROPIC_API_KEY`. The four subscription
   pass-through CRs carry the `sk-ant-oat-PLACEHOLDER-...` key and `$0` on all
   seven price fields (input, output, five cache fields); dropping any one
   bills the household or restarts phantom spend.
   [claude-code-subscription.md](references/claude-code-subscription.md)
3. **Metered cloud models carry no `info.extra` prices** (the cost map is the
   invoice), except `qwen/qwen3-embedding-8b`, whose real price is declared
   because the cost map lacks it. Local models carry none (sunk hardware); only
   the `qwen3.6-35b-a3b` demo fixture has synthetic prices (`demo` only). [model-catalog.md](references/model-catalog.md)
4. **Registration is not entitlement.** A new CR is reachable only once a key
   allow-lists it; allow-list changes are captain decisions. `models: []`
   means all models.
5. **Removing `rpmLimit`/`tpmLimit`/`maxBudget` from a key never clears it on
   the live key** (`omitempty`); going unlimited needs a one-time admin
   `/key/update` with explicit `null`s.
   [governance-keys.md](references/governance-keys.md)
6. **Key aliases are global and there is no adopt-by-alias.** An existing
   alias leaves the CR `GenerateFailed` forever; `/key/delete` first, which is
   a real rotation. Per-key `aliases` grant nothing.
7. **Never `spec.metadata` on `jev-decisions`**: it silently deletes the
   hand-applied pass-through grant.
   [passthrough-lockdown.md](references/passthrough-lockdown.md)
8. **Pass-through routes are admin-only by config, and entry order is
   load-bearing**: each exact `/openrouter` or `/anthropic` entry sits above
   its dead-loopback catch-all; never set `forward_headers`.
9. **Never name a hand-written HTTPRoute `litellm`, never set `spec.route`**:
   the operator deletes the route on a later reconcile.
   [platform.md](references/platform.md)
10. **Thinking models return empty `content`.** The classifier and
    `pr-review-local` need `enable_thinking: false` declared on the model;
    without it the router fails open on 100% of traffic while looking healthy.
    Image floor v1.93.0. [auto-router.md](references/auto-router.md)
11. **Auto-router fail-open reads `complexity_router_default_model`**, not the
    inner `default_model`; keep both on `chat-local`, and cloud tiers on the
    `-metered` CRs.
12. **`os.environ/<VAR>` is resolved at startup**: removing a provider key
    from `externalsecret.yaml` while a model names it downs the whole proxy.
13. **`litellm-dragonfly` is required**: without it limits and budgets are
    per worker and overshoot, even with `replicas: 1`.
14. **The AI PR reviewer is advisory only** (never a required check, never
    `auto`), and rotating its key has a second, silent GitHub-secret step.
    [pr-reviewer.md](references/pr-reviewer.md)
15. **Never call `GET /spend/logs` without a filter** (it OOM-killed the
    proxy), and never zero historical spend to make a number look clean.
    [spend-logs.md](references/spend-logs.md)
16. **Vector store: one model, one width** (`qwen/qwen3-embedding-8b` at
    2000 dims), registry under `extraConfig`, and never export
    `PG_VECTOR_API_KEY`. [vector-store.md](references/vector-store.md)

## Where things live

| What | Path |
|---|---|
| Proxy CR (image, env, SSO, generalSettings, routerSettings, pass-through, Hub list, vector registry) | `kubernetes/apps/base/ai/litellm/app/litellmproxy.yaml` |
| Models (one CR each) | `kubernetes/apps/base/ai/litellm/app/models/` |
| Virtual keys + PushSecrets | `kubernetes/apps/base/ai/litellm/app/virtualkeys/` |
| Internal route + `/anthropic` 404 filter | `kubernetes/apps/base/ai/litellm/app/httproute-internal.yaml` |
| `litellm-secret` (from 1Password items `litellm`, `cloudnative-pg`, `ai-keys`, `litellm-sso`, `litellm-pgvector`) | `kubernetes/apps/base/ai/litellm/app/externalsecret.yaml` |
| DB init Job, alerts, scrape | `app/dbinit.yaml`, `app/prometheusrule.yaml`, `app/servicemonitor.yaml` |
| Operator chart | `kubernetes/apps/base/ai/litellm-operator/app/helmrelease.yaml` |
| Vector-store server | `kubernetes/apps/base/ai/litellm-pgvector/app/` |
| Flux overlays | `kubernetes/apps/main/ai/litellm{,-operator,-pgvector}.yaml` |
| Authentik OIDC provider | `terraform/authentik/litellm.tofu` (skill `authentik-terraform`) |
| AI PR reviewer | `.github/workflows/ai-pr-review.yaml`, `.github/ai-review-rules.md` |
| Human runbooks | `kubernetes/apps/base/ai/litellm/README.md` (prerequisites, Jev grant), [claude-code-subscription.md](references/claude-code-subscription.md) (client setup), [spend-logs.md](references/spend-logs.md) (reading request logs) |
| CI gates | `scripts/ci/litellm-*-test.py` |

## Procedures

- Add a model (metered or local): [model-catalog.md](references/model-catalog.md), "Adding a new metered model".
- Add or change a consumer key, budgets, rotation: [governance-keys.md](references/governance-keys.md).
- Change a fallback chain, then prove it: [fallbacks.md](references/fallbacks.md), [failover-drill.md](references/failover-drill.md).
- Tune or verify the auto-router: [auto-router.md](references/auto-router.md).
- Add a subscription family or debug a pass-through 401: [claude-code-subscription.md](references/claude-code-subscription.md).
- Re-apply the Jev grant, reason about `/openrouter` and `/anthropic`: [passthrough-lockdown.md](references/passthrough-lockdown.md).
- Read a request's prompt/response/cost, retention: [spend-logs.md](references/spend-logs.md).
- Operator, route, SSO, pod posture, DB bootstrap: [platform.md](references/platform.md).

## Verify

- Offline: `for t in scripts/ci/litellm-*-test.py; do python3 "$t"; done` and
  `task flux:test:all`. Several tests drive the real `litellm` library pinned
  to the proxy image; install it as `validate.yaml`'s `python-tests` job does.
- Live (read-only): `kubectl -n ai get litellmproxy,litellmmodel,litellmvirtualkey`
  (all `Ready=True`); a stale `Ready=False` may be controller backoff, check
  `lastTransitionTime` and operator logs.
- Rendered config: `kubectl -n ai get cm litellm-config -o jsonpath='{.data.config\.yaml}'`.
- Live-testing a CR change: `flux suspend ks litellm -n ai`, patch, test,
  `flux resume` (skill `flux-gitops`).
