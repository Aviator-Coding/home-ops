# LiteLLM fallback chains (Phase 5)

How availability and context-window fallbacks are configured on the `ai/litellm`
governance proxy, what was measured to justify each choice, and how to prove a
failover after merge without touching the real B70 backend.

Companion docs: [`README.md`](README.md) (the governance layer itself),
[`auto-router.md`](auto-router.md) (the D3 `auto` alias).

All measurements below were taken **2026-08-26** against the live proxy
(`ghcr.io/berriai/litellm-non_root:v1.98.0`, the pinned image) on a suspended
`litellm` Flux Kustomization, using throwaway `fallback-probe*` CRs that were
deleted afterwards. Nothing in the real backend was degraded.

---

## 1. The governance result that shapes everything else

**Config-declared fallbacks bypass a virtual key's model allow-list. Caller-supplied fallbacks do not.**

This is the single most important fact about this feature, and it is why the
fallback chains below are *not* attached to `qwen3.6-35b-a3b`.

### What was measured

A throwaway key `fallback-probe-key` was minted with
`spec.models: [fallback-probe]` - one model, no cloud entitlement of any kind.
A throwaway model `fallback-probe` pointed at a dead backend
(`http://fallback-probe-dead.ai.svc.cluster.local:9/v1`, `num_retries: 0`), and
`router_settings.fallbacks` declared `fallback-probe -> claude-sonnet-5`.

| # | Request | Key allow-list | Result |
|---|---------|----------------|--------|
| A | `model: fallback-probe` (backend dead) | `[fallback-probe]` | **HTTP 200 from `anthropic/claude-sonnet-5`** |
| B | `model: fallback-probe` | `[qwen3.6-35b-a3b]` (`demo`) | HTTP 403 `key_model_access_denied` |
| C | `model: fallback-probe` + body `fallbacks: [claude-opus-5]` | `[fallback-probe]` | HTTP 403 `key_model_access_denied`, *"Tried to access claude-opus-5"* |

Test A response headers, verbatim:

```
HTTP/1.1 200 OK
x-litellm-model-name: anthropic/claude-sonnet-5
x-litellm-response-cost: 2.6e-05
x-litellm-key-spend: 2.6e-05
```

A key entitled to exactly one local-shaped model was served by a paid Anthropic
model and **billed real USD for it**, without ever naming that model.

### Why it happens

The allow-list gate (`can_key_call_model`, `litellm/proxy/auth/auth_checks.py`)
runs in the proxy's auth layer, against every model name **present in the
request**. Test C proves it also covers a caller-supplied `fallbacks` array -
that array is part of the request, so it is checked, and the 403 names the
smuggled model explicitly.

The router's own fallback chain is not part of the request. `litellm/router.py`
and everything under `litellm/router_utils/` contain **zero** references to
`user_api_key_dict`, `can_key_call_model`, or any key allow-list - by the time
`async_function_with_fallbacks` picks the fallback deployment, the calling key's
entitlements are no longer in scope. The one place LiteLLM *does* re-check a
resolved target (`can_key_call_resolved_model`) is wired to
`model_group_alias` rewrites and the realtime/auto-router endpoints
(`litellm/proxy/common_request_processing.py`), not to fallbacks.

### The rule this forces

> **A cloud fallback may only be declared on an alias whose every consumer is
> already entitled to cloud spend.**

`qwen3.6-35b-a3b` is held by `demo`, a deliberately local-only, $0.05-budget key
(captain decision D4). Putting a cloud fallback on that alias would silently
convert every local-only consumer into a cloud consumer the first time the B70
hiccupped - real spend, against budgets sized for governance-accounting play
money, on keys whose whole purpose is that they cannot reach Anthropic.

So the local model is exposed under **two aliases with different entitlements**:

| Alias | Backend | Fallbacks | Who holds it |
|---|---|---|---|
| `qwen3.6-35b-a3b` | B70 llama.cpp | **none** - terminal | local-only keys (`demo`) |
| `chat-ha` | same B70 llama.cpp | `-> claude-sonnet-5` | cloud-entitled keys only |

Same weights, same GPU, same cost profile while healthy. The only difference is
what happens when the B70 is down, and that difference is exactly the
entitlement boundary. A key that must never reach Anthropic is given
`qwen3.6-35b-a3b` and is structurally incapable of failing over; a key that is
*supposed* to survive a GPU outage is given `chat-ha` and pays for it.

This mirrors the property already proven for the `auto` alias (see
`virtualkeys/router-demo.yaml`): the allow-list is checked against the model the
**caller asked for**, never the one the router resolves to. `auto` is a
governance boundary for the same reason `chat-ha` is.

---

## 2. Axis 1 - availability fallbacks

Fires on connection errors, 5xx and other retryable provider failures - i.e.
"the backend is down or erroring", never "the request was bad".

| Primary | Fallback | Rationale |
|---|---|---|
| `chat-ha` | `claude-sonnet-5` | The cloud-entitled view of the local B70 model. |
| `auto` | `claude-sonnet-5` | Without it a B70 outage takes the routed alias down completely - see below. |

`qwen3.6-35b-a3b` and `qwen3.6-35b-a3b-classifier` deliberately have **no**
fallback and are terminal. Adding one to either would breach D4 (see §1).

### Why Sonnet and not Opus

An availability fallback is selected by **infrastructure failure, not task
difficulty**. The traffic it catches is whatever the 35B local model was already
handling - ordinary chat and agent turns - so repricing it as frontier work is
simply wrong. Sonnet is 2.5x cheaper than Opus on both input ($2/M vs $5/M) and
output ($10/M vs $25/M), and its 1,000,000-token input window is ~3.8x the local
model's 262,144, so nothing is given up in capability terms.

Opus stays reserved for the D3 router's REASONING tier, where the classifier has
actually judged the work hard. That separation is the whole point of having both
a router and a fallback chain: the router picks on *difficulty*, the fallback
chain picks on *availability*, and they must not be allowed to collapse into
each other.

### Why `auto` needs its own entry

The D3 router fails **open to local** by design: on classifier failure it routes
to `complexity_router_default_model` (`qwen3.6-35b-a3b`) without scoring. That is
exactly right when the *classifier* is broken - but when the **B70 itself** is
down, the classifier call fails *and* the fail-open target is the same dead
backend, so the whole `auto` alias dies. The `auto -> claude-sonnet-5` entry is
what turns that into a degraded-but-serving path. `auto` is cloud-entitled by
construction (its COMPLEX and REASONING tiers are Anthropic models), so this
breaches no entitlement.

---

## 3. Axis 2 - context-window fallbacks

A separate config key from availability, because they are separate failure axes
dispatched from different exception types: LiteLLM routes
`ContextWindowExceededError` down `context_window_fallbacks` and retryable
provider errors down `fallbacks`.

### The direction question - measured, and the assumption was wrong

The Phase 5 brief anticipated that this axis might not apply, on the theory that
the Anthropic models cap *lower* than our 262k local model. **They do not.**

| Model | `max_input_tokens` | Source |
|---|---|---|
| `qwen3.6-35b-a3b` (local) | **262,144** | llama.cpp `/props` on the live pod: `n_ctx` = 262144 per slot, `total_slots` = 4 with unified KV, so a single request can use the full window. Matches `n_ctx_train`. |
| `claude-sonnet-5` | **1,000,000** | LiteLLM v1.98.0 built-in cost map, `litellm_provider: anthropic` |
| `claude-opus-5` | **1,000,000** | same |

Cloud is a **~3.8x superset**, so `local -> cloud` is the correct direction and
covers a real ~738k-token band that the local model can never serve.

Cross-check that 1M is the *standard* window and not a beta/gated tier that
LiteLLM would fail to unlock: the same cost map lists `claude-sonnet-4-5` at
`max_input_tokens: 200000` **with** a premium
`input_cost_per_token_above_200k_tokens`, i.e. it models the gated-long-context
concept explicitly and prices it separately. The 5-generation entries carry
`max_input_tokens: 1000000` and **no** such premium. (LiteLLM's Anthropic
transport sends no `context-1m-*` beta header - grepped, none exists in
`llms/anthropic/` - which is consistent with 1M being ungated for these models.)

There is deliberately **no** reverse (`cloud -> local`) entry: a prompt that
overflows a 1M window cannot possibly fit in 262k, so that chain would be
guaranteed to fail its second leg.

### Trigger path: confirmed at source, end-to-end check deferred

For this axis to do anything, llama.cpp's overflow error must be classified as
`ContextWindowExceededError`. LiteLLM v1.98.0 does this explicitly -
`ExceptionCheckers.is_error_str_context_window_exceeded`
(`litellm_core_utils/exception_mapping_utils.py`) matches the substring
`"exceeds the available context size"`, annotated in-source with the comment
`# llama.cpp/Lemonade`.

**Not** proven end-to-end pre-merge, and stated plainly rather than glossed: an
attempt to overflow the live backend with a >262k-token prompt was abandoned
after llama.cpp spent 300s tokenizing the ~2.4MB payload without returning. It
rejects *after* tokenization, not before, so the check is slow and loads a GPU
that also serves real cluster traffic. The end-to-end confirmation is step 4 of
the post-merge runbook (§5), where it costs one scoped request.

---

## 4. Alerts

Six alerts in `kubernetes/apps/base/ai/litellm/app/prometheusrule.yaml`, group
`litellm.rules`. Every series and label used below was confirmed present on a
live `/metrics` scrape **and** queried back out of Prometheus.

| Alert | Severity | Expression (abridged) | Means |
|---|---|---|---|
| `LiteLLMFallbackChainExhausted` | critical | `rate(litellm_deployment_failed_fallbacks_total[10m]) > 0` | Primary *and* fallback both failed - user-visible 5xx |
| `LiteLLMSustainedFailover` | warning | `rate(litellm_deployment_successful_fallbacks_total[15m]) > 0` for 15m | Serving from cloud continuously - real spend, local backend down |
| `LiteLLMContextWindowFallbackFiring` | warning | same counter, `exception_class=~".*ContextWindowExceeded.*"` | Prompts routinely overflow the local tier |
| `LiteLLMDeploymentStuckFailing` | warning | `litellm_deployment_state >= 1` **and on(model_id)** live failure rate | Deployment unhealthy *and still failing* |
| `LiteLLMCloudProviderAuthFailing` | critical | `failure_responses{api_provider="anthropic", exception_status=~"401\|403"}` | Our Anthropic credential is being rejected |
| `LiteLLMCloudProviderQuotaExhausted` | warning | `failure_responses{api_provider="anthropic", exception_status="429"}` | Provider quota/rate limit |

Three traps these encode, each found by measurement rather than assumption:

**1. `litellm_deployment_state` is a latched gauge.** It is set to 1 on any
failure and back to 0 only on a later success
(`set_deployment_partial_outage` / `set_deployment_healthy` in
`litellm/integrations/prometheus.py`); it never self-resets and a deployment that
fails once and then goes idle stays at 1 forever. Alerting on it alone would be
a permanent false alarm. The `and on (model_id) (... rate(failure_responses) > 0)`
guard is what makes it mean *still broken*.

**2. Our own governance denials look exactly like provider auth failures if you
only match on status.** A virtual key calling outside its allow-list emits
`exception_status="403"` too - measured, with
`exception_class="ProxyException"` and an **empty** `api_provider`. Those are the
allow-list working correctly and must never page. Selecting
`api_provider="anthropic"` is what separates them. The same applies to 429: a key
hitting its own `tpmLimit` emits `exception_status="429"` with
`exception_class="HTTPException"` and no `api_provider` (observed live while
testing), which is `LiteLLMConsumerBudgetExhausted` territory, not a provider
quota event.

**3. `litellm_deployment_cooled_down_total` exists but is inert here - do not
build an alert on it.** The reference repo's "deployment out of rotation" alert
does not translate. LiteLLM exempts single-deployment model groups from
cooldown: in `router_utils/cooldown_handlers.py`, a 429 cools a deployment down
only `if ... and not is_single_deployment_model_group`, and the error-rate path
carries the same guard with the comment *"by default we should avoid cooldowns
on single deployment model groups"*. The one remaining path needs
`percent_fails == 1.0` **and** `total_requests_this_minute >=
SINGLE_DEPLOYMENT_TRAFFIC_FAILURE_THRESHOLD`, which defaults to **1000 requests
per minute**. Every model group in this proxy has exactly one deployment and this
cluster will never see 1000 rpm on one of them. Confirmed empirically: after
driving a deployment to 100% failure, `litellm_deployment_cooled_down_total` had
**zero** series. `LiteLLMDeploymentStuckFailing` covers the same operational
question using signals that do move here.
