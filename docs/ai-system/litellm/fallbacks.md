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
