# LiteLLM (governance-only layer)

Redeployed 2026-08-26 per captain decisions **B4** and **D4**. This is not a
reversion to LiteLLM's pre-2026-06-07 role as this cluster's unified LLM
proxy (removed in #941 because it was a redundant double-proxy once
`agentgateway` matured - see `docs/ai-system/agentgateway/GLOSSARY.md` and the
investigation at the bottom of this page). It is a narrow, additive
governance layer that sits *beside* `agentgateway`, not in front of it.

Manifests: `kubernetes/apps/base/ai/litellm/`. App-level detail (image,
prerequisites, RBAC): `kubernetes/apps/base/ai/litellm/README.md`.

## Scope (binding, from B4/D4)

- **B4**: LiteLLM does **not** front the public listener, or any listener at
  all. In-cluster consumers only - no `HTTPRoute`, no
  `AgentgatewayBackend`, no route of any kind. Reachable only at
  `http://litellm.ai.svc.cluster.local:4000`, from inside the cluster, or via
  `kubectl port-forward` for a human operator.
- **B4**: Zero changes to `agentgateway/` (this cluster's actual AI gateway,
  built on the standalone AgentGateway product - "envoy AI gateway" in some
  captain shorthand) or to `vllm/`. This app only *reads* `vllm-app`'s
  OpenAI-compatible `/v1` endpoint as a `model_list` backend.
- **D4**: per-consumer governance from day one - a model allow-list *and* a
  spend/rate budget per virtual key, with deliberately tiny initial values,
  "we will expand later."

The captain's lab proved this works with a **six-line `model_list`** pointed
at the local chat backend, at **7.4ms** proxy overhead. That six-line block
is `kubernetes/apps/base/ai/litellm/app/resources/config.yaml` verbatim (the
file has a few more lines for the Prometheus metrics callback, which is
additive, not part of the lab result). Nobody could find that lab result
committed anywhere in the repo before this change (see the scout report
referenced in D4's decision trail) - this doc is that write-up.

## Why Postgres (`#why-postgres`)

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
architecture, not a design choice made here. No Redis/Dragonfly is used:
that's only needed for LiteLLM's router-state sharing across replicas or its
own response cache, neither of which this single-replica, governance-only
deployment uses.

## Per-consumer governance: how it actually gets minted

Config-file model routing (`config.yaml`) and DB-backed virtual keys
(`consumers.json`) are two different mechanisms, so this app does **not**
use the LLMKube/`litellm-operator` CRD approach the reference cluster
(`joryirving/home-ops`) uses - explicitly not adopted here in favor of a
plain HelmRelease per this repo's conventions. Instead:

1. `app/resources/consumers.json` declares each consumer: `name`, `models`
   (allow-list), `maxBudget` (USD), `budgetDuration`, `rpmLimit`, `tpmLimit`.
2. A Helm `post-install`/`post-upgrade` hook Job
   (`provision-keys` controller in `helmrelease.yaml`, script
   `app/resources/provision_keys.py`) runs after every install/upgrade. It is
   pure-stdlib Python (no PyYAML/curl/kubectl image) that:
   - waits for the proxy's `/health/liveness` to return 200,
   - reads the `litellm-consumer-keys` Secret in `ai` for any key this Job
     already minted,
   - for a **new** consumer: `POST /key/generate` (key_alias, models,
     max_budget, budget_duration, rpm_limit, tpm_limit), then writes the
     returned raw key into that Secret,
   - for an **existing** consumer: `POST /key/update` with the already-known
     raw key, syncing budget/rate-limit/allow-list only - **the credential
     itself never changes** on a values edit.
3. `app/pushsecret.yaml` pushes each consumer's key out of that Secret into
   1Password (`litellm-consumer-<name>` item, `key` property) - the same
   "GitOps-minted credential, durably in the secret store" pattern the
   reference cluster's `LiteLLMVirtualKey` + `PushSecret` combo uses, without
   needing their CRD.

### Adding or expanding a consumer (values-file edit, not a redesign)

1. Add an entry to `consumers` in
   `kubernetes/apps/base/ai/litellm/app/resources/consumers.json` (or edit an
   existing entry's `maxBudget`/`budgetDuration`/`rpmLimit`/`tpmLimit`/`models`
   to raise a limit).
2. Adding a **new** consumer also needs one matching `data.match` block in
   `app/pushsecret.yaml` (`secretKey` == the new consumer's `name`).
3. Commit, merge. The next HelmRelease reconcile (`flux reconcile hr litellm
   -n ai`, or just wait for the `1h` interval) re-runs the provisioning Job,
   which mints the new key or re-syncs the changed limits, and the PushSecret
   picks up the new 1Password item on its own 5m refresh.

Raising an existing consumer's budget is exactly this: edit the number, no
Job/RBAC/schema change needed - that's the "values-file edit, not a
redesign" property D4 asked for.

### Sensible tiny defaults (current values, `demo` consumer)

| Knob | Value | Why this number | Raise it |
| --- | --- | --- | --- |
| `models` | `["qwen3.6-35b-a3b"]` | The only model this proxy actually serves today (the six-line `model_list`). | Add the model to `config.yaml`'s `model_list` first, then to this consumer's `models`. |
| `maxBudget` | `0.05` (USD) | Small enough to exhaust in one manual smoke test, so the budget enforcement path (`429`) is trivially exercisable, not just theoretical. | Edit the number in `consumers.json`. |
| `budgetDuration` | `30d` | Spend resets monthly rather than being a one-time lifetime cap that permanently bricks the key. | Any LiteLLM duration string (`7d`, `1mo`, ...). |
| `rpmLimit` | `2` | Deliberately below any real interactive workload - proves the limiter fires without needing sustained load. | Raise once a real consumer is wired up. |
| `tpmLimit` | `2000` | About one short chat completion's worth of tokens. | Raise alongside `rpmLimit`. |

## Verification runbook (`#verification-runbook`)

**This cannot be run from a firstmate crewmate worktree** - this cluster has
no reachable API server or 1Password session from that sandbox (bare-metal
LAN cluster, no cloud access). Run this from a workstation with `kubectl`
context `home-ops` and 1Password CLI access, after the 1Password item and
this PR are both merged and Flux has reconciled:

```bash
# 1. Confirm the proxy and the provisioning Job both came up clean.
kubectl -n ai get deploy litellm
kubectl -n ai logs -l batch.kubernetes.io/job-name --tail=50 | grep -E "demo:|wrote"

# 2. Pull the demo consumer's minted key out of 1Password (pushed there by
#    pushsecret.yaml) or straight from the cluster:
DEMO_KEY=$(kubectl -n ai get secret litellm-consumer-keys -o jsonpath='{.data.demo}' | base64 -d)

# 3. Port-forward the proxy (it has no route by design - B4).
kubectl -n ai port-forward svc/litellm-app 4000:4000 &

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
#    maxBudget to something like 0.0001 first for a fast repro). Expect the
#    request to start failing with a budget-exceeded error once exhausted.

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
adds a narrow, optional governance layer beside `agentgateway`, fronting
nothing, replacing nothing, routing no existing consumer's traffic anywhere
it doesn't already go.
