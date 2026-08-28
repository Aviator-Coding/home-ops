# Claude Code subscription pass-through (`claude-code-subscription`)

Captain request 2026-08-27: *"use the claude code subscription claude-code cli and get more insight."*

A `claude` CLI logged in to a **personal Claude Max/Pro subscription** normally
talks straight to `api.anthropic.com`, and the cluster sees nothing. This model
puts LiteLLM in that path without taking over the billing: the CLI keeps sending
its own subscription OAuth token, LiteLLM forwards it upstream, and the tokens
stay billed to that person's flat-rate plan. What the cluster gains is the
"more insight" half - per-request token counts, latency and per-virtual-key
attribution for traffic that was previously invisible.

Declared by [`kubernetes/apps/base/ai/litellm/app/models/claude-code-subscription.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-code-subscription.yaml).
It is **the only model CR in this repo for which the proxy holds no credential.**

Upstream tutorial: <https://docs.litellm.ai/docs/tutorials/claude_code_max_subscription>.
Everything below that contradicts it was measured against our own pinned image
(`ghcr.io/berriai/litellm-non_root:v1.98.0`) on 2026-08-27 and the source
citations are to the `v1.98.0` tag.

---

## 1. We deliberately did NOT set `forward_client_headers_to_llm_api`

The upstream tutorial calls `general_settings.forward_client_headers_to_llm_api: true`
**"Required: forwards OAuth token to Anthropic."** On v1.98.0 that is wrong on
both halves, and setting it would have been a real, unnecessary widening.

**It does not forward the OAuth token.** That flag's only effect is
`add_litellm_data_for_backend_llm_call` (`litellm_pre_call_utils.py:1099`)
calling `_get_forwardable_headers` (`:926`), whose allow-list is *only* headers
starting `x-` (excluding `x-stainless*`) plus `anthropic-beta`. `Authorization`
matches neither and is dropped.

**The OAuth token travels a completely different, ungated path.**
`add_provider_specific_headers_to_request` (`:2971`) is called unconditionally
at `litellm_pre_call_utils.py:1704` - outside any `forward_client_headers_to_llm_api`
check. It copies `Authorization` into `data["provider_specific_header"]`
whenever `is_anthropic_oauth_key()` says the value starts with `sk-ant-oat`
(`llms/anthropic/common_utils.py:53`, prefix at `types/llms/anthropic.py:711`),
scoped to `custom_llm_provider = "anthropic,bedrock,vertex_ai"`. Downstream,
`optionally_handle_anthropic_oauth` (`common_utils.py:71`) reads that header and
**assigns it to `api_key`**, replacing whatever the deployment was configured
with. The same function unconditionally carries `anthropic-version` and
`anthropic-beta` too (`ANTHROPIC_API_HEADERS`, `types/llms/anthropic.py:664`).

So on v1.98.0 the feature works with the flag **off**, which is how we ship it.

**What the flag would have cost us.** It is genuinely proxy-global: not
per-model, not per-provider. Every `x-*` header any client sends would be
forwarded to *every* backend - `xai/`, `zai/`, `openrouter/`, and the local
llama.cpp behind `qwen3.6-35b-a3b` - for zero benefit to this feature.

If header forwarding is ever actually wanted, **use the narrow form instead**.
`add_headers_to_llm_call_by_model_group` (`:1042`) reads
`litellm_settings.model_group_settings.forward_client_headers_to_llm_api` as a
*list of model names* (exact or wildcard, resolved through
`_check_model_access_helper`), e.g.:

```yaml
litellmSettings:
  model_group_settings:
    forward_client_headers_to_llm_api:
      - claude-code-subscription
```

That is the supported way to scope it to this model alone. We need neither
today.

---

## 2. Security review, measured live

`forward_client_headers_to_llm_api` was absent from the live rendered
`litellm-config` ConfigMap for every measurement below - so these numbers
describe **production as it already is**, not as this change makes it.

### 2a. Virtual-key governance is NOT bypassable by a client header

`demo` is allow-listed to `qwen3.6-35b-a3b` only.

| # | Key | Model | Client `Authorization` | Result |
| --- | --- | --- | --- | --- |
| A1 | `demo` | `qwen3.6-35b-a3b` | *(none)* | `200` |
| A2 | `demo` | `qwen3.6-35b-a3b` | `Bearer sk-ant-oat01-FAKE…` | `200` - identical |
| A4 | `demo` | `claude-sonnet-5` | *(none)* | `403 key not allowed to access model. This key can only access models=['qwen3.6-35b-a3b']` |
| A5 | `demo` | `claude-sonnet-5` | `Bearer sk-ant-oat01-FAKE…` | `403` - **identical denial** |

Same on `/v1/messages` (C3). D4 allow-lists, budgets and rpm/tpm limits are
enforced before any header handling and a client-supplied header cannot move
that boundary. **This is the "unchanged governance" proof for existing models.**

Note A1/A2 also confirm the D4 limiter is live and shared: the first run of this
matrix exhausted `demo`'s `rpmLimit: 2` and returned
`429 Rate limit exceeded … Current limit: 2, Remaining: 0`.

### 2b. FINDING - a client OAuth token overrides the shared key on *existing* models

This is the one that matters, and it is **pre-existing, not introduced here.**

| # | Endpoint | Model | Client `Authorization` | Result |
| --- | --- | --- | --- | --- |
| B1 | `/v1/chat/completions` | `claude-sonnet-5` | *(none)* | `200` via shared `os.environ/ANTHROPIC_API_KEY` |
| B2 | `/v1/chat/completions` | `claude-sonnet-5` | `Bearer sk-ant-oat01-FAKE…` | `401 AnthropicException - {"type":"authentication_error","message":"OAuth access token is invalid."}` |
| B3 | `/v1/chat/completions` | `claude-sonnet-5` | `Bearer sk-garbage…` (not OAuth-shaped) | dropped, request proceeds normally |
| C1 | `/v1/messages` | `claude-sonnet-5` | *(none)* | `200` |
| C2 | `/v1/messages` | `claude-sonnet-5` | `Bearer sk-ant-oat01-FAKE…` | `401` - same |

Anthropic itself answering **"OAuth access token is invalid."** is the proof:
our real `sk-ant-api…` key was never sent. A caller's header replaced the
deployment's configured credential on a model that has nothing to do with this
feature, with the flag off.

Bounds of the exposure, all verified above:

- **Only `sk-ant-oat`-prefixed values.** Any other `Authorization` value is
  dropped by `clean_headers` (`:777`) - B3.
- **Only Anthropic-family providers** (`anthropic,bedrock,vertex_ai`); xai / zai
  / openrouter / local are untouched - A2.
- **Only when the caller authenticated with `x-litellm-api-key`.** If the
  virtual key is presented in `Authorization` (the usual way), `clean_headers`
  treats that header as proxy auth and never forwards it.
- **It cannot bypass entitlement** - §2a. The caller must already be allowed to
  call the model.

So the realistic impact is a caller *substituting their own subscription* for
the household key on a model they were already entitled to, or - if their token
is bad - failing their own request. It is not a privilege escalation.

### 2c. Secondary finding - a bad token briefly cools down the shared model group

B2's `401` did not only fail its own request: it tripped LiteLLM's deployment
cooldown for the whole `claude-sonnet-5` model group. Immediately afterwards,
unrelated requests returned
`429 No deployments available for selected model … cooldown_list=['e2216ed…']`,
and `/v1/messages` was affected too. It cleared on its own (D1: `200`, ~60s
later, no intervention).

Any consumer entitled to a shared Anthropic model can therefore make it briefly
unavailable **to every other consumer** by sending a malformed OAuth token.
Pre-existing and unchanged by this PR, but worth knowing before blaming a
provider outage.

---

## 3. Why the model carries a placeholder `apiKey`

Omitting `apiKey` does **not** produce a credential-less model.
`AnthropicModelInfo.get_api_key` (`common_utils.py:740`) is literally
`api_key or get_secret_str("ANTHROPIC_API_KEY")`, and the proxy pod holds
`ANTHROPIC_API_KEY` through its `envFrom`. Measured in the running pod:

```console
$ kubectl -n ai exec deploy/litellm -c litellm -- python3 -c '…get_api_key(None)…'
get_api_key(None) returned a key: True
  looks like our shared metered key (sk-ant-api prefix): True
  identical to ANTHROPIC_API_KEY env var: True
```

A caller who forgets the OAuth header would have **silently billed the
household's metered account** for traffic they believed their subscription
covered. The CR therefore sets a non-secret placeholder that is non-`None`
(blocking the env fallback) and `sk-ant-oat`-prefixed (so the request takes the
OAuth branch). The failure mode becomes Anthropic's own
`401 "OAuth access token is invalid."` - self-describing, and zero spend.
A real client token overrides it (§2b), so it is never used in normal operation.

---

## 4. Pricing decision: explicit `$0`, verified not guessed

LiteLLM has **no** OAuth-aware or subscription-aware cost handling - nothing in
`cost_calculator.py` or `spend_tracking/` distinguishes pass-through traffic. Left
unpriced, it would be charged against the caller's D4 budget at
`claude-sonnet-5`'s metered rate: dollars nobody is ever invoiced on a flat-rate
subscription.

The one real risk with `0` is a truthiness bug treating it as "unset". There
isn't one: `use_custom_pricing_for_model` (`litellm_logging.py:4661`) tests
`is not None`. Confirmed in the running pod:

```console
input_cost_per_token  in custom-pricing keys: True
output_cost_per_token in custom-pricing keys: True
explicit ZERO pricing is honoured as custom pricing: True
no pricing declared -> falls back to built-in cost map: True
```

That function also reads `model_info` from `litellm_metadata`, which is where
the `/v1/messages` route stores it - so the zero applies on the endpoint Claude
Code actually uses, not just `/v1/chat/completions`.

**Consequence:** a `maxBudget` cannot constrain this model, since $0 spend never
reaches it. Grant it only on a virtual key that carries `rpmLimit`/`tpmLimit`;
the real ceiling is Anthropic's own subscription rate limiting.

---

## 5. Client runbook

### 5a. One-time: log the CLI in to your subscription

Interactive and per-person. On the workstation that will use it:

```bash
claude          # then: /login  ->  "Claude account with subscription"
```

This is a browser OAuth flow that mints the `sk-ant-oat…` token into the CLI's
own credential store. **Headless verification of this step is out of scope**, the
same as every other manual credential step in this repo (a 1Password item, an
Authentik Proxy Provider): nothing in Git or CI can perform or check it. Neither
the cluster nor this repo ever sees the token - it stays on the workstation and
is sent per-request.

### 5b. One-time: get a LiteLLM virtual key

Registration is not entitlement, so this model ships with its own key
(captain decision 2026-08-27):
[`app/virtualkeys/claude-code-subscription.yaml`](../../../kubernetes/apps/base/ai/litellm/app/virtualkeys/claude-code-subscription.yaml).
It is allow-listed to `claude-code-subscription` and nothing else, so it is not
a second door into the metered Anthropic models.

It is also **the only key in that directory with no `maxBudget`**, deliberately:
per §4 its model is priced at $0, so any budget could never trip and would read
as protection that does not exist. `rpmLimit: 10` / `tpmLimit: 250000` are the
whole guardrail - one interactive CLI user, same order of magnitude as the
`opencode` workspace key and nudged up because Claude Code's agentic loop spends
requests per tool call, not per human turn. Raise those two numbers if a normal
session 429s; there is no budget to raise, and nothing in that file changes what
the subscription is charged.

The operator mints the key into a Secret and a `PushSecret` mirrors it to
1Password (`litellm-consumer-claude-code-subscription`). Read it from either:

```bash
kubectl -n ai get secret litellm-key-claude-code-subscription -o jsonpath='{.data.key}' | base64 -d
```

Give each additional person their own key rather than sharing this one, or the
per-consumer attribution that motivated the whole model collapses into a single
bucket.

### 5c. Point the CLI at LiteLLM

```bash
export ANTHROPIC_BASE_URL="https://litellm.${SECRET_DOMAIN}"   # internal gateway
export ANTHROPIC_MODEL="claude-code-subscription"
export ANTHROPIC_CUSTOM_HEADERS="x-litellm-api-key: Bearer sk-…your-virtual-key…"
claude
```

`litellm.${SECRET_DOMAIN}` resolves on the private VLAN only (split DNS,
`envoy-internal`); in-cluster callers can use
`http://litellm.ai.svc.cluster.local:4000` instead.

**The virtual key must go in `ANTHROPIC_CUSTOM_HEADERS` as `x-litellm-api-key`,
never as `Authorization`.** `Authorization` is reserved for the CLI's own
subscription token, and if LiteLLM sees its virtual key there it treats that
header as proxy auth and stops forwarding it upstream (`clean_headers`,
`:777`) - the pass-through silently stops working.

### 5d. Confirm the insight landed

```bash
# per-request tokens/latency/attribution for the new model
kubectl -n ai logs deploy/litellm -c litellm --since=5m | grep claude-code-subscription
```

Spend will read `$0` by design (§4); tokens, duration and the owning virtual key
are the signal. The same rows drive the Prometheus metrics scraped by
[`app/servicemonitor.yaml`](../../../kubernetes/apps/base/ai/litellm/app/servicemonitor.yaml).

### 5e. Adding Opus, or a background model

Copy the CR to a second file with its own `metadata.name`/`modelName` and a
different `params.model` (resolve the id against Anthropic's **direct** catalog -
`models/kustomization.yaml` rule 3), add it to
`models/kustomization.yaml`, and add its name to the virtual key's allow-list.
Keep the placeholder `apiKey` and the `$0` prices; both apply for the same
reasons.

---

## 6. What this change did NOT touch

Additive by design. One model CR and one new virtual key were added; no
*existing* model CR, virtual key, allow-list, fallback chain or
`generalSettings` value was modified, and the new key names only the new model.
The cloud entitlement boundary in
`routerSettings.fallbacks` ([`fallbacks.md`](fallbacks.md)) is unchanged, and
`claude-code-subscription` is deliberately absent from every fallback chain -
a config-declared fallback bypasses the calling key's allow-list, and pointing
one at a model that requires a client-supplied token would fail every caller who
does not send one.
