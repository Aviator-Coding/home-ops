# Claude Code subscription pass-through (`claude-code-subscription`)

Captain request 2026-08-27: *"use the claude code subscription claude-code cli and get more insight."*

A `claude` CLI logged in to a **personal Claude Max/Pro subscription** normally
talks straight to `api.anthropic.com`, and the cluster sees nothing. This model
puts LiteLLM in that path without taking over the billing: the CLI keeps sending
its own subscription OAuth token, LiteLLM forwards it upstream, and the tokens
stay billed to that person's flat-rate plan. What the cluster gains is the
"more insight" half - per-request token counts, latency and per-virtual-key
attribution for traffic that was previously invisible.

**RENAMED 2026-08-31** (captain decision - Alternative B of the pass-through
investigation report, selected over firstmate's own recommendation to leave
the naming alone): the
two pass-through model CRs moved from `claude-code-subscription` /
`claude-code-subscription-opus` to the **natural** `claude-sonnet-5` /
`claude-opus-5`, and the models that held those natural names - billed to this
household's metered Anthropic key - moved to `claude-sonnet-5-metered` /
`claude-opus-5-metered` to free them. **Every client-side
`ANTHROPIC_DEFAULT_OPUS_MODEL`/`ANTHROPIC_DEFAULT_SONNET_MODEL` override this
document used to require is gone** - §5c and §7 below are updated for the new
names, and §8 is new: it documents the deliberate 401 an admin now gets
asking for the bare natural name, and the metered names to use on purpose
instead. The dedicated virtual key keeps its original name, alias and Secret
(`claude-code-subscription` - it still accurately names what the key is FOR);
only the two models it is scoped to were renamed. Nothing else in this
document changed meaning: every measurement, table and mechanism below was
re-verified against the current config and still holds, with names updated
in place.

Declared by two model CRs - Sonnet
[`claude-sonnet-5.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-sonnet-5.yaml)
and, since 2026-08-30, Opus
[`claude-opus-5.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-opus-5.yaml).
They are **the only model CRs in this repo for which the proxy holds no
credential**, they differ from each other in exactly one line (`params.model`),
and everything this document says about one applies unchanged to the other.
Why Opus is a separate CR rather than a per-key alias, with the measurements
behind that: **§7**.

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
      - claude-code-subscription-opus
```

That is the supported way to scope it to these two models alone. We need
neither today.

---

## 2. Security review, measured live

`forward_client_headers_to_llm_api` was absent from the live rendered
`litellm-config` ConfigMap for every measurement below - so these numbers
describe **production as it already is**, not as this change makes it.

**Naming note:** every measurement below was taken 2026-08-27, before the
2026-08-31 rename. `claude-sonnet-5` in this section's tables is the model
that was named that at measurement time - today that CR is
`claude-sonnet-5-metered`. The mechanism these measurements prove (a client
OAuth token overrides the shared key on any Anthropic-family model the caller
is already entitled to; entitlement is still checked first) is a property of
the allow-list and header-forwarding code path, not of any one model's name,
so it holds unchanged under the new name.

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
unpriced, it would be charged against the caller's D4 budget at the metered
`claude-sonnet-5-metered` rate: dollars nobody is ever invoiced on a flat-rate
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

**Consequence for the CR prices:** declaring explicit `$0` is correct - a
flat-rate subscription has no per-token invoice, so LiteLLM's metered cost map
would otherwise inflate a D4 budget with dollars nobody is charged. **But the
two prices above are not the whole job:** prompt-cache pricing has its own
fields, and until they were zeroed too they carried essentially the entire
recorded bill (§4a). With the full set declared, recorded spend really is $0,
so a `maxBudget` can never trip and would read as protection that does not
exist. As of 2026-08-31 the dedicated calling key also carries no
`rpmLimit`/`tpmLimit` and currently has no local ceiling at all (§4b); do not
put these models on a budget-gated metered key. The true money ceiling is, and
always was, Anthropic's own subscription rate limiting.

### 4a. The `$0` needs the CACHE price fields too (fixed 2026-08-31)

The two prices above are necessary and **not sufficient**, and the gap was
large: this key had accrued **$54.31** of recorded spend on a model nobody is
invoiced for. Corrected here after measuring the live spend log.

**Where the money appeared to go.** Decomposing `metadata.cost_breakdown` over
every priced row on this key:

| component | recorded |
| --- | --- |
| `output_cost` | **$0.0000** |
| `cache_read_cost` | $27.7977 |
| `cache_creation_cost` | $26.5120 |
| **total** | **$54.3097** |

`output_cost` being exactly zero is the tell: the `output_cost_per_token: 0`
above was working perfectly. **Every recorded dollar was prompt-cache cost.**

**Mechanism.** `info.extra` declared only `input_cost_per_token` and
`output_cost_per_token`, but cache pricing lives in its own fields. On
registration `_resolve_builtin_model_cost_entry` copies `_CACHE_PRICING_FIELDS`
(`utils.py`) from the built-in entry onto the custom key, and built-in
`claude-sonnet-5` declares `cache_creation_input_token_cost` 2.5e-06,
`cache_creation_input_token_cost_above_1hr` 4e-06 and
`cache_read_input_token_cost` 2e-07. Claude Code caches its context on almost
every turn, so those three fields carried essentially the entire bill. The fix
is to zero them explicitly, including the `_above_*` tiers, since Claude Code
routinely exceeds a 200k context and caches for over an hour.

**Verified end-to-end, at zero cost.** A throwaway probe model was pointed at
the local llama.cpp backend while declaring an upstream id that *does* carry a
built-in price (`openai/gpt-4o-mini`), so the fallback price is observable
without ever calling a cloud provider:

| probe state | `/v1/chat/completions` | `/v1/messages` |
| --- | --- | --- |
| input/output zeroed only | `6e-07` (all `cache_read_cost`) | `6e-07` |
| **plus the five cache fields** | **`0`** | **`0`** |

So the fix lands on both routes, and the residual goes to exactly zero. The
probe model and its key were deleted afterwards and the rendered
`litellm-config` hashed byte-identical to its pre-test value.

**This corrects an earlier reading.** A first pass attributed the figure to the
`anthropic_messages` route being priced from the metered map while
`/v1/chat/completions` was fine. That was wrong: the route is irrelevant - the
probe reproduces the identical residual on both - and the `$0` was always
honoured for input and output. The real fault was incomplete price coverage.
The mistaken framing survives nowhere in this document; it is recorded here only
so the corrected mechanism is not re-litigated from the old numbers.

**No real money ever moved.** The caller's OAuth token pays Anthropic
throughout (§2b/§7). What was wrong was only the *accounting*, and the
historical rows are not retroactively repriced - `LiteLLM_SpendLogs` stores the
computed figure, so the ~$54 already recorded stays on this key until the 30-day
retention window (§10 of [`request-logs.md`](request-logs.md)) ages it out.
Expect the recorded total to keep showing that history for a while, and read
spend on this key as $0 only for requests made **after** this change.

**Consequence for `maxBudget` is unchanged.** With the cache fields zeroed a
budget can never trip, so one would be inert; before the fix a budget would have
tripped on fictional dollars and locked the key out. Either way a budget is the
wrong instrument here.

**Annotated, not zeroed, and unproven as of the 2026-08-31 rate-limit removal
below.** The historical **$54.31** stays on this key's `spend` column and in
`LiteLLM_SpendLogs` - see the disclaimer two paragraphs up - and is
additionally recorded in the header of
[`virtualkeys/claude-code-subscription.yaml`](../../../kubernetes/apps/base/ai/litellm/app/virtualkeys/claude-code-subscription.yaml)
(cause, amount, date, fix, all alongside the key itself). At the time that fix
shipped, **zero requests had hit this key since the 2026-08-31T04:09:50Z proxy
restart**, so the fix was real but not yet demonstrated against live traffic.
Rather than zero the figure to make it look resolved, `LiteLLMClaudeCodeSubscriptionSpendRegression`
in
[`app/prometheusrule.yaml`](../../../kubernetes/apps/base/ai/litellm/app/prometheusrule.yaml)
watches `litellm_spend_metric_total{api_key_alias="claude-code-subscription"}`
(the Prometheus scrape name for the per-request Counter incremented on every
logged call regardless of amount; process counters reset at the 2026-08-31
restart, so the series starts fresh and does not carry the DB's ~$54.31) for a
nonzero increase - that is the signal that either confirms the fix once real
traffic resumes, or catches a regression before it re-accrues another $54.

## 4b. Rate limits removed (captain decision, 2026-08-31)

`rpmLimit`/`tpmLimit` (raised to 3000/750000000 by the captain on 2026-08-30,
sizing history in the CR header) were dropped entirely on 2026-08-31.
Firstmate measured this key's actual usage across all 212 minutes it had ever
been active: peak 70 requests/min (p99 60) against a 3000 limit (43x headroom),
and peak 2,808,629 tokens/min against a 750000000 limit (267x headroom). At
that much headroom the limits were not functioning as the runaway-agent
guardrail they were introduced to be, and the real ceiling was always
Anthropic's own rate limiting against the caller's personal subscription -
this proxy neither sees nor can raise it. The captain chose to drop both and
rely on that upstream ceiling entirely. **This key now has no local ceiling of
any kind** - no `maxBudget` (§4/§4a), no `rpmLimit`, no `tpmLimit`.

**Removing the fields from the CR does not, by itself, clear an
already-set limit on the live key - this was measured, not assumed.**
`LiteLLMVirtualKeySpec.{TPMLimit,RPMLimit}` are Go `*int64` with
`json:",omitempty"`, so an absent field marshals to nothing in the
`/key/update` request body rather than an explicit JSON `null`. LiteLLM's
`prepare_key_update_data` (`key_management_endpoints.py`, v1.98.0) builds the
DB update via `data.model_dump(exclude_unset=True)`, which treats a field
that is merely *absent* from the request as "leave the column alone" - only a
field present with value `null` clears it. Measured live 2026-08-31: removing
both fields from the CR made litellm-operator's reconciler detect drift and
call `POST /key/update` (the key's `updated_at` timestamp advanced), but
`rpm_limit`/`tpm_limit` on the live key were unchanged at `3000`/`750000000`
immediately afterward. Clearing them for real took one manual, one-time
`POST /key/update` with `{"rpm_limit": null, "tpm_limit": null}` against the
admin API using the master key, confirmed by a follow-up `GET /key/info`
showing both `null`. That one-time step is only needed when an
*already-limited* key is going unlimited; a key created with no limits from
the start, or regenerated after its spec already carries none, never hits
this gap. Full narrative and the exact measurements: the CR header itself.

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
It is allow-listed to the two pass-through models (`claude-sonnet-5` and
`claude-opus-5` since the 2026-08-31 rename) and nothing else, so it is not a
second door into the metered Anthropic models (`claude-sonnet-5-metered` /
`claude-opus-5-metered`). Both allow-listed models are CRs for which **the
proxy holds no credential**;
that property, not the length of the list, is what keeps this key unable to
spend the household's money, and `scripts/ci/litellm-claude-code-subscription-test.py`
asserts it directly (every allow-listed name must carry the placeholder
`apiKey`, and none may be a metered route).

It is also **the only key in that directory with no `maxBudget`**, deliberately:
its models are priced at an explicit $0 across every field including the cache
ones (§4a), so recorded spend is $0 and any budget could never trip - a cap that
cannot fire reads as protection that does not exist. Note this only became true
on 2026-08-31: before the cache fields were zeroed, a budget here would have
tripped on ~$54 of fictional spend and locked the key out. As of 2026-08-31
(§4b) it also carries no `rpmLimit`/`tpmLimit` - the captain's call, after
measured headroom showed neither was functioning as a real guardrail anymore.
**This key has no local ceiling of any kind.** Current state, the full sizing
history, and the 2026-08-31 removal rationale all live on the CR itself (do not
restate them here). Git is the source of truth for those numbers; the LiteLLM
UI is not - a live UI edit is reverted on the next operator reconcile unless
the CR is updated first. The real ceiling the proxy cannot see or raise is
Anthropic's own rate limiting against the caller's personal subscription.
There is no budget to raise, and nothing in that file changes what the
subscription is charged. The `LiteLLMVirtualKey` is server-side state
reconciled by litellm-operator, so *raising* a limit takes effect on the live
key after merge + operator reconcile alone. **Clearing an already-set limit
does not** - the operator can only omit a field, never send it as an explicit
`null`, and LiteLLM's `/key/update` leaves an omitted field's existing value
untouched (§4b). Dropping a limit to none therefore needs a one-time manual
admin-API call in addition to the Git change, same as it did here on
2026-08-31.

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
export ANTHROPIC_MODEL="claude-sonnet-5"
export ANTHROPIC_CUSTOM_HEADERS="x-litellm-api-key: Bearer sk-…your-virtual-key…"
claude
```

**As of the 2026-08-31 rename, that is the whole client contract - no
`ANTHROPIC_DEFAULT_OPUS_MODEL`/`ANTHROPIC_DEFAULT_SONNET_MODEL` override is
needed anymore.** Before the rename, `ANTHROPIC_MODEL` alone only named the
model for the *main* loop, and anything that asked for a model by family - a
subagent declared `model: opus`, `/model opus`, a Task tool call - resolved
that family through `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` instead, defaulting to
Anthropic's own public ids `claude-opus-5` / `claude-sonnet-5` - which were,
at the time, the **metered** CRs this key was correctly refused for (§7 tells
that history and why it was fixed on the client rather than by proxy-side
aliasing). Now those exact ids ARE this pass-through's own model names, so a
by-family request resolves to the same place `ANTHROPIC_MODEL` does, with
nothing to override. See §8 for what happens if you deliberately want the
metered route instead.

The installed CLI (2.1.251) also honours `ANTHROPIC_DEFAULT_HAIKU_MODEL` and
`ANTHROPIC_DEFAULT_FABLE_MODEL`. **Deliberately left unset**: no pass-through
CR exists for those families, and inventing one is a catalog decision, not part
of this fix. A request that resolves to either 403s (no allow-listed model of
that name at all, pass-through or metered). If background/Haiku traffic starts
failing visibly, the fix is a third CR following §5e, not an allow-list edit.

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

Tokens, duration and the owning virtual key are the signal. Spend reads `$0` by
design (§4), but only for requests made **after** 2026-08-31: rows recorded
before the prompt-cache fields were zeroed carry real-looking dollars nobody was
invoiced, and are not retroactively repriced (§4a). Read a non-zero total on this
key as history, not as spend - and if NEW rows start carrying cost again, a cache
price field has been dropped from a model CR. The same rows drive the Prometheus metrics scraped by
[`app/servicemonitor.yaml`](../../../kubernetes/apps/base/ai/litellm/app/servicemonitor.yaml).

### 5e. Adding a further family (Haiku, Fable)

Opus is already done -
[`app/models/claude-opus-5.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-opus-5.yaml),
added 2026-08-30, renamed 2026-08-31. To add another family, follow the same
shape:

1. Copy either subscription CR to a new file with its own
   `metadata.name`/`modelName` and a different `params.model`, resolving the
   id against Anthropic's **direct** catalog (`models/kustomization.yaml`
   rule 3 - the dash form `anthropic/claude-haiku-5` or similar, never
   OpenRouter's dotted spelling).
2. Add the file to `models/kustomization.yaml`.
3. Add the new `modelName` to the virtual key's allow-list.
4. **Decide the name deliberately, the same choice the 2026-08-31 rename made
   for Sonnet and Opus.** If nothing else on this proxy already holds that
   family's natural name (there is no metered `claude-haiku-5`/`claude-fable-5`
   CR today), give the new pass-through CR the natural name outright and no
   client override is needed - this is the now-preferred shape. Only reach for
   `ANTHROPIC_DEFAULT_<FAMILY>_MODEL` (§5c's pre-rename mechanism) if the
   natural name is already taken by a metered CR you are not renaming; in that
   case, follow §7's evidence for why a per-key alias does not work and why
   the client override was the fallback, not the first choice.

**Keep the placeholder `apiKey` and the full set of explicit `$0` prices** -
input, output, **and** the five prompt-cache fields (§4a). All of them apply
for the same measured reasons (§3, §4), and the CI test fails the change if any
one is dropped: an omitted `apiKey` is not a credential-less model, it is a
silent fallback to the household's metered `ANTHROPIC_API_KEY`; dropping a
cache zero restarts the fictional-spend accrual that §4a closed.

**Never solve a new family by adding its metered name to the allow-list**
(`claude-opus-5-metered`, `claude-sonnet-5-metered`, `auto`). That converts
this key into the second door into metered billing the whole design exists to
prevent, and it is what §7 rules out on measured grounds.

---

## 6. What these changes did NOT touch

Additive by design across five passes. The money-safety boundary is unchanged
in all of them: no other virtual key, no household credential, and no
metered-model *credential* was touched. The metered CRs still carry
`os.environ/ANTHROPIC_API_KEY` and stay absent from this key's allow-list; the
CI test asserts both, under their current names. The cloud entitlement
boundary in `routerSettings.fallbacks` ([`fallbacks.md`](fallbacks.md)) is
unchanged, and both pass-through models are deliberately absent from every
fallback chain - a config-declared fallback bypasses the calling key's
allow-list, and pointing one at a model that requires a client-supplied token
would fail every caller who does not send one.

**2026-08-27 (original).** One model CR and one new virtual key were added; no
*existing* model CR, virtual key, allow-list, fallback chain or
`generalSettings` value was modified, and the new key named only the new model.

**2026-08-30 (Opus).** One model CR added, plus one line on the subscription
key's own allow-list naming it. Rate limits on that key were raised in the same
window (sizing history on the CR / §4b) but no other virtual key moved.

**2026-08-31 (cache-price zeros).** Both subscription model CRs gained the five
prompt-cache `$0` fields (§4a). No allow-list, rate-limit, virtual-key,
fallback, or credential change - only the incomplete `info.extra` price set on
the two pass-through CRs. Historical spend rows were not rewritten.

**2026-08-31 (rate-limit removal / phantom-spend annotation).** The subscription
key's `rpmLimit`/`tpmLimit` were removed entirely (§4b); the ~$54.31 historical
phantom spend was annotated on the CR header, not zeroed; and
`LiteLLMClaudeCodeSubscriptionSpendRegression` was added. No `LiteLLM_SpendLogs`
row and no key `spend` column was altered. No metered model, other virtual key,
fallback, credential, or `/anthropic` route change.

**2026-08-31 (rename, Alternative B).** The two pass-through model CRs moved
from `claude-code-subscription`/`claude-code-subscription-opus` to the natural
`claude-sonnet-5`/`claude-opus-5`; the metered CRs that held those names moved
to `claude-sonnet-5-metered`/`claude-opus-5-metered`; the D3 auto-router's tier
map (`models/auto.yaml`) and both `routerSettings` fallback chains
(`litellmproxy.yaml`) were repointed at the `-metered` names in the same
change, since a fallback or tier pointed at a credential-less pass-through
model would 401 instead of serving. The dedicated virtual key's own name,
alias and Secret did not change - only its `models` allow-list values did.
Every client-side `ANTHROPIC_DEFAULT_OPUS_MODEL`/`ANTHROPIC_DEFAULT_SONNET_MODEL`
override was removed (§5c). No virtual key's entitlement changed: nothing
named either metered model or the pass-through models directly before this
change, so the rename alone could not silently grant or revoke access -
verified against the current repo, not assumed from the 2026-08-30 report. No
household credential, no `/anthropic` route, and no rate limit or budget was
touched.

---

## 7. HISTORICAL: the `claude-opus-5` name collision, and why it was closed on the client (2026-08-30 to 2026-08-31)

**This entire section describes the state BEFORE the 2026-08-31 rename.** At
the time, the pass-through CRs were named `claude-code-subscription` /
`claude-code-subscription-opus` and the natural names `claude-sonnet-5` /
`claude-opus-5` belonged to the metered CRs - so a client asking for a model
by family collided with the household's metered account, and the fix living
here was a client-side environment variable, not a rename. The rename in §6's
last entry is what actually retired this collision: it is kept below, names
unchanged from the original measurements, because it is the evidence trail
that justified the client-side workaround being the right interim fix, and
because §7a's finding (per-key aliasing cannot work at all, for a structural
ordering reason) is still true today and still the reason a future
name-collision cannot be solved by aliasing either. If you are adding a new
pass-through family, read §5e first - the current answer is "own the natural
name outright," not "add a client override."

Added 2026-08-30, after a `claude` subagent requested Opus by name and got
`403 … can only access models=['claude-code-subscription']. Tried to access
claude-opus-5`.

The awkward part was that the name Claude Code sends, `claude-opus-5`,
**already meant the metered model on this proxy at the time**. So a second
pass-through CR was necessary but not sufficient: the client's request still
had to reach it. Two options were considered.

### 7a. Per-key model aliasing - evaluated against v1.98.0, and rejected

LiteLLM does have key-level aliases, and our CRD exposes them
(`LiteLLMVirtualKey.spec.aliases`, type `object`). The appealing shape was
`aliases: {claude-opus-5: claude-code-subscription-opus}` on this key only,
leaving every other key's `claude-opus-5` metered. **It does not work, and the
reason is an ordering that no amount of config can change.**

The rewrite lives in `_update_model_if_key_alias_exists`
(`proxy/litellm_pre_call_utils.py:2177`), which runs inside
`add_litellm_data_to_request` - i.e. in the route handler. The 403 is raised
much earlier, by `can_key_call_model` (`proxy/auth/auth_checks.py:3370`) called
from the `user_api_key_auth` FastAPI **dependency**, before the handler body
executes. And that check has no key-alias awareness at all: it consults
`_model_in_team_aliases` (`auth_checks.py:3334`) for *team* aliases, and
`auth_checks.py` contains no `_model_in_key_aliases` counterpart. So the
allow-list is always evaluated against the **raw** requested name.

Measured live against the running pod on 2026-08-30 with a throwaway key
(`key_alias=fm-alias-probe-TEMP`, `duration=20m`, deleted immediately after -
it never held a metered model):

| # | Key `models` | Key `aliases` | Requested | Result |
| --- | --- | --- | --- | --- |
| E1 | `['claude-code-subscription']` | *(none)* | `claude-opus-5` | `403 key_model_access_denied` - the captain's error, reproduced |
| E2 | `['claude-code-subscription']` | `{claude-opus-5: claude-code-subscription}` | `claude-opus-5` | `403` - **byte-identical denial; the alias changed nothing** |
| E3 | `['claude-code-subscription']` | `{claude-opus-5: claude-code-subscription}` | `claude-code-subscription` | `401 "OAuth access token is invalid."` - control: the key does work |

E2 is the whole argument. The only way to make an alias fire would be to *also*
put `claude-opus-5` on the key's `models` - and then the key's entitlement
names the metered route, with nothing but a downstream string rewrite standing
between subscription traffic and the household's `ANTHROPIC_API_KEY`. Drop the
alias (a CRD field pruned, a hand-edited key, an operator regression) and the
key silently bills real money. That is precisely the failure this feature was
built to make impossible, so per-key aliasing is rejected on money-safety
grounds, not merely because it is unsupported.

Team-level aliases *are* consulted by the access check and would technically
work. They are not used: `team_model_aliases` is marked deprecated in this very
source file, it would introduce a LiteLLM team construct this repo has no other
use for, and it buys nothing over the client-side option below.

### 7b. Chosen: a distinct model name plus the client's own alias vars

`claude-code-subscription-opus` is a distinct name, so it never collides with
the metered CR, and the allow-list keeps its real invariant - *every* model on
this key is one for which the proxy holds no credential. The client points its
Opus family at that name with `ANTHROPIC_DEFAULT_OPUS_MODEL` (§5c), which the
installed Claude Code 2.1.251 supports alongside `ANTHROPIC_DEFAULT_SONNET_MODEL`,
`ANTHROPIC_DEFAULT_HAIKU_MODEL` and `ANTHROPIC_DEFAULT_FABLE_MODEL`.

The cost of this option is honest and worth stating: **it is workstation
configuration, so it cannot be enforced from Git.** A user who sets only
`ANTHROPIC_MODEL` still gets the 403 on Opus. That is the right failure - it is
loud, it is free, and it never reaches the metered account - and it is the same
class of manual step as the one-time OAuth login in §5a. What the repo *can*
enforce, and does, is that no misconfiguration on the client can turn into
metered spend: the allow-list names no metered route and the CI test fails the
build if that ever changes.

### 7c. Money-safety verification, measured live

Run 2026-08-30 against the running proxy with the Opus CR applied and a
throwaway key carrying the exact post-merge allow-list
(`['claude-code-subscription', 'claude-code-subscription-opus']`). The captain's
own key was never modified. Both temporary objects were deleted afterwards and
the rendered `litellm-config` hashed **byte-identical** to its pre-test value.

| # | Model requested | Client `Authorization` | Result |
| --- | --- | --- | --- |
| V1 | `claude-code-subscription-opus` | *(none)* | `401 AnthropicException … "OAuth access token is invalid."` |
| V2 | `claude-code-subscription-opus` | `Bearer sk-ant-oat01-FAKE…` | `401` - same |
| V2m | same, on **`/v1/messages`** | *(none)* | `401` - same |
| V3 | `claude-code-subscription` | *(none)* | `401` - existing Sonnet path unchanged |
| V4 | `claude-opus-5` (metered) | *(none)* | `403 key_model_access_denied` |
| V5 | `claude-sonnet-5` (metered) | *(none)* | `403` |
| V6 | `auto` (D3 router) | *(none)* | `403` |
| V7 | `claude-opus-5` (metered) | `Bearer sk-ant-oat01-FAKE…` | `403` - a client header cannot move the boundary |

**V1 is the money-safety proof, and it is the same measurement the original
design rested on.** Anthropic itself answering *"OAuth access token is
invalid."* means the request was admitted by the allow-list, was dispatched on
the Anthropic Opus route, and **carried no `sk-ant-api…` household key** - if
our metered credential had been sent, that request would have succeeded and
been billed. The throwaway key's recorded spend was `0.0` after the whole
matrix.

V4-V7 are the negative half: the two-model allow-list is not a widening. Every
metered route stayed refused, and the rendered config at measurement time
showed `claude-opus-5` and `claude-sonnet-5` (then the metered CRs; today
`claude-*-5-metered`) carrying `os.environ/ANTHROPIC_API_KEY`, untouched.

**What is NOT proven here, stated plainly:** a *successful* Opus completion. That
needs a real subscription OAuth token, which lives only on a person's
workstation and which neither this repo nor CI can hold - the same boundary §5a
draws around the interactive login. The 401 proves every link in the chain
except the token itself, and the identical chain is what serves Sonnet in
production today. At the time this was measured, that was confirmed on a
logged-in workstation with `claude -p '...' --model claude-code-subscription-opus`;
today the equivalent command names the current pass-through model directly,
`claude -p '...' --model claude-opus-5`.

---

## 8. Reaching the metered models on purpose

The 2026-08-31 rename has one deliberate, unavoidable cost: **an admin who
asks for `claude-sonnet-5` or `claude-opus-5` without a subscription OAuth
token now reaches the pass-through CR and gets Anthropic's own
`401 "OAuth access token is invalid."`, not a working metered call.** This is
not a bug and not the pre-existing §7 collision reappearing - it is the
mechanism in §3 doing exactly what it was built to do (refuse cleanly rather
than silently bill the household key), now firing for a new class of caller:
someone who deliberately wants the metered, household-billed route and no
longer has a natural name that reaches it.

**If you hit that 401 and you wanted the metered account, not your own
subscription, use the metered names instead:**

- `claude-sonnet-5-metered` - [`app/models/claude-sonnet-5-metered.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-sonnet-5-metered.yaml)
- `claude-opus-5-metered` - [`app/models/claude-opus-5-metered.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-opus-5-metered.yaml)

Both are the same models that used to answer to the bare `claude-sonnet-5` /
`claude-opus-5` names before 2026-08-31 - same upstream `anthropic/claude-*-5`
id, same `os.environ/ANTHROPIC_API_KEY` credential, same real per-token
billing against this household's metered account and D4 consumer budgets.
Nothing about how they work changed, only their name.

**Reaching them requires a key entitled to the metered route.** The dedicated
`claude-code-subscription` key (§5b) is deliberately NOT one - its allow-list
holds only the two credential-less pass-through CRs, and the CI test
(`scripts/ci/litellm-claude-code-subscription-test.py`,
`virtualkey_allowlist_names_no_metered_route`) fails the build if that ever
changes. Reach the metered models the same way every other cloud consumer in
this proxy already does: a virtual key whose `models` allow-list names
`claude-sonnet-5-metered`/`claude-opus-5-metered` directly, or `auto`
(the D3 router, whose COMPLEX/REASONING tiers point at these same CRs -
`models/auto.yaml`), each carrying a real `maxBudget` since these routes bill
real money. `curl`/API callers can also use `-H 'Authorization: Bearer sk-…'`
against `/v1/chat/completions` or `/v1/messages` with `"model":
"claude-sonnet-5-metered"` and no OAuth header at all - the master key or any
metered-entitled virtual key authenticates the call normally, exactly as
before the rename.

**Where an admin will actually see this documented at the point of failure:**
the Kustomization comment on both metered model CRs
([`claude-sonnet-5-metered.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-sonnet-5-metered.yaml),
[`claude-opus-5-metered.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-opus-5-metered.yaml))
and the pass-through CRs
([`claude-sonnet-5.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-sonnet-5.yaml),
[`claude-opus-5.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/claude-opus-5.yaml))
all cross-reference this document and each other by name, and
[`kubernetes/apps/base/ai/litellm/README.md`](../../../kubernetes/apps/base/ai/litellm/README.md#claude-code-subscription-pass-through)
links here from the app's own model-catalog table - the two places an admin
debugging a 401 against this proxy is most likely to already be looking.
