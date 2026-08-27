# LiteLLM fallback chains (Phase 5)

How availability and context-window fallbacks are configured on the `ai/litellm`
governance proxy, what was measured to justify each choice, and how to prove a
failover after merge without touching the real B70 backend.

Companion docs: [`README.md`](README.md) (the governance layer itself),
[`auto-router.md`](auto-router.md) (the D3 `auto` alias).

Measurements here come from two passes against the live proxy
(`ghcr.io/berriai/litellm-non_root:v1.98.0`, the pinned image), both on a
suspended `litellm` Flux Kustomization using throwaway probe CRs that were
deleted afterwards: the pre-merge design pass on **2026-08-26**, and the
post-merge runbook execution on **2026-08-27** (§7). Nothing in the real backend
was degraded in either.

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

### The shipped shape, verified end-to-end

Re-checked 2026-08-26 with the actual committed manifests applied to the live
cluster (Flux suspended), driven **through the internal gateway** rather than a
port-forward, then reverted:

| Request | Key | Result |
|---|---|---|
| `GET /v1/models` | `ha-demo` | only `chat-ha` listed |
| `chat-ha` | `demo` | **403** - *"can only access models=['qwen3.6-35b-a3b']"* |
| `claude-sonnet-5` | `demo` | **403** - same |
| `chat-ha`, B70 healthy | `ha-demo` | 200, served by `openai/qwen3.6-35b-a3b`, **no `x-litellm-response-cost` header at all** |
| `GET /v1/models`, no key | - | **401** |

The last two rows are the design working: `demo` cannot reach the
fallback-carrying alias at all, so no availability fallback can ever carry it to
Anthropic; and a healthy `chat-ha` request costs literally nothing, confirming
that a `ha-demo` budget measures cloud-fallback spend and nothing else.

Total real cloud spend across every test in this document: **$0.000026** (one
`max_tokens: 1` Sonnet completion, Test A). Every other cloud attempt was either
a 403 or deliberately retargeted at the local model.

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

**Verification status: PROVEN end-to-end (2026-08-27, post-merge).** This was
shipped as reasoning-only and has since been measured, so the reasoning above is
confirmed rather than assumed. Runbook step 3b was executed against the live
proxy: an `auto-probe` clone of this alias, with all four tiers **and** the
classifier pointed at a dead backend, returned `served by: qwen3.6-35b-a3b` -
i.e. the auto-router's failure did propagate out of the `auto` model group and
its fallback fired, exactly as the control-flow argument predicted. Auto-router
failures are not swallowed internally, and the `auto:` entries in
`routerSettings` are load-bearing rather than decorative.

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

### Trigger path: confirmed at source AND end-to-end

For this axis to do anything, llama.cpp's overflow error must be classified as
`ContextWindowExceededError`. LiteLLM v1.98.0 does this explicitly -
`ExceptionCheckers.is_error_str_context_window_exceeded`
(`litellm_core_utils/exception_mapping_utils.py`) matches the substring
`"exceeds the available context size"`, annotated in-source with the comment
`# llama.cpp/Lemonade`.

**Proven end-to-end 2026-08-27 (post-merge), and the pre-merge caveat this
paragraph used to carry was itself wrong.** It claimed the check was too slow to
run because llama.cpp "rejects after tokenization"; the real explanation was
that the abandoned attempt used a ~240k-token prompt, which is *under* the
262,144 limit, so the backend was legitimately generating rather than rejecting.
A genuinely oversized prompt is refused in **about one second**:

```
HTTP 400  {"error":{"code":400,
  "message":"request (400010 tokens) exceeds the available context size (262144 tokens), try increasing it",
  "type":"exceed_context_size_error","n_prompt_tokens":400010,"n_ctx":262144}}
```

That message contains the matched substring, and the full dispatch was then
observed through the proxy - LiteLLM raised
`litellm.ContextWindowExceededError` and routed down the **context** chain, not
the availability one:

```
litellm.ContextWindowExceededError: ... exceeds the available context size (262144 tokens) ...
Received Model Group=ctx-probe
Error doing the fallback: ... Received Model Group=<context fallback target>
```

So `context_window_fallbacks` genuinely fires on local overflow, and the
`chat-ha -> claude-sonnet-5` chain is real: 400,010 tokens exceeds the local
262,144 window but fits Sonnet's 1,000,000 (§3), which is the whole point of the
direction chosen here. The proof used a throwaway `ctx-probe` alias with a dead
fallback target so the oversized prompt was never billed to a cloud model.

---

## 4. Alerts

Six alerts in `kubernetes/apps/base/ai/litellm/app/prometheusrule.yaml`, group
`litellm.rules`. Every series and label used below was confirmed present on a
live `/metrics` scrape **and** queried back out of Prometheus.

| Alert | Severity | Expression (abridged) | Means |
|---|---|---|---|
| `LiteLLMFallbackChainExhausted` | critical | `rate(litellm_deployment_failed_fallbacks_total[10m]) > 0` | Primary *and* fallback both failed - user-visible 5xx |
| `LiteLLMSustainedFailover` | warning | `rate(litellm_deployment_successful_fallbacks_total{exception_class!~".*ContextWindowExceeded.*"}[15m]) > 0` for 15m | Availability failover only - serving from cloud continuously (real spend, local backend down); context-window events go to the alert below |
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

---

## 5. Post-merge failover proof (runbook)

**Never degrade `vllm-app` to force a failover.** It serves real cluster traffic
and a restart costs minutes of GGUF reload. The proof instead introduces a
throwaway model whose backend is a black hole, exercises it with a throwaway
key, and deletes both. This is exactly the procedure used to produce every
measurement in this document.

Run it after merge, on firstmate's go. Budget: a few cents at most; the version
below spends **$0** because the fallback target is local.

```bash
export KUBECONFIG=...            # cluster admin
kubectl -n ai get ks 2>/dev/null # sanity: you are on the right cluster

# 0. Suspend so Flux cannot clobber the probe mid-test, and so the probe
#    cannot outlive the test. (CLAUDE.md: a Kustomization's own interval will
#    silently revert live edits.)
flux suspend ks litellm -n ai
```

**1. Create the probe model and a key scoped to it only.**

```bash
kubectl apply -f - <<'EOF'
---
apiVersion: litellm.home-operations.com/v1alpha1
kind: LiteLLMModel
metadata: {name: fallback-probe, namespace: ai}
spec:
  modelName: fallback-probe
  proxyRef: litellm
  params:
    model: openai/probe-dead
    apiBase: http://fallback-probe-dead.ai.svc.cluster.local:9/v1   # nothing listens; port 9 = discard
    apiKey: "not-needed"
    additional: {num_retries: 0, timeout: 5}
---
apiVersion: litellm.home-operations.com/v1alpha1
kind: LiteLLMVirtualKey
metadata: {name: fallback-probe-key, namespace: ai}
spec:
  proxyRef: litellm
  keyAlias: fallback-probe-key
  secretName: litellm-key-fallback-probe
  secretKey: key
  models: [fallback-probe]        # deliberately NOT the fallback target
  maxBudget: "0.20"
  budgetDuration: 30d
  rpmLimit: 20
  tpmLimit: 20000
EOF
```

**2. Point a fallback at it.** Use the LOCAL model as the target for a free run;
swap to `claude-sonnet-5` only if the cloud leg specifically needs proving
(costs ~$0.00003 for a `max_tokens: 1` call).

```bash
kubectl -n ai patch litellmproxy litellm --type=merge -p \
  '{"spec":{"routerSettings":{"routing_strategy":"simple-shuffle","fallbacks":[{"fallback-probe":["qwen3.6-35b-a3b"]}]}}}'
kubectl -n ai rollout status deploy/litellm --timeout=180s
```

**3. Exercise it.** The primary is unreachable, so every call must be served by
the fallback.

```bash
kubectl -n ai port-forward svc/litellm 4000:4000 &
PK=$(kubectl -n ai get secret litellm-key-fallback-probe -o jsonpath='{.data.key}' | base64 -d)
for i in $(seq 1 8); do
  curl -s http://localhost:4000/v1/chat/completions \
    -H "Authorization: Bearer $PK" -H 'Content-Type: application/json' \
    -d '{"model":"fallback-probe","messages":[{"role":"user","content":"say ok"}],"max_tokens":4}' \
    | python3 -c 'import sys,json;print("served by:",json.load(sys.stdin).get("model"))'
  sleep 18   # >1 scrape interval apart, so rate() sees an increase
done
```

Expected: `served by: qwen3.6-35b-a3b` every time, HTTP 200.

**3b. Exercise the `auto` leg (optional, separate from the plain-group proof).**

Not covered by the probe above, because `auto` is an auto-router rather than a
plain backend (see §2). To prove it, clone the `auto` alias with every tier and
the classifier pointed at a dead backend, give the probe key that alias, and
confirm a request lands on the fallback rather than 500-ing:

```bash
kubectl apply -f - <<'EOF'
apiVersion: litellm.home-operations.com/v1alpha1
kind: LiteLLMModel
metadata: {name: auto-probe, namespace: ai}
spec:
  modelName: auto-probe
  proxyRef: litellm
  params:
    model: auto_router/complexity_router
    additional:
      complexity_router_default_model: fallback-probe      # dead
      complexity_router_config:
        tiers: {SIMPLE: fallback-probe, MEDIUM: fallback-probe,
                COMPLEX: fallback-probe, REASONING: fallback-probe}
        classifier_type: llm
        classifier_llm_config: {model: fallback-probe, timeout_ms: 5000}
        classifier_fallback: default_model
        default_model: fallback-probe
EOF
kubectl -n ai patch litellmproxy litellm --type=merge -p \
  '{"spec":{"routerSettings":{"fallbacks":[{"auto-probe":["qwen3.6-35b-a3b"]}]}}}'
kubectl -n ai patch litellmvirtualkey fallback-probe-key --type=merge -p \
  '{"spec":{"models":["fallback-probe","auto-probe"]}}'
# then call model: auto-probe with $PK and check which model answers
```

`served by: qwen3.6-35b-a3b` confirms the `auto` leg works and §2's reasoning
holds - this is what was observed when the step was first run on 2026-08-27. An
HTTP 500 would mean auto-router failures do **not** propagate to the fallback
layer, and the `auto:` entries in `routerSettings` should then be removed as
misleading. Delete `auto-probe` in teardown alongside the other probes.

**4. Capture the llama.cpp context-overflow error string** (the one link not
proven pre-merge - see §3). Send a prompt over 262,144 tokens to the *real*
local alias with the master key. It returns in about a second - a prompt over
the limit is refused on token count, before any generation. (Keep it comfortably
over 262,144: an *under*-limit prompt is genuinely processed and will look like
a hang, which is what derailed the first pre-merge attempt.) This step is
deliberately narrower than a full chain proof - it only confirms the error
string LiteLLM matches.

```bash
python3 -c 'import json;json.dump({"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"alpha "*400000}],"max_tokens":1},open("/tmp/big.json","w"))'
MK=$(kubectl -n ai get secret litellm-secret -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)
curl -s -m 900 http://localhost:4000/v1/chat/completions -H "Authorization: Bearer $MK" \
  -H 'Content-Type: application/json' --data-binary @/tmp/big.json | head -c 400
```

**Success criterion:** the response body contains the exact substring
`exceeds the available context size`, which is what LiteLLM matches to raise
`ContextWindowExceededError`. Record the full error string either way.
**If it does not match, `context_window_fallbacks` is inert and this doc's §3
must be corrected** - that is the point of the step.

**5. Verify the alerts fired**, against Prometheus rather than by reading YAML:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
q(){ curl -s --get --data-urlencode "query=$1" http://localhost:9090/api/v1/query | python3 -m json.tool | head -30; }
q 'sum by (requested_model, fallback_model) (rate(litellm_deployment_successful_fallbacks_total[15m])) > 0'
q '(litellm_deployment_state >= 1) and on (model_id) (sum by (model_id) (rate(litellm_deployment_failure_responses_total[15m])) > 0)'
```

Both returned data when this was run pre-merge (`LiteLLMSustainedFailover` at
rate 0.0093, `LiteLLMDeploymentStuckFailing` on the probe deployment).

To also see `LiteLLMFallbackChainExhausted`, repoint the fallback at a *second*
dead model so both legs fail, fire 3 requests, and expect HTTP 500 plus
`litellm_deployment_failed_fallbacks_total > 0`.

**6. Tear down. Do not skip - the suspend is what makes this safe.**

```bash
kubectl -n ai delete litellmvirtualkey fallback-probe-key --ignore-not-found
kubectl -n ai delete litellmmodel fallback-probe fallback-probe-2 --ignore-not-found
kubectl -n ai delete secret litellm-key-fallback-probe --ignore-not-found
flux resume ks litellm -n ai        # reverts routerSettings to what Git says
kubectl -n ai get cm litellm-config -o jsonpath='{.data.config\.yaml}' | grep -A12 router_settings
```

The last command must show the committed chains (`chat-ha`/`auto` ->
`claude-sonnet-5`), not the probe. `flux resume` triggers one immediate
reconcile, so the revert is not deferred to the next interval.

---

## 6. The internal route (captain instruction, 2026-08-26)

`kubernetes/apps/base/ai/litellm/app/httproute-internal.yaml` puts the proxy on
`envoy-internal` at `litellm.${SECRET_DOMAIN}`. The public half of B4 is
untouched and still forbidden.

### Standalone HTTPRoute, not the operator's `spec.route`

**Not a DNS decision.** `spec.route` would have resolved correctly: the
`envoy-internal` Gateway itself carries
`external-dns.alpha.kubernetes.io/target: internal.${SECRET_DOMAIN}` and
external-dns applies it to every attached route. Verified live - `searxng` and
`jellyfin` carry no route-level target annotation and both resolve
`CNAME -> internal.${SECRET_DOMAIN} -> 10.50.0.26`. (Before this change,
`litellm.${SECRET_DOMAIN}` resolved to `10.50.0.27` only via the `*.${SECRET_DOMAIN}`
wildcard that the agentgateway `internal` Gateway publishes; a specific record
now wins over it.)

It is an **annotations** decision. The operator's route schema is exactly
`{hostnames, parentRefs, filters}` - there is no annotations field - so an
operator-owned route cannot carry `gatus.home-operations.com/endpoint` or
`gethomepage.dev/*`. That is not cosmetic here: Gatus *does* auto-discover
un-annotated routes (the Gateway carries `group: internal`), but then applies a
default `[STATUS] == 200` check against `/`, which is exactly why `agentmemory`
(404) and `mcp-gateway` (406) sit permanently red in Gatus today. Owning the
annotation lets this app check `/health/readiness` - which returns
`{"status":"healthy","db":"connected"}` and so also covers the Postgres
dependency - and land in the `ai` group. It also finally consumes the
`GATUS_GROUP: ai` substitution that `kubernetes/apps/main/ai/litellm.yaml` has
been defining and nothing was using.

### The name is load-bearing: never `litellm`

> The operator **deletes** any HTTPRoute whose name equals the `LiteLLMProxy`'s
> name, in the proxy's namespace, whenever `spec.route` is absent - it treats it
> as an orphan of a route it owns.

Proven live 2026-08-26: a hand-written route named `litellm` was created
successfully, served traffic, and then **vanished on the next `LiteLLMProxy`
reconcile**, while an identical route named `litellm-nametest` survived the same
reconcile. The failure is *delayed* - the route works until anything touches the
proxy CR (a config change, a resync, an operator restart) - which makes it the
kind of bug that ships green and breaks days later. Hence `litellm-internal`,
matching the sibling `agentgateway-internal` convention. Do not "tidy" this name.

For the same reason, do not set `spec.route` on the `LiteLLMProxy`: it would
create a second, competing HTTPRoute for the same Service.

### Access gating

None added, deliberately, per "do not invent a new auth surface". 49 of the 52
internal HTTPRoutes in this cluster carry no `SecurityPolicy`; the internal
gateway is itself the boundary (private VLAN + split DNS, never
internet-reachable), and LiteLLM's own master-key/DB login still guards the
admin UI and API. Verified through the gateway: `/v1/models` with a virtual key
returns only that key's allow-listed models, and **without** a key returns
`401`. If SSO in front of the UI is wanted later, `monitoring/kromgo-auth` is
the Authentik ExtAuth pattern to copy.

---

## 7. Post-merge verification record (2026-08-27)

The §5 runbook was executed once, in full, after PR #1457 merged. Recorded here
so the claims above have a dated result behind them rather than only a
procedure. Additional cloud spend: **$0** - every leg used a dead or local
target.

| Check | Result |
|---|---|
| DNS | `litellm.${SECRET_DOMAIN}` -> CNAME `internal.${SECRET_DOMAIN}` -> the envoy-internal address. Control: an unrouted name still resolves to the `*.${SECRET_DOMAIN}` wildcard, so this is a real published record |
| HTTPRoute | `Accepted=True`, `ResolvedRefs=True`; `litellm-internal` survived operator reconciles (the §6 name trap) |
| TLS | validates without `-k` |
| Gatus | discovered as `litellm-internal` in group `ai`, both custom conditions passing - not the default `/` check |
| Auth | `401` unauthenticated through the gateway; each virtual key lists only its own allow-listed model |
| Availability failover | 8/8 requests moved from the dead primary to the fallback, all HTTP 200 |
| Governance | `demo` -> `chat-ha` = **403**; `ha-demo` -> `chat-ha` on a healthy B70 served the local model with **no cost header** |
| `auto` leg (3b) | proven - see §2 |
| Context axis (4) | proven - see §3 |

**Alert behaviour, with both event types present simultaneously:**

| Expression | Matched |
|---|---|
| `LiteLLMSustainedFailover` (ctx excluded) | availability event only |
| context-window discriminator | context event only |
| `LiteLLMFallbackChainExhausted` | both (correctly axis-agnostic) |

The observed `exception_class` values were `Openai.InternalServerError` and
`Openai.ContextWindowExceededError` - provider-prefixed, which is why both
alerts match on a regex rather than an exact string. This is the empirical
confirmation that the two alerts split one counter correctly; without the
exclusion on `LiteLLMSustainedFailover`, sustained prompt overflow would raise
the availability alert with the wrong remediation.

Teardown left no drift: `router_settings` returned byte-identical to Git and no
probe CRs remained.
