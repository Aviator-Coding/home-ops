# LiteLLM (governance layer)

Redeployed 2026-08-26 per captain decisions **B4** and **D4**. This is not a
reversion to LiteLLM's pre-2026-06-07 role as this cluster's unified LLM
proxy (removed in #941 because it was a redundant double-proxy once
`agentgateway` matured - see `docs/ai-system/agentgateway/GLOSSARY.md` and the
investigation at the bottom of this page). It is a narrow, additive
governance layer that sits *beside* `agentgateway`, not in front of it.

Manifests: `kubernetes/apps/base/ai/litellm/`. App-level detail (image,
prerequisites, the pod-security gap, DB bootstrap):
`kubernetes/apps/base/ai/litellm/README.md`.

**Delivery changed on 2026-08-26 (captain decision O1)**: the app is now a set
of `litellm.home-operations.com/v1alpha1` CRs reconciled by the
[home-operations litellm-operator](https://github.com/home-operations/litellm-operator)
(`kubernetes/apps/base/ai/litellm-operator/`), not a bjw-s app-template
HelmRelease. Nothing about B4/D4/D3 semantics changed - the operator renders
the same `config.yaml` from `LiteLLMModel` CRs (proven by a semantic diff at
migration time: one intentional difference, an explicit
`general_settings.store_model_in_db: false`) - but the *mechanism* below for
minting keys did, and that section has been rewritten accordingly.

The **complexity-tier auto-router** (captain decision D3, added 2026-08-26) is
one additive model alias on top of everything described here: design, tier
definitions, measured classifier latency/accuracy, and how to tune the
classifier prompt and thresholds all live in
[`auto-router.md`](auto-router.md). Nothing on this page changes because of
it - the direct model names, the virtual-key minting flow and the budgets
behave exactly as documented below, and routed calls bind to those same
budgets.

**Availability and context-window fallbacks** (Phase 5) live in
[`fallbacks.md`](fallbacks.md): measured allow-list-vs-fallback semantics, the
`chat-ha` / `ha-demo` entitlement split, alert expressions, the internal route
decision, and the post-merge failover runbook. Do not restate those facts here.

**Claude Code Max/Pro subscription pass-through** (captain request 2026-08-27)
lives in [`claude-code-subscription.md`](claude-code-subscription.md): the one
model the proxy holds no credential for, the deliberate non-use of
`forward_client_headers_to_llm_api`, `$0` pricing / no-`maxBudget` key shape,
and the client runbook. Do not restate those facts here.

**Full request/response logging** (captain request 2026-08-27) lives in
[`request-logs.md`](request-logs.md): the `store_prompts_in_spend_logs`
mechanism verified against the pinned image, which column actually holds the
prompt, how to pull one request's prompt/response/cost from the Admin UI, the
API or SQL, the confidentiality finding (LAN-only NAS Barman sink accepted
2026-08-27, caller credentials redacted), and the shipped 30d retention bound.
Do not restate those facts here.

## Scope (binding, from B4/D4)

- **B4**: LiteLLM does **not** front the **public** listener - no
  `envoy-external` parentRef, no `AgentgatewayBackend`. That half is unchanged
  and non-negotiable.
  **Amended 2026-08-26 (captain instruction):** the internal gateway is now
  allowed. `kubernetes/apps/base/ai/litellm/app/httproute-internal.yaml` attaches to
  `envoy-internal` at `litellm.${SECRET_DOMAIN}`. In-cluster consumers still
  use `http://litellm.ai.svc.cluster.local:4000` and nothing about that path
  changed; `kubectl port-forward` also still works.
- **B4**: Zero changes to `agentgateway/` (this cluster's actual AI gateway,
  built on the standalone AgentGateway product - "envoy AI gateway" in some
  captain shorthand) or to `vllm/`. This app only *reads* `vllm-app`'s
  OpenAI-compatible `/v1` endpoint as a `model_list` backend.
- **D4**: per-consumer governance from day one - a model allow-list *and* a
  spend/rate budget per virtual key, with deliberately tiny initial values,
  "we will expand later."

The captain's lab proved the governance path works with a **six-line
`model_list`** pointed at the local chat backend, at **7.4ms** proxy
overhead. That six-line block is still the direct `qwen3.6-35b-a3b` entry in
`kubernetes/apps/base/ai/litellm/app/models/qwen3.6-35b-a3b.yaml` (plus its
`info.extra` governance-accounting prices so virtual-key spend can accrue,
and the Prometheus metrics callback on the `LiteLLMProxy` - neither was part
of the lab result). D3's additive `auto` router, classifier deployment and
cloud tier backends are four more CRs in `app/models/` and are owned by
[`auto-router.md`](auto-router.md). Phase 5 adds a sixth CR, `chat-ha` (same
local backend as `qwen3.6-35b-a3b`, cloud-entitled fallback view) - owned by
[`fallbacks.md`](fallbacks.md). A seventh, `chat-local` (2026-08-27), is the
same local backend again with **no prices at all**; it exists because those
`info.extra` prices are a *test fixture* for the `demo` budget, and billing real
production traffic against them made free B70 compute show up as real-looking
dollars on every spend dashboard. Real traffic runs on `chat-local`;
`qwen3.6-35b-a3b` is now the demo test's alias alone. See
[`auto-router.md`](auto-router.md#why-the-local-tiers-point-at-chat-local-not-qwen36-35b-a3b).
Nobody could find that lab result committed anywhere in the repo before the
B4/D4 redeploy (see the scout report referenced in D4's decision trail) -
this doc is that write-up.

## Why Postgres

LiteLLM has **no config-file-only way to declare a virtual key with a budget
or rate limit.** Confirmed against LiteLLM's own docs while designing this:
virtual keys only exist via the `/key/generate` REST API, and without a
connected Postgres database that API only stores keys **in memory** - they
vanish on every pod restart, which defeats "budget" as a concept (nothing
would ever remember what a key has already spent). `general_settings.master_key`
alone (no DB) gives you exactly one credential with no per-consumer anything.

So D4's "virtual keys with budgets from day one" requirement forces a real
database, which is why this app - unlike most of this namespace's
otherwise-stateless apps - depends on the shared `postgres-17` CNPG cluster
(`kubernetes/apps/base/database/cloudnative-pg/`), the same way
`immich`/`linkwarden`/`seerr` do. This is the same tradeoff this app's
pre-removal incarnation made (its removal PR #941 explicitly had to `DROP
DATABASE litellm` as a manual follow-up) - it's inherent to LiteLLM's
architecture, not a design choice made here.

## Why Dragonfly (Redis)

Added 2026-08-27, prompted by the captain reading LiteLLM's own live warning
banner (`ui/litellm-dashboard/src/components/NoRedisWarningBanner.tsx` in
BerriAI/litellm): *"This proxy is running more than one worker (or the worker
count could not be verified). Without Redis, rate limits, budgets, router
state, and cache invalidation are per worker, so limits are enforced once per
worker and spend can overshoot."*

This app originally shipped with no Redis/Dragonfly on the assumption that
`replicas: 1` made per-instance state sharing moot. That assumption was
wrong: the banner triggers on **worker** count, not pod/replica count, and
LiteLLM's own uvicorn/gunicorn layer runs multiple workers inside a single
pod regardless of `spec.replicas`. So even this single-replica deployment was
enforcing D4's per-consumer rate limits and budgets independently per worker,
letting spend overshoot exactly the way the banner describes - the opposite
of what D4 was for.

The fix is `litellm-dragonfly` (this repo's own
[`kubernetes/components/dragonfly`](../../../kubernetes/components/dragonfly),
the same component `searxng`/`immich`/`authentik`/`paperless-ngx`/`rsshub`
already use), wired on
[`kubernetes/apps/main/ai/litellm.yaml`](../../../kubernetes/apps/main/ai/litellm.yaml)
and consumed by the `LiteLLMProxy` CR
([`app/litellmproxy.yaml`](../../../kubernetes/apps/base/ai/litellm/app/litellmproxy.yaml))
via `routerSettings.redis_host`/`redis_port` (cross-worker rate limits,
budgets, router state) and `litellmSettings.cache`/`cache_params` (a 300s-TTL
exact-response cache - mirrors the reference repo's pattern, joryirving
litellm/litellmproxy.yaml @ 3d3b700). `LITELLM_DISABLE_NO_REDIS_WARNING` is
deliberately never set: the fix is the store, not hiding the banner.

## Per-consumer governance: how it actually gets minted

Config-file model routing and DB-backed virtual keys are two different
mechanisms in LiteLLM, and the operator handles them with two different CRDs:

1. **`LiteLLMVirtualKey`** - one CR per consumer in
   `kubernetes/apps/base/ai/litellm/app/virtualkeys/`, carrying `models`
   (allow-list), optional `maxBudget` (USD, typed as a decimal **string**),
   `budgetDuration`, `rpmLimit`, `tpmLimit`, plus `keyAlias`, `secretName` and
   `secretKey`. This is the whole surface the retired `consumers.json` had, and
   more (`maxParallelRequests`, `duration`, `aliases`, `userID`/`teamID`,
   `metadata`). Every current key carries a `maxBudget` except
   `claude-code-subscription` - its model is subscription-priced at `$0`, so a
   budget could never trip; see [`claude-code-subscription.md`](claude-code-subscription.md).
2. The operator's virtual-key controller reconciles each CR against the proxy's
   admin API, authenticating with the master key named by
   `LiteLLMProxy.spec.apiAccess.masterKeyRef`. Branching is by the operator's
   own output Secret, **not** by alias lookup in the proxy DB:
   - **no output Secret yet**: `POST /key/generate`, then it creates the Secret
     named by `spec.secretName` (owned by the CR) holding the returned raw key,
   - **output Secret already exists**: it `GET`s the live key, compares every
     governed field, and only `POST /key/update`s when they differ - **the
     credential itself is preserved** across a budget or allow-list edit,
     exactly as the retired script did,
   - **deleted** CR: a finalizer deletes the remote key before the object goes,
     which the script never did (an orphaned key used to linger in the DB).
3. A `PushSecret` beside each CR mirrors the minted key into 1Password
   (`litellm-consumer-<name>` item, `key` property), unchanged from before.

This retires `consumers.json`, `provision_keys.py`, the `provision-keys` Helm
hook Job, the `provision-keys-sync` CronJob and their Role/RoleBinding. The
CronJob existed only because a ConfigMap-only Git change could not re-fire a
Helm hook; the operator watches the CRs directly, so a budget edit converges on
the next reconcile rather than within 15 minutes.

> **Key-alias collision / no adopt-by-alias.** A pre-existing proxy-DB row under
> the same `keyAlias` (including keys left by the retired script) blocks minting
> until deleted via `POST /key/delete` - that is a real credential rotation, not
> optional cleanup. Authoritative trap, symptoms, and the related
> controller-runtime backoff gotcha: the LiteLLM virtual-key and controller
> NOTES entries in `AGENTS.md`.

### Adding or expanding a consumer (one file, not a redesign)

1. Copy an existing file in
   `kubernetes/apps/base/ai/litellm/app/virtualkeys/` and change the CR name,
   `keyAlias`, `secretName`, the PushSecret's `remoteKey`, and the limits - or
   just edit an existing CR's `maxBudget`/`budgetDuration`/`rpmLimit`/
   `tpmLimit`/`models` to raise a limit.
2. Add the file to that directory's `kustomization.yaml`.
3. Commit, merge. Flux applies the CR; the operator mints (no output Secret yet)
   or PATCHes (Secret already exists). Reusing a `keyAlias` that already exists
   in the proxy DB without deleting it first stays `GenerateFailed` forever -
   see the key-alias callout above. The PushSecret picks up a newly written
   Secret key on its own 5m refresh. To watch it land:
   `kubectl -n ai get litellmvirtualkey -o wide` and
   `kubectl -n ai describe litellmvirtualkey <name>` (the status conditions name
   the failure reason - `AdminClientFailed`, `GenerateFailed`, `UpdateFailed` -
   when something is wrong).

Raising an existing consumer's budget is exactly this: edit the number on the
CR, no Job/RBAC/schema change needed - that's the "one-file edit, not a
redesign" property D4 asked for.

### Sensible tiny defaults (current values, `demo` consumer)

| Knob | Value | Why this number | Raise it |
| --- | --- | --- | --- |
| `models` | `["qwen3.6-35b-a3b"]` | The local chat backend, in its **synthetically priced** view - and since 2026-08-27 `demo` is that alias's *only* consumer, which is the point: those prices exist so this key's `$0.05` cap can be exhausted by one smoke test, and no production traffic should accrue them. Real consumers use the zero-priced `chat-local` instead. Since D3 the proxy also serves the `auto` router alias and its Anthropic tier backends, but `demo` is deliberately *not* given them - see the `router-demo` consumer and [`auto-router.md`](auto-router.md#budgets) for the routed-consumer shape. | Add a `LiteLLMModel` CR under `app/models/` first, then the model name to this consumer's `models`. |
| `maxBudget` | `0.05` (USD) | Small enough to exhaust in one manual smoke test, so the budget enforcement path (`429`) is trivially exercisable, not just theoretical. Spend is computed from the per-token prices on the model CR (`info.extra.input_cost_per_token` / `output_cost_per_token`, which the operator renders into `model_info`) - those are governance-accounting prices, not cloud billing; without non-zero prices LiteLLM records $0 spend and `max_budget` never trips. | Edit `maxBudget` on `app/virtualkeys/demo.yaml` - remembering it is a decimal STRING (and the model CR prices if the burn rate should change too). |
| `budgetDuration` | `30d` | Spend resets monthly rather than being a one-time lifetime cap that permanently bricks the key. | Any LiteLLM duration string (`7d`, `1mo`, ...). |
| `rpmLimit` | `2` | Deliberately below any real interactive workload - proves the limiter fires without needing sustained load. | Raise once a real consumer is wired up. |
| `tpmLimit` | `2000` | About one short chat completion's worth of tokens. | Raise alongside `rpmLimit`. |

## Verification runbook

**1Password writes still need a workstation session** - creating the
prerequisite `litellm` item is a human step and cannot be done from an agent
sandbox. The `kubectl` half of this runbook *is* reachable read-only from a
firstmate crewmate worktree with the repo's kubeconfig (corrected 2026-08-26:
the earlier claim that the API server was unreachable from there is wrong).
Run this after the 1Password item exists and Flux has reconciled this PR:

```bash
# 1. Confirm the operator, the proxy, the DB-init Job and the keys are clean.
kubectl -n ai get deploy litellm-operator litellm
kubectl -n ai get job litellm-db-init
kubectl -n ai get litellmproxy,litellmmodel,litellmvirtualkey
#    Every CR should report Ready=True; `llproxy` also prints the model count
#    (expect 6) and ready replicas. On a failure, the condition message names
#    the reason (AdminClientFailed / GenerateFailed / UpdateFailed / ...):
kubectl -n ai describe litellmvirtualkey demo

# 2. Pull the demo consumer's minted key out of 1Password (pushed there by
#    the PushSecret in app/virtualkeys/demo.yaml) or straight from the
#    operator-owned Secret:
DEMO_KEY=$(kubectl -n ai get secret litellm-key-demo -o jsonpath='{.data.key}' | base64 -d)

# 3. Port-forward the proxy. (Since 2026-08-26 it also has an internal route
#    at https://litellm.${SECRET_DOMAIN}; port-forward stays the
#    dependency-free path and is what this runbook uses.)
kubectl -n ai port-forward svc/litellm 4000:4000 &

# 4. Golden path: call the allow-listed model. Expect a normal completion.
curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $DEMO_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"say ok"}]}'

# 5. Allow-list enforcement: ask for a model NOT on this key's list. Expect
#    an auth/permission error, not a completion.
curl -s http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $DEMO_KEY" -H 'Content-Type: application/json' \
  -d '{"model":"some-other-model","messages":[{"role":"user","content":"hi"}]}'

# 6. Budget enforcement: repeat step 4 until $0.05 is exhausted (or lower
#    maxBudget in app/virtualkeys/demo.yaml to "0.0001" first for a fast
#    repro - it is a decimal STRING). Expect the request to start failing
#    with a budget-exceeded error once exhausted.

# 7. Confirm governance is visible in Prometheus:
#    litellm_remaining_api_key_budget_metric should show the key's
#    decreasing balance; the LiteLLMConsumerBudgetExhausted alert should
#    fire once it hits zero.
```

Record the actual output of steps 4-6 (redact the key) as the PR's evidence
that governance is active, not just deployed.

## Investigation this design supersedes

A separate scout investigation (`homeops-litellm-vs-agentgateway`, 2026-08-21)
looked at reverting **all** LLM traffic from `agentgateway` back to LiteLLM
and recommended against it - that would have recreated the exact
double-proxy shape #941 removed, for capability at best at parity with what
`agentgateway` already does, while reintroducing Postgres+Redis. That
recommendation still stands and is unrelated to this deployment: this app
adds a narrow, optional governance layer beside `agentgateway`, replacing
nothing and routing no existing consumer's traffic anywhere it doesn't already
go. It has an **internal-only** HTTPRoute (`litellm.${SECRET_DOMAIN}` on
`envoy-internal`) as of 2026-08-26; it still never fronts the public listener
and is still not this gateway's `/v1` - see Scope above and
[`fallbacks.md`](fallbacks.md#6).
