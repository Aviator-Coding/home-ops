# Complexity-tier auto-router (`auto`, decision D3)

One additive alias. A request to `auto` is classified by a local LLM into one
of four tiers and dispatched to that tier's backend. Nothing is forced
through it: direct model names keep working, and consumers opt in by asking
for `auto`. Config: `models/auto.yaml` (the whole router block rides
`spec.params.additional`) and `models/qwen3.6-35b-a3b-classifier.yaml`.

## Tiers

| Tier | Backend | Where |
|---|---|---|
| `SIMPLE` | `chat-local` | local B70, $0 |
| `MEDIUM` | `chat-local` | local B70, $0 |
| `COMPLEX` | `claude-sonnet-5-metered` | Anthropic |
| `REASONING` | `claude-opus-5-metered` | Anthropic |

- The local/cloud line between MEDIUM and COMPLEX is the whole cost policy.
  Move it by re-pointing a tier, not by editing the rubric.
- Tiers target `chat-local` (zero-priced), never `qwen3.6-35b-a3b`, whose
  synthetic demo prices would make free compute show up as real-looking spend.
- Cloud tiers target the **`-metered`** CRs. The natural `claude-*-5` names are
  credential-less subscription pass-through CRs and would 401.
- To disable the cloud tier, point all four tiers at `chat-local`;
  `ANTHROPIC_API_KEY` then simply goes unused.
- `return_raw_model_name: true` puts the serving backend in the response's
  `model` field. `session_affinity: false`: on, the first turn's tier would
  pin a whole session (one complex opener keeps a long chat on Opus).

## The classifier

```yaml
litellm_params:
  model: auto_router/complexity_router
  complexity_router_default_model: chat-local   # the fail-open pin LiteLLM reads
  complexity_router_config:
    classifier_type: llm
    classifier_llm_config:
      model: qwen3.6-35b-a3b-classifier          # LOCAL, never a cloud model
      timeout_ms: 8000
      classification_rubric: agentic
    classifier_fallback: default_model
    default_model: chat-local                    # keep aligned with the sibling
```

- **LLM, not `heuristic`**: the default rule-based scorer measured 25-35% tier
  accuracy (errors running the wrong way) against 80-85% for the LLM
  classifier.
- **`agentic` rubric**: the default `legacy` preset grades ordinary
  engineering as top tier and sends routine agent work to Opus. This is the
  single biggest cost lever.
- **Local classifier**: a cloud classifier would put a paid call in front of
  every request. The classifier sub-call is billed to the **calling** key, so
  its alias is zero-priced.
- The separate classifier deployment differs from the plain alias in three
  load-bearing ways:
  1. `extra_body.chat_template_kwargs.enable_thinking: false`. The model is a
     reasoning model; without the flag it returns empty `content`, every
     classification fails the structured-output parse, and the router fails
     open on 100% of traffic while looking healthy (measured 12/12
     unparseable without, 11/12 correct with).
  2. `num_retries: 0`: its timeout rides in front of every routed request;
     the default 2 retries would cost 3 x timeout on a wedged backend.
  3. Its own `modelName`, so classifier latency and failures are a separate
     metrics series (the alerts select on it).

## Fail open to local

On a classifier timeout, provider error or unparseable reply the request goes
to the local model **unscored** and still succeeds. `classifier_fallback:
heuristic` would rerun the weak scorer, which can still pick a cloud tier for
a request nothing classified. A broken classifier must degrade to local,
never to the invoice.

**`complexity_router_default_model` is the key LiteLLM honours.**
`init_complexity_router_deployment` reads that sibling; it does not read
`complexity_router_config.default_model`. Without the sibling, fail-open
falls back to the MEDIUM then SIMPLE tier model, so retargeting MEDIUM to a
cloud model would silently burn cloud money on every classifier failure.
Keep both keys pointing at `chat-local`.

## Classifier latency

Measured on the shared B70 with a warm prefix cache: p50 3.0s, p95 4.6s,
max 5.5s for the classifier call (full routed request p50 4.8s). Prompt
length barely matters (the ~850-token rubric is prefix-cached); GPU
contention sets the spread, and grammar-constrained JSON adds ~1s.

- `timeout_ms: 8000` is ~1.7x p95. A contended GPU still answers; a wedged
  one costs exactly one 8s round trip.
- **Cold start**: the first classification after a proxy restart can exceed
  8s and fail open (one request per restart). Do not raise the timeout for
  it; warm the router with one throwaway `auto` request after a rollout if
  needed.
- `LiteLLMRouterClassifierFailingOpen` (>20% over 15m) can fire once after a
  `litellm` or `vllm` restart on a quiet cluster, because one cold miss out of
  fewer than five calls clears 20%. Check
  `litellm_deployment_total_requests_total{requested_model="qwen3.6-35b-a3b-classifier"}`
  before treating it as real. No minimum-count guard on purpose: on a quiet
  router a classifier failing every call must still alert.
- `auto` is the wrong alias for latency-sensitive, obviously simple traffic;
  call `chat-local` directly.

## Budgets

Routed calls and the classifier sub-call bind to the caller's D4 budget. With
the local tiers and the classifier at $0, an `auto` key's `maxBudget`
measures only real USD on COMPLEX/REASONING, so a budget alert on `opencode`
or `router-demo` always means real money. `rpmLimit`/`tpmLimit` are the only
bound on local routed volume. Prove the 429 path with a COMPLEX request, or
with `demo` on `qwen3.6-35b-a3b`; local routed traffic never moves a budget.

The allow-list is checked against what the caller asked for, so a key holding
only `auto` reaches every tier but cannot call a tier backend directly.

## Observability

No custom exporter. Labels carry the decision: `requested_model` is what the
caller asked for (`auto`, or the classifier's own name for its sub-calls);
`litellm_model_name`/`model_id` is what served it. Tiers map 1:1 to backends,
so `requested_model="auto"` split by `litellm_model_name` is the per-tier
breakdown.

```promql
# tier split
sum by (litellm_model_name) (rate(litellm_deployment_success_responses_total{requested_model="auto"}[5m]))
# share that left the cluster
sum(rate(litellm_deployment_success_responses_total{requested_model="auto",litellm_model_name=~"claude-.*"}[1h]))
  / sum(rate(litellm_deployment_success_responses_total{requested_model="auto"}[1h]))
# mean classifier latency, seconds
sum(rate(litellm_llm_api_latency_metric_sum{requested_model="qwen3.6-35b-a3b-classifier"}[5m]))
  / sum(rate(litellm_llm_api_latency_metric_count{requested_model="qwen3.6-35b-a3b-classifier"}[5m]))
# fail-open rate
sum(rate(litellm_deployment_failure_responses_total{requested_model="qwen3.6-35b-a3b-classifier"}[15m]))
  / sum(rate(litellm_deployment_total_requests_total{requested_model="qwen3.6-35b-a3b-classifier"}[15m]))
```

Alerts: `LiteLLMRouterClassifierFailingOpen`, `LiteLLMRouterClassifierSlow`
(mean > 6s over 30m), `LiteLLMRouterCloudTierShare` (> 50% cloud over 1h).
Failures carry `exception_class` (`Openai.Timeout` vs
`Openai.InternalServerError`). The tier name and the fail-open `cause` are
not metrics (nested `routing_decision` cannot become a label); read them in
the pod log (`ComplexityRouter: LLM classifier failed ... falling back to`)
or the spend-log row.

## Post-merge verification

```bash
kubectl -n ai logs deploy/litellm | grep -A6 "Proxy initialized with Config"   # lists the aliases
kubectl -n ai get cm litellm-config -o jsonpath='{.data.config\.yaml}' | grep -A5 "tiers:"
KEY=$(kubectl -n ai get secret litellm-key-router-demo -o jsonpath='{.data.key}' | base64 -d)
kubectl -n ai port-forward svc/litellm 4000:4000 &
# SIMPLE -> expect "model": "chat-local"
curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"auto","max_tokens":32,"messages":[{"role":"user","content":"what is the capital of France?"}]}' | jq -r .model
# COMPLEX -> expect claude-sonnet-5-metered (spends a few real cents)
curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"auto","max_tokens":32,"messages":[{"role":"user","content":"implement a distributed token bucket rate limiter on Redis, correct under concurrency"}]}' | jq -r .model
kubectl -n ai logs deploy/litellm --since=5m | grep -i "complexityrouter\|classifier"   # no fail-open line = classified
```

`ComplexityRouter initialized` is a DEBUG line; its absence at the default
log level is not a fault.

## Tuning

Change one thing at a time; every knob moves spend.

| Symptom | Knob | Notes |
|---|---|---|
| Too much traffic reaches Anthropic | `tiers.COMPLEX: chat-local` | Moves the routing line; never remove the fail-open sibling to "follow" a tier |
| Ordinary work graded REASONING | `classification_rubric` | Must be `agentic` |
| Classifier timing out under load | `timeout_ms` | Toward 12000 at most; every second is paid by every routed request when wedged |
| Classifier slow while healthy | `system_prompt` | Replaces the rubric wholesale, including its prompt-injection paragraph; rarely helps. `system_prompt` and `classification_rubric` are mutually exclusive |
| A phrase must always hit one tier | `keyword_tier_rules` | Deterministic, evaluated before classification |
| Caller must force a stronger model | built in | `LITELLM ESCALATE` in the prompt bumps one tier |

`tier_boundaries`, dimension weights and keyword lists only affect the
heuristic scorer, which is not used. Without the built-in rubric's defense
paragraph a caller can pin themselves to Opus by asking for it.

## Version floor

`classifier_type: llm` and `classifier_llm_config` first exist in LiteLLM
v1.93.0. Below it `ComplexityRouterConfig` is `extra="allow"`: the keys parse
and are ignored, and the router silently runs the weak scorer with no error.
Floor enforced by `.renovate/overrides.json5` and
`scripts/ci/litellm-auto-router-test.py`.
