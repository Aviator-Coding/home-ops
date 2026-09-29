# Fallback chains and their alerts

Config: `routerSettings.fallbacks` and `routerSettings.context_window_fallbacks`
on `kubernetes/apps/base/ai/litellm/app/litellmproxy.yaml` (rendered verbatim
to `router_settings`). Proof procedure: [failover-drill.md](failover-drill.md).

## The governance result that shapes everything

**A config-declared fallback bypasses the calling key's model allow-list. A
caller-supplied `fallbacks` array does not.**

Measured with a throwaway key allow-listed to one dead-backend model and a
config fallback to Sonnet: the request was served by
`anthropic/claude-sonnet-5` and billed real USD, never having named it. The
same model with a body `fallbacks: [...]` got 403. Why: the allow-list
(`can_key_call_model`) runs in the auth layer against model names **in the
request**; `litellm/router.py` and `router_utils/` never see the key's
entitlements. `can_key_call_resolved_model` only covers `model_group_alias`
rewrites and the realtime/auto-router endpoints, not fallbacks.

**Rule: a cloud fallback may only sit on an alias whose every consumer is
already cloud-entitled.** Holding such an alias IS the cloud entitlement.
Otherwise one B70 hiccup turns every local-only key into a cloud spender.

## What is declared

| Primary | `fallbacks` (availability) | `context_window_fallbacks` |
|---|---|---|
| `chat-ha` | `claude-sonnet-5-metered` | `claude-sonnet-5-metered` |
| `auto` | `claude-sonnet-5-metered` | `claude-sonnet-5-metered` |

Terminal, and must stay so (CI: `scripts/ci/litellm-fallback-chain-test.py`,
and `chat_local_has_no_cloud_fallback` in `litellm-auto-router-test.py`): `qwen3.6-35b-a3b`, `chat-local`,
`qwen3.6-35b-a3b-classifier`, `pr-review-local`, `embedding-local`, and the
four subscription pass-through models.

- Targets must be the **`-metered`** CRs. The natural `claude-sonnet-5` is the
  credential-less subscription pass-through; a fallback there would 401 every
  failed-over request.
- **Sonnet, not Opus**: an availability fallback fires on infrastructure
  failure, not difficulty, so it must not reprice ordinary local work as
  frontier work. Sonnet is 2.5x cheaper than Opus on input and output, and its
  1M window already covers the local 262,144. Opus stays for the router's
  REASONING tier (router picks on difficulty, fallback on availability).
- **`auto` needs its own entry**: when the B70 is down the classifier fails,
  the router fails open to `chat-local`, which is the same dead backend, and
  the request dies. The `auto` entry turns that into a degraded-but-serving
  path. `auto` is cloud-entitled by construction, so no entitlement is
  breached. Proven end to end: auto-router failures propagate out of the
  `auto` group and the fallback fires.
- **Context axis is separate**: LiteLLM routes `ContextWindowExceededError`
  down `context_window_fallbacks` and retryable provider errors down
  `fallbacks`. Direction is local -> cloud: cloud windows (1,000,000, ungated
  and not premium-priced for the 5-generation models) are ~3.8x the local
  262,144. No reverse entry: a prompt over 1M cannot fit 262k.
- The context trigger depends on llama.cpp's overflow message containing
  `exceeds the available context size`, which LiteLLM maps to
  `ContextWindowExceededError`. An oversized prompt is refused in about one
  second; an under-limit prompt is genuinely processed and looks like a hang.
- Every model group has exactly one deployment, so `routing_strategy`
  barely matters today; it is pinned to `simple-shuffle` so `least-busy`
  cannot creep in.

## Alerts (group `litellm.rules`, `app/prometheusrule.yaml`)

| Alert | Severity | Signal |
|---|---|---|
| `LiteLLMFallbackChainExhausted` | critical | `rate(litellm_deployment_failed_fallbacks_total[10m]) > 0`: primary and fallback both failed, caller got a 5xx |
| `LiteLLMSustainedFailover` | warning | successful fallbacks excluding `ContextWindowExceeded`, 15m: serving from cloud, real spend |
| `LiteLLMContextWindowFallbackFiring` | warning | successful fallbacks matching `ContextWindowExceeded`, 30m: a consumer routinely overflows the local window |
| `LiteLLMDeploymentStuckFailing` | warning | `litellm_deployment_state >= 1` **and on (model_id)** a live failure rate |
| `LiteLLMCloudProviderAuthFailing` | critical | failures with `api_provider="anthropic"`, status 401/403 |
| `LiteLLMCloudProviderQuotaExhausted` | warning | same with status 429 |

Traps these encode (each found by measurement):

1. **The fallback counters are sparse, not misnamed.** Both are labelled
   prometheus_client Counters that emit no series until the first fallback.
   Their absence from Prometheus is the healthy state. Siblings from the same
   factory (`litellm_deployment_cooled_down_total`,
   `litellm_llm_api_failed_requests_metric_total`) do appear when their event
   happens, which proves the naming. Do not "repair" these alerts. Metric
   names need the `_total` suffix. `LiteLLMHighFailureRate`'s
   `litellm_proxy_failed_requests_metric_total` is sparse the same way.
2. **`litellm_deployment_state` is latched**: set to 1 on any failure, back to
   0 only on a later success. Alone it would alert forever after one blip on
   an idle deployment; the `and on (model_id)` failure-rate guard makes it
   mean "still broken".
3. **Our own governance denials look like provider failures.** An allow-list
   denial is `exception_status="403"`, `exception_class="ProxyException"`,
   empty `api_provider`; a key hitting its own `tpmLimit` is `429` /
   `HTTPException`, empty `api_provider`. Selecting `api_provider="anthropic"`
   keeps these off the provider alerts.
4. **`exception_class` is provider-prefixed** (`Openai.InternalServerError`,
   `Openai.ContextWindowExceededError`), so the axis split uses regex. Without
   the exclusion, prompt overflow would raise the availability alert with the
   wrong remediation.
5. **Do not alert on `litellm_deployment_cooled_down_total`.** LiteLLM exempts
   single-deployment groups from cooldown (the remaining path needs 100%
   failure at >= 1000 rpm). Every group here has one deployment, so it is
   inert; `LiteLLMDeploymentStuckFailing` covers the question.

## Operational note

A bad client OAuth token on an Anthropic model can cool the whole model group
down for every consumer for about 60s (429 "No deployments available").
Check that before blaming a provider outage
([claude-code-subscription.md](claude-code-subscription.md)).
