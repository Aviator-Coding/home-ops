# LiteLLM complexity-tier auto-router

Captain decision **D3** (2026-08-26): *"yes build reference complexity tier why not"*.

One extra model alias on the LiteLLM governance proxy, `auto`. A request sent to it
is classified by an LLM into one of four complexity tiers and dispatched to the
backend for that tier: cheap work stays on the local B70, genuinely hard work goes
to Anthropic. Everything else about the proxy is unchanged (Phase 5 availability
fallbacks on `auto`/`chat-ha` are documented in [`fallbacks.md`](fallbacks.md),
not here).

Config lives in the `LiteLLMModel` CRs under
[`kubernetes/apps/base/ai/litellm/app/models/`](../../../kubernetes/apps/base/ai/litellm/app/models/) -
the router block itself is [`models/auto.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/auto.yaml),
the classifier deployment is [`models/qwen3.6-35b-a3b-classifier.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/qwen3.6-35b-a3b-classifier.yaml).
Since captain decision O1 (2026-08-26) the home-operations litellm-operator
renders these into the proxy's `config.yaml`; the rendered content is
semantically identical to the hand-written file that preceded it.
The governance layer it sits inside is documented in [`README.md`](README.md).

> **Reading the YAML snippets below.** They show the `litellm_params` block as
> LiteLLM finally sees it, which is what every semantic claim on this page is
> about. In Git that block is one level down: `LiteLLMModel.spec.params` for the
> typed keys (`model`, `apiBase`, `apiKey`) and `spec.params.additional` for
> everything else - the operator merges `additional` verbatim into
> `litellm_params`, so `complexity_router_default_model` and
> `complexity_router_config` both live there on `models/auto.yaml`. Nothing on
> this page changes shape; only where you type it does.

## It is additive, on purpose

`qwen3.6-35b-a3b` still resolves to the local model exactly as it did before D3.
Nothing is forced through the router. A consumer opts in by asking for `auto`.

| Alias | What it does |
|---|---|
| `qwen3.6-35b-a3b` | Local llama.cpp chat model. Pre-D3 behaviour, byte-identical. **Synthetically priced** - see below. |
| `chat-local` | Same backend, priced at zero. The tier target real traffic runs on. |
| `qwen3.6-35b-a3b-classifier` | Same backend, thinking disabled. Used *by* the router. |
| `claude-sonnet-5`, `claude-opus-5` | Anthropic, reachable directly too. |
| `auto` | **The router.** Classify, then dispatch to one of the four tiers. |

Phase 5 also exposes the local backend as `chat-ha` (cloud-entitled fallback
view). That alias is not part of the router and is owned by
[`fallbacks.md`](fallbacks.md).

### Why the local tiers point at `chat-local`, not `qwen3.6-35b-a3b`

Four aliases now resolve to the same llama.cpp server on the same B70. They
differ only in properties the others must not carry, and the one that matters
here is **price**.

`qwen3.6-35b-a3b` carries deliberately inflated `model_info` prices
(`input_cost_per_token: 5e-05`, `output 1e-04`). They are not a billing
estimate - nothing about local inference costs money - they exist so the `demo`
virtual key's `$0.05` cap can be exhausted by a single smoke test, which is what
makes the D4 budget path provable rather than theoretical. That test design is
captain-approved and stays.

Until 2026-08-27 the router's SIMPLE and MEDIUM tiers, its `default_model` and
its fail-open pin all pointed at that same priced alias, so **real traffic paid
play money**. Measured live on the day of the fix: one `say ok` request through
`auto`, classified SIMPLE and served entirely by the local B70, recorded
**$0.04735** of spend - 9.5% of `router-demo`'s whole $0.50 monthly budget - and
the `repo-wiki` consumer had accrued **$7.82** of its $30 cap without spending a
real cent. Every spend dashboard and every budget alert was reporting
real-looking dollars for free compute.

[`models/chat-local.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/chat-local.yaml)
is the same backend with **no prices at all**, and it is what the local tiers
target now. `qwen3.6-35b-a3b` is unchanged and its only remaining consumer is
the `demo` budget test.

`chat-local` is also **terminal**: it appears in neither `fallbacks` nor
`context_window_fallbacks`, so it is structurally incapable of reaching a paid
API, exactly like `qwen3.6-35b-a3b`. That is not a detail - a config-declared
fallback bypasses the calling key's allow-list ([`fallbacks.md`](fallbacks.md#1)),
so adding one here would silently make every local-only consumer a cloud
spender. Consumers that *want* cloud failover hold `chat-ha` instead.

## Tiers

Tier names and criteria are LiteLLM's built-in taxonomy; only the mapping is ours.

| Tier | Upstream criteria (abridged) | Our backend | Where it runs |
|---|---|---|---|
| `SIMPLE` | greetings, chitchat, factual lookups with a short known answer | `chat-local` | local B70, $0 |
| `MEDIUM` | everyday requests needing some explanation, light reasoning, minor code | `chat-local` | local B70, $0 |
| `COMPLEX` | non-trivial code, architecture, multi-step technical work, domain depth | `claude-sonnet-5` | Anthropic |
| `REASONING` | open-ended analysis, proofs, hard problems, tradeoffs | `claude-opus-5` | Anthropic |

The local/cloud boundary sits between `MEDIUM` and `COMPLEX`. That single line is
the whole cost policy: move it by re-pointing a tier, not by editing the rubric.

**To disable the cloud tier entirely**, point all four tiers at `chat-local`.
The router keeps working, `ANTHROPIC_API_KEY` simply goes unused, and no other
file needs to change. Use `chat-local`, not `qwen3.6-35b-a3b`: the latter would
work, but would start charging synthetic dollars to every consumer again.

## The classifier

```yaml
litellm_params:
  model: auto_router/complexity_router
  complexity_router_default_model: chat-local  # fail-open pin LiteLLM reads
  complexity_router_config:
    classifier_type: llm
    classifier_llm_config:
      model: qwen3.6-35b-a3b-classifier   # LOCAL. Never a cloud model.
      timeout_ms: 8000
      classification_rubric: agentic
    classifier_fallback: default_model
    default_model: chat-local             # keep aligned with the sibling above
```

**Local, so classification is free** - and since 2026-08-27 that is true in
recorded spend too, not just in real dollars: the classifier alias carries no
prices. Every routed request costs one extra completion against the B70 and
zero cloud spend. Pointing
`classifier_llm_config.model` at a cloud model would put a paid API call in
front of every request including the ones that were going to stay local, which
defeats the entire point.

**LLM, not the rule-based scorer.** `classifier_type` defaults to `heuristic`,
LiteLLM's local weighted-keyword scorer. The reference implementation A/B-tested
both and measured the scorer at 25-35% tier accuracy against 80-85% for the LLM
classifier, with the scorer's errors running the *wrong way* (COMPLEX scoring
below MEDIUM). We skip that step entirely. Our own measurement below is 15/16.

**`agentic` rubric, not the default.** LiteLLM's default preset is `legacy`,
which carries no calibration examples. Its tier prose puts "non-trivial code,
multi-step technical work" at the top of the scale - which is the *median*
request in agent traffic, so ordinary engineering grades as top-tier and the
router pays Opus prices for routine work. `agentic` is the preset calibrated on
terminal and coding tasks. Upstream keeps `legacy` as the default purely so
upgrading never silently moves an existing router's bill.

### Why a separate classifier deployment

`qwen3.6-35b-a3b-classifier` points at the same llama.cpp server as
`qwen3.6-35b-a3b`. Three differences, all load-bearing:

1. **`extra_body.chat_template_kwargs.enable_thinking: false`** - the one that
   makes the feature work at all. Qwen3.6-35B-A3B is a reasoning model: left to
   its default it emits ~1300 tokens of `reasoning_content` and returns an
   **empty** `content`, so LiteLLM's structured-output parse raises
   *"LLM classifier returned empty content"* and **every single classification
   fails**. The router then fails open on 100% of traffic - it looks healthy,
   serves every request, and never routes anything to a cloud tier.
   Measured 2026-08-26 on the live B70: 12/12 classifications unparseable with
   the flag absent, 11/12 correct with it set. Other spellings were tried and do
   **not** work on this llama.cpp build: top-level `reasoning_effort: none`,
   `chat_template_kwargs.thinking`, `chat_template_kwargs.reasoning_effort`.
2. **`num_retries: 0`** - the classifier's timeout budget rides in front of every
   routed request. At the router default of 2 retries a wedged backend costs
   `3 x timeout_ms` before failing open; at 0 it costs exactly one round trip.
3. **Its own `model_name`** - which is what makes classifier latency, spend and
   failures separable from ordinary local chat traffic in `/metrics`. See
   [Observability](#observability).

### Fail open to local

```yaml
litellm_params:
  complexity_router_default_model: chat-local
  complexity_router_config:
    classifier_fallback: default_model
    default_model: chat-local
```

On a classifier timeout, provider error or unparseable reply, the request is
routed to the local model **without being scored**, and still succeeds. The
alternative, `classifier_fallback: heuristic`, reruns the 25%-accurate keyword
scorer, which can still return COMPLEX and spend real money on a request that
nothing successfully classified. A broken classifier must degrade to the local
model, never to the invoice.

**`complexity_router_default_model` is the pin LiteLLM honours.**
`init_complexity_router_deployment` resolves the constructor `default_model`
from that sibling under `litellm_params` first; it does **not** read
`complexity_router_config.default_model`. Without the sibling it falls back to
the MEDIUM, then SIMPLE, tier model, and ComplexityRouter overwrites
`config.default_model` with that constructor value. Keep both keys pointing at
the local model. Re-pointing MEDIUM or SIMPLE at a cloud model does **not** move
the fail-open target while the sibling is set; removing the sibling would make
fail-open follow MEDIUM — the silent cloud-spend hazard if that tier is later
retargeted.

This is the one place our config deliberately reaches the same value as the
reference implementation for a different reason: they chose `default_model`
because their classifier used a non-complexity taxonomy, we choose it because
`heuristic` is a cloud-spend hazard.

Verified live 2026-08-26: with `timeout_ms` set below the classifier's real
latency, every routed request logged
`ComplexityRouter: LLM classifier failed (litellm.Timeout ...), falling back to
default_model` and returned HTTP 200 from the local model.

### Measured classifier latency

Measured 2026-08-26 against the live `vllm-app` B70 while it also served normal
cluster traffic, warm llama.cpp prefix cache (847/875 prompt tokens cached), the
exact rubric and `json_schema` response format LiteLLM sends:

| | classifier call alone | full routed request (classify + completion) |
|---|---|---|
| p50 | **3.0 s** | 4.8 s |
| p95 | **4.6 s** | 6.7 s |
| max | 5.5 s | 6.7 s |
| min | 1.9 s | 3.3 s |

**This is not fast, and it rides every routed request before a single token of
the answer is produced.** Two things dominate, and neither is prompt length:
the ~850-token rubric prefix is prompt-cached (a deliberately shortened system
prompt measured 3085 ms against 3068 ms for the full one, i.e. no difference),
and the B70 is shared with the rest of the `ai` namespace, which is where the
1.9 s - 5.5 s spread comes from. The grammar-constrained JSON decode costs about
1 s over free-text generation.

`timeout_ms: 8000` is ~1.7x measured p95 and above the observed max: a merely
contended GPU still answers instead of being failed open, while a wedged one
adds exactly one 8 s round trip (`num_retries: 0`) rather than the reference
implementation's 20 s x 3.

**Cold start is the one case that reliably exceeds it.** Measured in the
post-merge verification on 2026-08-26: the *first* classification after the
proxy started took **8.22s against the 8000ms timeout** and failed open, so the
first routed request after a restart is served by the local model without being
classified. Warm calls in the same session then averaged 2.14s with no further
fail-opens. The cause is the ~850-token rubric prefix being prefilled against a
cold llama.cpp prompt cache plus slot warm-up, and it costs one request per
restart.

Do **not** raise `timeout_ms` to absorb it. The failure direction is already the
safe one - local, no cloud spend, request still served - and a higher ceiling
would lengthen the stall on *every* routed request during a genuinely wedged
backend in order to buy back a single request per restart. If a restart must not
cost even that one classification, warm the classifier deliberately instead: send
one throwaway request to `auto` after a rollout, before real traffic arrives.

Note this interacts with `LiteLLMRouterClassifierFailingOpen`, which fires above
a 20% fail-open rate over 15m. A single cold-start miss clears that threshold
whenever **fewer than 5** classifier calls land in the same window - 1-in-4 is
25%, 1-in-2 is 50% - and on a quiet cluster right after a rollout that is the
normal case, with `for: 15m` satisfied simply because no further traffic arrives
to dilute it. So a lone warning shortly after a `litellm` or `vllm` restart is
expected and self-clears once traffic resumes; check it against
`litellm_deployment_total_requests_total{requested_model="qwen3.6-35b-a3b-classifier"}`
before treating it as real. Deliberately not "fixed" with a `min` request-count
guard: on a genuinely low-traffic router, a classifier that fails every call is
exactly what this alert should still catch.

**Consequence for consumers**: `auto` is the wrong alias for latency-sensitive,
obviously-simple traffic. Such a consumer should call `chat-local` directly -
which is exactly why the router was built additive. (Call `chat-local`, not
`qwen3.6-35b-a3b`: the latter is the synthetically-priced demo alias.)

### Measured accuracy

16 routed requests over 8 prompts drawn from LiteLLM's own `agentic` calibration
set and the reference repo's examples, 2026-08-26, live B70:

| Expected tier | Routed to | Correct |
|---|---|---|
| SIMPLE x4 | local `chat-local` | 4/4 |
| MEDIUM x4 | local `chat-local` | 4/4 |
| COMPLEX x4 | `claude-sonnet-5` | 4/4 |
| REASONING x4 | `claude-opus-5` | 4/4 |

16/16 on the routing decision that matters (local vs cloud). A separate
tier-exact run over 12 single prompts scored 11/12; the one miss graded
*"write a regex for a US phone number"* SIMPLE rather than MEDIUM, which costs
nothing because both tiers are the same local backend.

Treat these as a smoke test, not a benchmark: the prompts come from the
calibration set the rubric was written against, so they flatter it.

## Budgets

Routed calls bind to the same D4 per-consumer budgets as direct calls, and so do
the classifier's own sub-calls. LiteLLM forwards the caller's identity metadata
onto the classifier sub-call precisely so that spend "lands on the same
key/team/org/user as the request that caused it".

Verified end to end 2026-08-26 on the lab rig: a key with `max_budget: 0.01`
calling only `auto` served four routed requests, accrued spend from both the
classifier and the tier backend, and the fifth returned
**HTTP 429 `BudgetExceededError` - "Budget has been exceeded!"**. That
rejection is also visible as
`litellm_proxy_failed_requests_metric_total{requested_model="auto",exception_class="BudgetExceededError"}`.

**The allow-list is checked against what the caller asked for, not what the
router resolved to.** A key scoped to `models: ["auto"]` routes to every tier
including `claude-opus-5`, and can still run the classifier, but cannot call
`claude-opus-5` *directly*. That makes `auto` a genuine governance boundary, and
it is why the `router-demo`
[`LiteLLMVirtualKey`](../../../kubernetes/apps/base/ai/litellm/app/virtualkeys/router-demo.yaml)
lists only the alias.

### What a routed request actually costs

**A routed request that stays local costs $0.** Both of its components are local
inference on our own B70, and since 2026-08-27 both are priced at zero:

| Component | Priced at |
|---|---|
| The tier completion (`SIMPLE`/`MEDIUM` -> `chat-local`) | **$0** |
| The classifier sub-call (`qwen3.6-35b-a3b-classifier`) | **$0** |

Zeroing the classifier was the less obvious half, and it mattered more than the
tiers did. LiteLLM bills the classifier sub-call to the **calling** key - it
forwards the caller's identity metadata precisely so spend lands on the key that
caused it - so while that alias carried `qwen3.6-35b-a3b`'s synthetic prices it
was the dominant term in an `auto` consumer's recorded spend. Measured
2026-08-27: one `say ok` through `auto` recorded **$0.04735** on a cold prompt
cache, of which the completion was only ~$0.0022; the classifier prefill was
~95%. After the tiers moved to `chat-local` but before the classifier was
zeroed, the same request still recorded **$0.003** - and *all* of it was
classifier.

(The size of that term depended entirely on llama.cpp's prompt cache: cached
input tokens price at $0 because no `cache_read_input_token_cost` is set, so the
~850-token rubric prefix was free once warm and a warm classification settled to
~$0.0034. That is now moot, but it explains the spread in the old numbers.)

**Consequence for budgets.** An `auto` key's `maxBudget` now measures exactly
one thing: real USD spent on the COMPLEX/REASONING cloud tiers. A budget alert
on `opencode` or `router-demo` therefore always means real money, never local
volume - the same property the `chat-local`/`chat-ha` split gives direct
consumers. The accepted tradeoff is that **`rpmLimit`/`tpmLimit` are the only
volume bound left on a routed key**, since purely local traffic can no longer
move a budget at all. Size those, not the budget, when bounding a local workload.

Metrics are unaffected: the separate `requested_model="qwen3.6-35b-a3b-classifier"`
series comes from the alias name, not from prices, so classifier latency,
request counts and failure rates all still split out exactly as the dashboard
queries below assume. Only `litellm_spend_metric_total` for that series is now
0, which is the correct number for free local inference.

## Observability

No custom exporter and no new scrape target: the existing
[`servicemonitor.yaml`](../../../kubernetes/apps/base/ai/litellm/app/servicemonitor.yaml)
already covers this. The labels do the work, and all of the below were read off
a real v1.98.0 `/metrics` scrape on 2026-08-26.

Two labels carry the routing decision:

- `requested_model` - the alias the **caller** asked for (`auto`, or
  `qwen3.6-35b-a3b-classifier` for the router's own sub-calls).
- `litellm_model_name` / `model_id` - the deployment that **actually served** it.

Because our tiers map 1:1 onto backends, `requested_model="auto"` split by
`litellm_model_name` *is* the per-tier breakdown.

### Dashboard-ready queries

```promql
# Routing decisions per second, by tier backend (the tier split)
sum by (litellm_model_name) (
  rate(litellm_deployment_success_responses_total{requested_model="auto"}[5m]))

# Share of routed traffic that left the cluster
sum(rate(litellm_deployment_success_responses_total{requested_model="auto",litellm_model_name=~"claude-.*"}[1h]))
  / sum(rate(litellm_deployment_success_responses_total{requested_model="auto"}[1h]))

# Mean classifier latency, seconds (the tax on every routed request)
sum(rate(litellm_llm_api_latency_metric_sum{requested_model="qwen3.6-35b-a3b-classifier"}[5m]))
  / sum(rate(litellm_llm_api_latency_metric_count{requested_model="qwen3.6-35b-a3b-classifier"}[5m]))

# Fail-open rate: classifications that errored or timed out
sum(rate(litellm_deployment_failure_responses_total{requested_model="qwen3.6-35b-a3b-classifier"}[15m]))
  / sum(rate(litellm_deployment_total_requests_total{requested_model="qwen3.6-35b-a3b-classifier"}[15m]))

# Spend split: classifier vs local tier vs cloud tiers, by consumer
sum by (api_key_alias, requested_model, model) (
  rate(litellm_spend_metric_total{requested_model=~"auto|qwen3.6-35b-a3b-classifier"}[1h]))
```

`litellm_deployment_failure_responses_total` carries `exception_class`, so a
classifier timeout is distinguishable from a backend 500:
`exception_class="Openai.Timeout"` (`exception_status="408"`) vs
`Openai.InternalServerError`.

Alerts on all three of these live in
[`prometheusrule.yaml`](../../../kubernetes/apps/base/ai/litellm/app/prometheusrule.yaml):
`LiteLLMRouterClassifierFailingOpen`, `LiteLLMRouterClassifierSlow`,
`LiteLLMRouterCloudTierShare`.

**Not available as a metric**: the tier *name* and the classifier's `cause`
(`llm_classifier` vs `default_model_fallback`). LiteLLM records those on the
`StandardLoggingPayload`'s `routing_decision`, which the Prometheus integration
does not promote to a label - `custom_prometheus_metadata_labels` only reaches
scalar metadata fields, and `routing_decision` is a nested dict. The tier is
recoverable from the backend it selected, which is what the queries above do.
Per-request `cause` is visible in the pod log
(`ComplexityRouter: LLM classifier failed ... falling back to`) and in the
spend-log row in Postgres.

Note `/metrics` 307-redirects to `/metrics/`; Prometheus follows scrape
redirects by default, so the ServiceMonitor's `path: /metrics` is fine.

## Post-merge verification

Everything above was measured pre-merge against the live `vllm-app` backend
using a local LiteLLM v1.98.0 rig, because the in-cluster proxy could not start:
the 1Password item **`litellm`** that
[`../../../kubernetes/apps/base/ai/litellm/README.md`](../../../kubernetes/apps/base/ai/litellm/README.md)
lists as a prerequisite did not exist yet, so the `ExternalSecret` was
`SecretSyncedError` and the pod sat in `Init:CreateContainerConfigError`. That
is a prerequisite of the base governance layer, not of the router.

Once that item exists and Flux has reconciled, run this to demonstrate the
router end to end in-cluster. It is the same shape as the base layer's
[verification runbook](README.md#verification-runbook), extended with the two
routing demonstrations.

```bash
export KUBECONFIG=...                       # read-only is enough for steps 1-2

# 1. The prerequisite is met and the proxy is actually up.
kubectl -n ai get externalsecret litellm    # expect SecretSynced / Ready=True
kubectl -n ai rollout status deploy/litellm --timeout=5m

# 2. The router alias and its backends are registered.
kubectl -n ai logs deploy/litellm | grep -A6 "Proxy initialized with Config"
#    expect: chat-local, qwen3.6-35b-a3b, qwen3.6-35b-a3b-classifier,
#            claude-sonnet-5, claude-opus-5, auto
#    This list prints at the DEFAULT log level and is the reliable check.
#    `grep "ComplexityRouter initialized"` (which prints the four tier -> backend
#    pairs) is a DEBUG-level line: it matches nothing unless the pod runs with
#    LITELLM_LOG=DEBUG, so its absence here is not a fault. To see the tier map
#    without changing log level, read it back from the ConfigMap litellm-operator
#    renders from the LiteLLMModel CRs (name = <proxy.Name>-config, not the
#    pre-O1 kustomize configMapGenerator name litellm-configmap):
kubectl -n ai get cm litellm-config -o jsonpath='{.data.config\.yaml}' \
  | grep -A5 "tiers:"

# 3. Reach the proxy (no route by design - B4) with the router consumer key.
kubectl -n ai port-forward svc/litellm 4000:4000 &
KEY=$(kubectl -n ai get secret litellm-key-router-demo \
        -o jsonpath='{.data.key}' | base64 -d)

# 4. SIMPLE prompt -> must stay LOCAL.
curl -sS -D /tmp/simple.h -X POST localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"auto","max_tokens":32,
       "messages":[{"role":"user","content":"what is the capital of France?"}]}' \
  | jq -r .model
grep -i 'x-litellm-model-id\|x-litellm-response-cost' /tmp/simple.h
#    expect `model` = chat-local (return_raw_model_name: true), and the
#    model-id to match the local deployment in `curl localhost:4000/model/info`

# 5. COMPLEX prompt -> must reach the CLOUD tier.
curl -sS -D /tmp/complex.h -X POST localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"auto","max_tokens":32,
       "messages":[{"role":"user","content":"implement a distributed token bucket rate limiter on Redis, correct under concurrency"}]}' \
  | jq -r .model
#    expect `model` = claude-sonnet-5

# 6. The classifier decision itself, per request.
kubectl -n ai logs deploy/litellm --since=5m | grep -i "complexityrouter\|classifier"
#    a fail-open would read: "LLM classifier failed (...), falling back to
#    default_model". Absence of that line on steps 4-5 IS the evidence the
#    classifier decided both.

# 7. Metrics carry the per-tier split (the dashboard queries above).
curl -sSL localhost:4000/metrics -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  | grep 'litellm_deployment_success_responses_total.*requested_model="auto"'
#    expect one series per tier backend that served traffic, plus a separate
#    requested_model="qwen3.6-35b-a3b-classifier" series for the classifier.

# 8. Budgets bind on the routed path. Repeat step 5 until the router-demo key's
#    $0.50 is spent (or lower maxBudget in virtualkeys/router-demo.yaml first
#    for a fast repro - it is a decimal STRING) - expect HTTP 429 "Budget has been exceeded!".
```

Step 5 spends real money at Anthropic list price. It is a handful of cents at
`max_tokens: 32`, and the `router-demo` budget caps the blast radius.

## Tuning

Change one thing at a time and re-run the accuracy check - every knob below
moves spend.

| Symptom | Knob | Notes |
|---|---|---|
| Too much traffic reaching Anthropic | `tiers.COMPLEX: chat-local` | Moves the local/cloud *routing* line up a tier. Blunt, immediate, free. Does **not** move fail-open: that stays on `complexity_router_default_model` (and the aligned in-config `default_model`). Never remove that sibling to "follow" a retargeted MEDIUM/SIMPLE — without it fail-open falls back to MEDIUM and can silently burn cloud spend. |
| Ordinary engineering graded as REASONING | `classification_rubric` | Confirm it is `agentic`, not `legacy`. This is the single biggest lever on cost. |
| Classifier timing out under load | `classifier_llm_config.timeout_ms` | Raise toward 12000. Every extra second is paid by every routed request when the backend is wedged. |
| Classifier too slow even when healthy | `classifier_llm_config.system_prompt` | Replaces the rubric wholesale. Measured: prompt length barely affects latency here (prefix cache), so this rarely helps and it discards the calibration examples and the prompt-injection defense paragraph. Prefer routing latency-sensitive consumers around `auto` instead. |
| One specific phrase must always go to a given tier | `keyword_tier_rules` | Deterministic override, evaluated before classification. Cheaper than teaching the rubric. |
| A caller needs to force a stronger model | (already on) | The literal string `LITELLM ESCALATE` in the prompt bumps one tier. Callers can force *stronger*, never *which*. |
| Multi-turn sessions flip-flopping between tiers | `session_affinity: true` | Off by default here. On, the FIRST turn's tier pins the whole session, so one complex opener holds a long conversation on a paid model. |

Tier boundaries (`tier_boundaries`), dimension weights and keyword lists exist
in the schema but only affect the **heuristic** scorer, which we do not use.
Editing them changes nothing while `classifier_type: llm`.

To rewrite the classifier prompt safely: set
`classifier_llm_config.system_prompt`, remembering it replaces the built-in
rubric *entirely* - including the paragraph that tells the classifier to ignore
tier requests embedded in the caller's own system prompt. Without it, a caller
can pin themselves to `claude-opus-5` by asking for it. `system_prompt` and
`classification_rubric` are mutually exclusive.

## Version floor

`classifier_type: llm` and `classifier_llm_config` first exist in LiteLLM
**v1.93.0**. Below that, `ComplexityRouterConfig` is a pydantic model with
`extra="allow"`: the keys parse without complaint and are then **ignored**, so
the router silently runs the rule-based scorer. There is no error and no warning.
The image was moved from `main-v1.83.14-stable` to `v1.98.0` in the same change
that added this router (upstream also dropped the `main-vX.Y.Z-stable` tag
scheme after v1.81.x). Do not let Renovate float the tag below v1.93.0.

## What the reference does differently

Reference: `joryirving/home-ops` at `3d3b7008`, `kubernetes/apps/base/llm/litellm/models/auto.yaml`.

| | Reference | Here |
|---|---|---|
| Classifier model | dedicated second GPU box (`llama-strix`) | the same B70 as chat traffic, via a second alias |
| `timeout_ms` | 20000 (x3 retries) | 8000, `num_retries: 0` |
| `classification_rubric` | not set, so `legacy` | `agentic` |
| `session_affinity` | `true`, 1h | `false` |
| SIMPLE / MEDIUM | different backends | both local |
| Spend budgets | none anywhere in their LiteLLM config | D4 per-consumer budgets, enforced on routed calls |
| Thinking-model handling | not applicable (their classifier model is not one) | `enable_thinking: false`, mandatory here |

Their config also predates `classification_rubric` existing, and they run
v1.98.0, so their live router is on the uncalibrated `legacy` rubric.
