# Model catalog: registering, routing, pricing, AI Hub

One `LiteLLMModel` per model in `kubernetes/apps/base/ai/litellm/app/models/`,
listed in `models/kustomization.yaml`. The operator renders them into
`config.yaml` (sorted by `metadata.name`) and rolls the proxy only when the
render changes. Typed params (`model`, `apiBase`, `apiKey`) sit under
`spec.params`; anything else goes under `spec.params.additional` and is merged
verbatim into `litellm_params`. `spec.info` becomes `model_info`;
`info.extra` is the escape hatch for keys with no typed field (prices,
`supports_reasoning`).

## Rule 1: registration is not entitlement

A CR only makes a model callable by name. Nobody can reach it until a
`LiteLLMVirtualKey` allow-lists it, and the auto-router will not route to it
until `models/auto.yaml` names it. Both are captain decisions (D4). The one
way registration alone can spend money is a config-declared fallback, which
bypasses allow-lists ([fallbacks.md](fallbacks.md)).

## Rule 2: route by the credential you hold

Exactly four provider keys, all fields of the one 1Password `ai-keys` item
(the same item and field names the agentgateway backends read):

| Prefix | Env var | Reaches |
|---|---|---|
| `anthropic/` | `ANTHROPIC_API_KEY` | Claude (household metered account) |
| `xai/` | `XAI_API_KEY` | Grok |
| `zai/` | `ZAI_API_KEY` | GLM (native provider, resolves to `https://api.z.ai/api/paas/v4`; no `apiBase`, no `openai/` shim) |
| `openrouter/` | `OPENROUTER_API_KEY` | every other vendor (OpenAI, Gemini/Gemma, DeepSeek, Kimi, cloud Qwen, Nemotron, MiniMax, Meta Muse) |

Use a literal `apiKey: os.environ/<VAR>`, not `apiKeyRef` (that would inject a
second env var for the same value).

## Rule 3: resolve the id on the route you take

The same model has different ids on different routes, and only one works.
Anthropic's API uses dash form (`claude-opus-4-8`); OpenRouter spells it
`anthropic/claude-opus-4.8`, which is not a model on the direct route. Never
copy an id from a spreadsheet, a sibling CR or another catalog. Check:

- Anthropic, xAI, Z.ai: `GET /v1/models` on the provider (Z.ai:
  `https://api.z.ai/api/paas/v4/models`). Listing with the pod's key spends
  nothing.
- OpenRouter: `https://openrouter.ai/api/v1/models` (chat) or
  `/api/v1/embeddings/models` (embeddings), no auth needed.

Display names are not ids (e.g. Gemini 3.1 Pro is only
`google/gemini-3.1-pro-preview`; Muse is Meta's `meta/muse-glimmer-30b`).
When a row has no real catalog match, drop it rather than guess.

## Rule 4: `os.environ/` references are load-bearing at startup

An `apiKey: os.environ/<VAR>` the pod cannot resolve is a broken deployment
entry that takes the **whole proxy** down for every consumer. Remove the
models first, then the key from `app/externalsecret.yaml`.

## Pricing rule: declared prices must match the invoice

- **Metered cloud models: no `info.extra` prices.** LiteLLM's built-in cost
  map already prices them in real USD, which is what D4 budgets must
  measure. Hand-written prices decouple spend from the invoice.
- **Exceptions**, each for an invoice that is not per-token or a map gap:
  - The four Claude Code subscription pass-through CRs: explicit `$0` on
    input, output **and all five prompt-cache fields**
    ([claude-code-subscription.md](claude-code-subscription.md)).
  - `qwen3.6-35b-a3b`: synthetic governance prices, `demo` fixture only
    ([governance-keys.md](governance-keys.md)).
  - `qwen/qwen3-embedding-8b`: its real OpenRouter price
    ($0.01 per 1M input tokens) because the bundled map has no
    `openrouter/qwen/qwen3-embedding-8b` entry and would record $0.
    Re-check it if OpenRouter reprices.
- **Local models: no prices** (sunk hardware), so recorded spend on them is
  $0 and a budget that trips means real money moved.
- An explicit `0` is honoured, not read as unset
  (`use_custom_pricing_for_model` tests `is not None`).

## The five aliases of the one local chat backend

`openai/qwen3.6-35b-a3b` at `http://vllm-app.ai.svc.cluster.local:8000/v1`
(B70 llama.cpp, `apiKey: "not-needed"`). Same weights and GPU; each alias
carries exactly one property the others must not:

| Alias | Prices | Cloud fallback | Thinking | Held by |
|---|---|---|---|---|
| `qwen3.6-35b-a3b` | synthetic | none | on | `demo` only, never production |
| `chat-local` | zero | **none (terminal)** | on | real local traffic: auto SIMPLE/MEDIUM, fail-open pin, repo-wiki, local-only keys |
| `chat-ha` | zero | `claude-sonnet-5-metered` | on | cloud-entitled keys that must survive a B70 outage |
| `qwen3.6-35b-a3b-classifier` | zero | none | **off** | the auto-router only |
| `pr-review-local` | zero | none | **off** | the AI PR reviewer only |

- Never add prices to `chat-local`, `chat-ha`, the classifier or
  `pr-review-local`. Put any bigger synthetic budget on `qwen3.6-35b-a3b`.
- Never add a cloud fallback to anything but `chat-ha`/`auto`.
- Thinking-off (`additional.extra_body.chat_template_kwargs.enable_thinking:
  false`) is required wherever a consumer parses `content`: a reasoning model
  otherwise puts everything in `reasoning_content` and returns empty
  `content`. It only works declared on the model; per-request
  `chat_template_kwargs` changes nothing. `reasoning_effort: none`,
  `chat_template_kwargs.thinking` and `chat_template_kwargs.reasoning_effort`
  do not work on this llama.cpp build.
- Latency-sensitive, obviously-simple traffic should call `chat-local`
  directly, not `auto`.

## AI Hub metadata on local models

Local models are not in LiteLLM's cost map, so without `info` the Hub shows no
mode, window or capabilities. The five chat aliases carry an identical block
(`mode: chat`, `maxInputTokens: 262144` = vllm `--ctx-size`,
`supportsFunctionCalling`, `supportsVision`, `extra.supports_reasoning`, false
only on the two thinking-off aliases). Keep it identical: the aliases share
one backend cost-map key, so an alias without its own values reads a
sibling's. `maxOutputTokens` stays unset (llama.cpp `n_predict` defaults to
-1). This metadata was proven not to change the upstream request or recorded
spend. The `openai` provider label comes from the `openai/` prefix and cannot
be changed. `auto` declares only `mode: chat`; its window is whichever tier
served.

## AI Hub publication (Git-only)

The Hub lists exactly `litellmSettings.public_model_groups` on
`app/litellmproxy.yaml`. The UI "make public" button returns 500 because
`store_model_in_db` is false, so nothing can drift outside Git. **A new model
is not on the Hub until its `modelName` is added there**, and
`scripts/ci/litellm-ai-hub-test.py` fails until you do. Only three aliases
stay unpublished (the test's `UNPUBLISHED` set): the classifier,
`pr-review-local` and `qwen3.6-35b-a3b` (its synthetic price would display as
real). Publishing grants no access. `/public/*` is unauthenticated, but the
route is LAN-only.

## Embedding models

- `embedding-local` -> `ai/embedding-gpu` (Qwen3-Embedding-0.6B, 1024 dims,
  llama.cpp on the B70, `maxInputTokens: 512`). `params.model` must keep
  naming the model the backend actually serves; LiteLLM never checks. No
  prices, no cloud fallback (a fallback embedder at other dimensions would
  corrupt any index). Over-length input fails loud (HTTP 400). The B70 has no
  compute partition, so heavy embedding traffic slows chat (skill
  `b70-llm-serving`).
- `qwen/qwen3-embedding-8b` (OpenRouter, metered, priced explicitly) is the
  vector store's model, requested at `dimensions: 2000`
  ([vector-store.md](vector-store.md)). Other callers get its native 4096.

## Standing notes on registered models

- Both GLM CRs are registered although Z.ai answered every completion with
  `Insufficient balance or no resource package` (surfaced as RateLimitError)
  when they were added: an unfunded account, not a bad route or auth.
- `gpt-6-astra` / `gpt-6-astra-pro` cost $50 per 1M output tokens. Never put
  them in an auto-router tier or a fallback chain.
- `claude-fable-5` (metered, older Fable 5) and `claude-fable-5-1`
  (subscription pass-through, Fable 5.1) are distinct catalog models, not a
  rename.
- `typesafe/jev-1.13` has no `LiteLLMModel` and must not get one: it is a
  decisions model that OpenRouter refuses on chat completions. It is reached
  only through the `/openrouter/alpha/decisions` pass-through
  ([passthrough-lockdown.md](passthrough-lockdown.md)).

## Do not rate-limit a shared GPU deployment in LiteLLM

`LiteLLMModel.spec.params.rpm`/`tpm` looked like the way to bound
`embedding-local` load. Enforcing both needs
`routing_strategy: usage-based-routing-v2` (`simple-shuffle` with pre-call
checks is rpm-only and per-worker). Unit tests against `litellm.Router()` got
clean 429s, but the live proxy **hung callers 45s+ with no response** once
the cap was exceeded; root cause unattributed. The captain reverted it and
load is bounded in the backend server instead (see
`ai/embedding-gpu`'s `--no-cont-batching`/`--timeout` and
`scripts/ci/embedding-gpu-prompt-cache-test.py`). Reproduce against a live
proxy before trusting any deployment-level LiteLLM rate limit again.
Keep `routing_strategy: simple-shuffle`; never `least-busy` (it updates
in-flight counters after the routing decision, so bursts pile on one
deployment).

## Adding a new metered model (procedure)

1. Pick the route by credential (rule 2) and resolve the exact id on that
   route's live catalog (rule 3).
2. Create `models/<name>.yaml` from a sibling on the same route:
   `modelName`, `proxyRef: litellm`, `params.model: <prefix>/<id>`,
   `params.apiKey: os.environ/<VAR>`. No `info.extra` prices unless the cost
   map cannot price it (then declare the provider's real price).
3. Add it to `models/kustomization.yaml`.
4. Add the name to `public_model_groups` in `app/litellmproxy.yaml`
   (`scripts/ci/litellm-ai-hub-test.py` requires every non-plumbing model
   there).
5. Entitlement is separate: add it to a key's `models` only with a captain
   decision, and never to a budgetless key or a fallback chain.
6. Run `python3 scripts/ci/litellm-*-test.py` and `task flux:test:all`.
