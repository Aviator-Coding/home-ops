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

## CPU on talos-3

llama.cpp SYCL launches GPU work from CPU threads, so a starved host slows
decode and prefill together. Measured 2026-10-26 on a quiet server
(`scripts/bench/results/*-f16-ub2048-*.json`, `requests_processing` 0):

| talos-3 state | Decode t/s | Prefill t/s |
|---|---|---|
| tdarr-node busy (12 cores) | 39-42 | 1250-1290 |
| tdarr-node stopped | 64 | 1815 |

Pod uptime made no difference (fresh restart gave the same 42 t/s). The
Grafana "Decode t/s" panel reads about 22 t/s over 24 h because real traffic is
also 3-4 overlapping 30-65K prompts, where a 65K prefill took 181 s and starved
the other slots' decode to under 1 t/s.

The fix is three numbers that must stay consistent: chat `-t 8` with
`cpu: "8"` requested, and `tdarr-node` limited to `cpu: 4`. Re-measure with
the harness after changing any of them, with tdarr running.

With chat on `-ub 4096` and tdarr saturating its cap (`kubectl top` showing the full limit),
16K prefill measured 1853 t/s at `cpu: 6`, 1951 at `cpu: 4` and 2079 with tdarr stopped
(`scripts/bench/results/tdarr-cap*-live-p1-*`, `day-ub4096-*`). The remaining 6% is not cores,
so shared memory bandwidth or cache is the likely cause. Judge contention by prefill here: with
`--spec-type ngram-mod` a repeated prompt decodes faster the second time, so decode runs
are not comparable.

## Scheduling on talos-3

Chat requests 8 CPUs on the only B70 node (20 CPUs) and uses the Recreate strategy, so a restart
during a CI burst can sit Pending. `PriorityClass chat-gpu-high` (1100000, above the runners'
1000000) lets a Pending chat pod displace a runner or `tdarr-node`, and stops a runner from
preempting chat. The cost is a CI job losing its runner when that happens. `embedding-gpu-low`
(-10, `Never`) is the opposite end and never displaces anything.

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
- Not covered: anything that reaches embedding-gpu without LiteLLM (a direct
  in-cluster caller). Cap the client there.
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
