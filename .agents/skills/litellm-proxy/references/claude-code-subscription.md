# Claude Code subscription pass-through

Four `LiteLLMModel` CRs for which the proxy holds **no credential**:
`claude-sonnet-5`, `claude-opus-5`, `claude-haiku-4-5-20251001`,
`claude-fable-5-1`. A `claude` CLI logged in to a personal Max/Pro
subscription sends its own OAuth token per request; LiteLLM forwards it, the
tokens bill that person's flat-rate plan, and the cluster gains per-request
tokens, latency and per-key attribution. The CRs differ only in
`params.model`. Their key is `virtualkeys/claude-code-subscription.yaml`.
Client setup (human runbook):
[`docs/ai-system/litellm/claude-code-subscription.md`](../../../../docs/ai-system/litellm/claude-code-subscription.md).

## How the token travels (do not set the global header flag)

- `add_provider_specific_headers_to_request` runs **unconditionally** in
  `litellm_pre_call_utils.py`. When `Authorization` starts `sk-ant-oat`
  (`is_anthropic_oauth_key`), it becomes `provider_specific_header`, and
  `optionally_handle_anthropic_oauth` assigns it to `api_key`, replacing the
  deployment's own key. `anthropic-version`/`anthropic-beta` travel too.
  On the pinned image the OAuth header is scoped to the `anthropic` provider.
- `general_settings.forward_client_headers_to_llm_api` does **not** forward
  `Authorization` (only `x-*` minus `x-stainless*`, plus `anthropic-beta`),
  and it is proxy-global: every client `x-*` header would reach every backend
  (xai, zai, openrouter, local). It is deliberately not set, whatever the
  upstream tutorial says. If header forwarding is ever needed, use the
  per-model list form
  `litellm_settings.model_group_settings.forward_client_headers_to_llm_api`.
  CI asserts the global flag stays off.
- The virtual key must go in `x-litellm-api-key` (`ANTHROPIC_CUSTOM_HEADERS`),
  never `Authorization`: a virtual key in `Authorization` is treated as proxy
  auth and not forwarded, and the pass-through silently stops working.

## The same path applies to every Anthropic model (pre-existing)

A caller entitled to a **metered** Anthropic model can send an `sk-ant-oat`
token and substitute their own subscription for the household key (measured:
Anthropic answered "OAuth access token is invalid." for a fake token, proving
the household key was not sent). Bounds: only `sk-ant-oat` values (others are
dropped by `clean_headers`), only when the caller authenticated with
`x-litellm-api-key`, and it **cannot bypass the allow-list** (identical 403
with and without the header). Side effect: a bad token trips cooldown on the
whole model group for every consumer for about 60s.

## Placeholder `apiKey` is load-bearing (money-safety invariant)

Omitting `apiKey` is **not** credential-less:
`AnthropicModelInfo.get_api_key` is `api_key or get_secret_str("ANTHROPIC_API_KEY")`,
and the pod holds that metered key. A caller without an OAuth header would
silently bill the household. Each CR sets the non-secret
`sk-ant-oat-PLACEHOLDER-CLIENT-SENDS-ITS-OWN-TOKEN`: non-`None` (blocks the
env fallback) and `sk-ant-oat`-prefixed (takes the OAuth branch). A tokenless
caller gets Anthropic's own `401 "OAuth access token is invalid."` and spends
nothing; a real client token overrides the placeholder.

## `$0` on all seven price fields (money-safety invariant)

LiteLLM has no subscription-aware pricing; left unpriced, flat-rate traffic
would be charged to the key at metered rates. Each CR declares in
`info.extra`:

```yaml
input_cost_per_token: 0
output_cost_per_token: 0
cache_read_input_token_cost: 0
cache_read_input_token_cost_above_200k_tokens: 0
cache_creation_input_token_cost: 0
cache_creation_input_token_cost_above_1hr: 0
cache_creation_input_token_cost_above_200k_tokens: 0
```

Input/output zeros alone are **not** enough: `_resolve_builtin_model_cost_entry`
copies `_CACHE_PRICING_FIELDS` from the built-in entry onto a custom key, and
Claude Code caches almost every turn. Before the cache fields were zeroed the
key accrued ~$54 of fictional spend with `output_cost` exactly $0. The
`_above_*` tiers matter because Claude Code exceeds 200k context and caches
for over an hour. The fix applies on both `/v1/chat/completions` and
`/v1/messages`. Zero is honoured (`is not None` check).

Consequences:

- The key has **no `maxBudget`** (it could never trip) and **no
  `rpmLimit`/`tpmLimit`** (captain decision after measuring 43x/267x
  headroom). It has no local ceiling; Anthropic's subscription rate limiting
  is the real one. Never put these models on a budget-gated metered key.
- The historical ~$54 stays on the key's `spend` column and in
  `LiteLLM_SpendLogs` (rows are not repriced; they age out with 30d
  retention). Never edit or zero those records to make the number clean.
- `LiteLLMClaudeCodeSubscriptionSpendRegression` fires on
  `increase(litellm_spend_metric_total{api_key_alias="claude-code-subscription"}[1h]) > 0`.
  That counter increments per logged call, so any new nonzero means a price
  field regressed or an unpriced model joined the allow-list.

## The key's allow-list (CI-pinned)

Only the four pass-through CRs plus `embedding-local` (credential-less and
zero-priced). Never a metered route (`claude-sonnet-5-metered`,
`claude-opus-5-metered`, `claude-fable-5`) and never `auto`: that would make
subscription traffic a second door into household billing.
`scripts/ci/litellm-claude-code-subscription-test.py` asserts every
allow-listed Anthropic name carries the placeholder and all seven zeros.
None of these models may appear in a fallback chain (a fallback there would
also fail every caller without a token).

## Names: natural names are the pass-through

The pass-through CRs own the natural names; the household-metered CRs are
`claude-sonnet-5-metered` / `claude-opus-5-metered`. So an admin asking for
bare `claude-sonnet-5` without an OAuth token gets the deliberate 401; to
spend the metered account on purpose, ask for the `-metered` name with a key
entitled to it (master key, a metered-entitled virtual key, or `auto`).

Per-key `aliases` cannot solve a name collision (the allow-list runs on the
raw name first; see [governance-keys.md](governance-keys.md)). Owning the
natural name outright is the only safe shape.

## Adding a further family

1. Copy a pass-through CR; change `metadata.name`, `modelName` and
   `params.model`. Keep the placeholder `apiKey` and all seven zeros.
2. Add it to `models/kustomization.yaml`, the key's `models`, and
   `public_model_groups`.
3. Resolve the id: `GET https://api.anthropic.com/v1/models` with the pod's
   key lists ids but not which one a bare family alias means. Cross-check the
   installed CLI's baked-in catalog
   (`strings -a <claude binary> | grep -o 'latest_per_family:{[^}]*}'`); its
   values are catalog keys, and each entry's `provider_ids.first_party` is the
   wire id (Haiku's key is `claude-haiku-4-5`, its wire id
   `claude-haiku-4-5-20251001`). Strongest evidence: existing
   `LiteLLM_SpendLogs` rows where the client already requested the family
   (`model ILIKE '%<family>%'`).
4. Check every holder of the natural name first (`grep -rn` over
   `kubernetes/ docs/ scripts/ci/`, plus `LiteLLM_VerificationToken.models`
   and `LiteLLM_SpendLogs.model`). If a metered CR holds it and renaming would
   break a live consumer, stop and get a decision.
5. Native-1M families get a client `ANTHROPIC_DEFAULT_<FAMILY>_MODEL=<name>[1m]`
   in the runbook only when the catalog entry has `native_1m: true` (Haiku
   4.5 stays 200k, so its variable stays unset).
6. Never solve a family by allow-listing its metered name.

## Known limits through this proxy

- `POST /v1/messages/count_tokens` under-counts by about 30%: the proxy calls
  Anthropic with the placeholder, gets 401, and falls back to a local
  tokenizer. Auto-compact uses response `usage`, so compaction is unaffected;
  client estimates like `/context` are.
- Anthropic's `anthropic-ratelimit-unified-*` headers come back prefixed
  `llm_provider-...`, so Claude Code's usage-limit warnings likely cannot fire
  (inferred from header names, not tested).
- Clients must use the `[1m]` suffix to keep the 1M window (base URL is not
  `api.anthropic.com`); see the runbook.
