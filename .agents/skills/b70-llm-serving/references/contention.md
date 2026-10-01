# Two tenants, one card

`ai/vllm` and `ai/embedding-gpu` both request `devic.es/b70: 1`.
`count: 99` lets the scheduler place both. Nothing partitions compute
or VRAM. `tdarr-node` uses the same card through `devic.es/b70-vaapi`
(skill `intel-gpu`). ComfyUI was the previous co-tenant and is gone
(skill `ai-stack`); do not put it back beside chat.

Measured card: 32656 MiB total, chat holding 26909 MiB (about 82%).
The embedding server is sized to ~1493 MiB so a chat restart still has
a few GiB free. `--ub 2048` on the embedder fits and spends that margin.
There is no llama.cpp flag that reserves a fraction of the card.
`--gpu-memory-utilization` is the parked `vllm-embed` controller.

## Rate, not concurrency

Idle chat decode on this card is the reference the curve was measured
against (~84 t/s in that session, not a number to copy into a dashboard).

| Embedding rate | Chat decode left |
|---|---|
| 0.5 req/s | ~99% of idle. A 154,715-node backfill is on the order of a few hours |
| 1 req/s | ~70% (about a 30% cost) |
| 4 req/s | ~16% (about an 84% cost) |
| always-full queue | collapses to a few percent (~2 t/s) |

Degradation reverses when the burst stops. Idle embeddings cost nothing
measurable. At equal throughput a saturated queue cost chat about 7x
more than a paced one.

Live 2026-09-30 (no caps): 100 closed-loop workers took chat-local from
~3.3 s to ~20 s; 500 workers held 1042 of 1529 requests 28-31 s, all HTTP 200.
The server has no admission limit and `--timeout 30` is a socket timeout, not
a request or queue deadline: it bounds nothing here.

The caps live at LiteLLM, on the `embedding-local` CR
(`kubernetes/apps/base/ai/litellm/app/models/embedding-local.yaml`):
`params.rpm: 60` (1 req/s, every key together), `additional.max_parallel_requests: 8`
and `additional.timeout: 5`. Real use is far below that (2 req/min from real keys
over 30 days, peak 58 in one minute from master-key tests). They work only with
`routerSettings.optional_pre_call_checks: [enforce_model_rate_limits]` (without it
rpm only weights routing) and `model_group_retry_policy.embedding-local` with
`RateLimitErrorRetries: 0` and `TimeoutErrorRetries: 0` (without it the router holds
a refused caller, which is how an earlier rpm-only cap hung callers 45 s+).
`scripts/ci/litellm-embedding-limit-test.py` proves each against the real Router.

- A refusal is an immediate HTTP 429 with no `retry-after`. The OpenAI SDK
  retries it with backoff; a client that does not must pace itself.
- A wedged backend holds a caller about 3x `timeout` + 1.5 s: the openai
  client inside LiteLLM retries a timed-out embedding twice and ignores
  `max_retries` on that path (v1.103.1).
- Not covered: anything that reaches embedding-gpu without LiteLLM, for example
  the agentgateway `embedding-local` backend. Cap the client there.
- Model `rpm` is router-wide enforcement: any other deployment that sets `rpm` or
  `tpm` becomes a hard limit too.
- The pgvector store and `embeddings/batch` use `qwen/qwen3-embedding-8b` on
  OpenRouter, not this alias, so ingestion is unaffected.

Continuous batching, left on, interleaves the whole queue and is what
steals the card. `--parallel 2` only caps slots.

## Chat stays llama.cpp

vLLM on this MoE OOMs during warmup (intel/llm-scaler#382). A second
card is not the fix for that. Skill `intel-gpu` for the deferral.
Buying one does not make this server a vLLM deployment.

TEI's XPU image was deployed on this card and could not create its
backend (`0xe223` is Battlemage; that IPEX build predates it). Do not
retry it as a drop-in.
