# B70 LLM Serving — Benchmark & Tuning Runbook

> **Hardware:** single Intel Arc Pro B70 (Xe2 / Battlemage G31, 32 GB) on `talos-3`.
> **App:** `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml` (chat = llama.cpp SYCL named
> `vllm`; embeddings = vLLM named `vllm-embed`, **default-off**). **Updated:** 2026-09-07
> (image `server-intel-b10820`, `-b 2048 -ub 2048`, ctx 262144, embed/ComfyUI `replicas: 0`).
> Sections 1-4 keep the 2026-06-26 SYCL/Vulkan and isolation evidence; the live serving
> matrix and VRAM correction are [section 6](#6-mixed-batch-prefill-fragmentation-2026-09-07).
>
> This runbook records the measured baseline, the SYCL-vs-Vulkan backend decision, the
> tuning A/B matrix, and the single-card workload-isolation design. It is the evidence
> trail for any change to `helmrelease.yaml`. Reproduction commands are inline.

## TL;DR

- **Backend stays SYCL.** Measured on `b9592` (still true on `b10820`): SYCL decode **~61 t/s**
  vs Vulkan **~36 t/s** on the exact MoE chat model - SYCL wins **1.68×**. The Reddit
  "Vulkan 2.5-3× faster on MoE" finding was from broken build `8739`; the SYCL MoE
  expert-dispatch penalty is **fixed** as of `b9592` (PRs #21527/#21638 merged).
  **No backend change** (image pin did move; see next bullet).
- **Two levers ship 2026-09-07 and they COMPOUND: image `b9592` -> `b10820` and
  `-ub 512` (default) -> `2048`.** Either alone buys ~12% on the loaded benchmark;
  together they cut concurrent wall time **129.3s -> 52.5s (2.46x)** and take
  aggregate prefill 506 -> 1246 t/s. Prefill is 88% of this workload, so this is
  the throughput fix. Full matrix, mechanism and method: [section 6](#6-mixed-batch-prefill-fragmentation-2026-09-07).
- **Current live args:** `--ctx-size 262144` (native max, 2026-07-08), 4 auto slots with
  unified KV, `-b 2048 -ub 2048`, image `server-intel-b10820`. VRAM at that window is
  **~7.2 GiB free** (KV is 2720 MiB, not the ~5.2 GiB sections 1-4 assumed - only 10/40
  layers are full-attention), so context was **not** shortened. `vllm-embed` and `comfyui`
  stay `replicas: 0` for **compute isolation** (section 4's 38× collapse), not because the
  window cannot fit; re-enabling either still needs a re-test. `kv_unified=true` with 4 auto
  slots still gives a single request the full window *and* fleet concurrency. Do **not** pin
  `--parallel` / `--kv-unified` (#1093).
- **The real bottleneck is single-card compute contention, not config.** Chat decode
  collapsed **38×** (61 → 1.6 t/s) when embeddings ran flat-out on the same card. Embeddings
  left the card on 2026-06-28 (#1098; agentmemory uses OpenRouter). Remaining contention is
  **chat vs ComfyUI**. Isolation procedure below; structurally, a second card is still
  deferred ([`b70-second-card-decision.md`](./b70-second-card-decision.md)).

## 1. Baseline (measured 2026-06-26, build `server-intel-b9592`)

`llama-bench` is **not** shipped in the `ggml-org/llama.cpp:server-intel` image (only
`/app/llama-server`). Baseline was taken with the plan's documented fallback: timed
`/completion` requests (the server returns per-request `timings`) plus the `--metrics`
Prometheus gauges. Decode/prefill are sensitive to live production traffic on the shared
card, so figures are **best-of-N within idle windows** unless noted.

| Metric | Value | Notes |
| --- | --- | --- |
| **Decode `tg128` (clean, idle)** | **~61 t/s** | 58.2 / 61.3 / 62.2 / 63.1 across runs |
| Decode under embeddings flood | **~1.6 t/s** | 17 req/s embeddings → **38× collapse** |
| Decode under normal prod load | ~5.7 t/s | 4 slots interleaving real requests |
| Decode rolling-average (`predicted_tokens_seconds`) | ~14.6 t/s | production-experienced; incl. prefill + queue |
| **Prefill `pp512` (best, contended)** | ~185 t/s | 20–185 t/s range under live load |
| Prefill `pp2102` | ~79 t/s | contended sample |
| GPU | Arc Pro B70, 32656 MiB total | ~27 GiB free idle; ~22.7 GiB free with embeddings resident |
| Slots / KV | `n_parallel=4` (auto), `kv_unified=true` | **all 4 slots reported `n_ctx=131072` on this date** (live GitOps is `n_ctx=262144` since 2026-07-08; see §5) |
| KV cache | `q8_0/q8_0`, `--flash-attn on` | |
| Backend | SYCL, `-DGGML_SYCL_F16=ON` | confirmed in `intel.Dockerfile` (research) |
| Model | `Qwen3.6-35B-A3B UD-Q4_K_M` (multimodal, mmproj-BF16) | weights ~20.6 GiB |

**Vs published references (same model, same card):**

| Source | Build | Decode tg128 | Prefill pp512 |
| --- | --- | --- | --- |
| Reddit post | `8739` | ~14 t/s SYCL (erratic) / 39.4 Vulkan | — |
| PMZFX | `b8840` | 54.7 t/s SYCL | 615 t/s |
| **Ours** | **`b9592`** | **~61 t/s SYCL / ~36 Vulkan** | best ~185 (contended) |

- **Decode gap is resolved in our favour:** our SYCL `b9592` (~61) **exceeds** PMZFX's `b8840`
  (54.7). The MoE penalty is fixed; the `predicted_tokens_seconds` gauge (14.6) is a
  long-window average corrupted by prefill + queue, **not** the decode rate — the per-request
  `timings.predicted_per_second` (~61) is authoritative.
- **Prefill gap is real but expected:** our best ~185 t/s vs PMZFX's 615 (idle, isolated).
  Two causes: (1) our samples were under live contention; (2) **structural** — llama.cpp SYCL
  has no XMX flash-attention kernel, so prefill stays well behind vLLM-XPU. This is the
  strongest argument in the [second-card memo](./b70-second-card-decision.md), not something
  single-card tuning can fix.

### Reproduce

```bash
# decode tg128 (per-request timings; run several, take best idle reading)
kubectl -n ai exec deploy/vllm -c app -- sh -lc \
  'curl -s localhost:8000/completion -d "{\"prompt\":\"Once upon a time\",\"n_predict\":128,\"ignore_eos\":true,\"temperature\":0,\"cache_prompt\":false}" | tr "," "\n" | grep predicted_per_second'

# prefill pp512 (large prompt, n_predict=1)
kubectl -n ai exec deploy/vllm -c app -- sh -lc \
  'P=$(yes "lorem ipsum dolor" | head -180 | tr "\n" " "); curl -s localhost:8000/completion -d "{\"prompt\":\"$P\",\"n_predict\":1,\"cache_prompt\":false}" | tr "," "\n" | grep -E "prompt_n|prompt_per_second"'

# live throughput gauges + slot config
kubectl -n ai exec deploy/vllm -c app -- sh -lc 'curl -s localhost:8000/metrics | grep -E "tokens_seconds|requests_"; curl -s localhost:8000/props | tr "," "\n" | grep -E "n_ctx|total_slots"'
```

## 2. Upstream applicability verdicts (research, build `b9592`)

| Knob / question | Verdict | Evidence |
| --- | --- | --- |
| `b9592` newer than `b8840` | **Yes** | monotonic build numbers; 9592 > 8840 |
| SYCL Q8_0 reorder PRs #21527 / #21638 | **In our build** | merged far below b8840; #21527 lifted Q8_0 tg 3.1× |
| `-DGGML_SYCL_F16=ON` | **On** | default ARG in `ggml-org` `intel.Dockerfile` |
| SYCL MoE expert-dispatch fix (8739→b8840) | **In our build** | proven by PMZFX 54.7 t/s + our 61 t/s |
| Vulkan beats SYCL on MoE | **Does NOT apply** | true only on broken `8739`; SYCL fixed since |
| `q8_0` KV cost (~6% tg) | **Accepted** | required for VRAM fit; see §3 |
| vLLM hosts 35B MoE on 1 card (`intel/llm-scaler#382`) | **Still blocked** | #382 OPEN; OOMs even on 2 cards. Keep llama.cpp |
| `GGML_SYCL_DISABLE_OPT=1` for MoE stability | **Not needed for us** | single-source claim; our pod ran 7d, 4 slots, no SEGV |
| SYCL Gated DeltaNet / SSM kernels (needed for hybrid archs, e.g. Qwen3.8-27B) | **Missing** | `ggml-sycl` has no SSM_SCAN/SSM_CONV/GATED_DELTA_NET kernels; ollama#15966 reports SIGSEGV on hybrid archs (`qwen3.5moe`, `qwen3next`) on Intel SYCL. Blocks any hybrid `qwen35`/`qwen35moe`-family model on this backend until upstream adds support - independent of image tag |

## 3. Tuning A/B matrix

Each row is an isolated test on the live card as of **2026-06-26** (ctx 131072, embeddings
could still be resident). Backend A/B led because it gates the rest. Live GitOps since
2026-07-08 is `--ctx-size 262144` with embed/ComfyUI off; see §5.

### Backend: SYCL vs Vulkan — **SYCL wins, KEEP SYCL**

Method: `flux suspend hr vllm` → patch deployment image to `ghcr.io/ggml-org/llama.cpp:server-vulkan`
+ `GGML_VK_VISIBLE_DEVICES=0` → Recreate → benchmark → revert image/env, `resume`.

| Backend (b9592, MoE, q8_0 KV, 131072 ctx) | Decode tg128 | Notes |
| --- | --- | --- |
| **SYCL (current)** | **~61 t/s** | 58.96 / 63.05 / 61.34 |
| Vulkan | ~36 t/s | very stable 33.3–36.4 |

- **Vulkan is feasible on our Intel-plugin Talos node** — ANV (Mesa 26 / Battlemage) found the
  GPU off the device-plugin's render node alone; **no `card0` hostPath needed**. So the
  feasibility blocker did *not* materialise — but Vulkan is simply slower on this MoE.
- Vulkan's ~36 t/s ≈ the Reddit post's 39.4 — Vulkan stayed flat across builds while SYCL
  improved 14 → 61. Coherent story: **the SYCL fixes, not a backend switch, are the win.**
- ⚠️ Operational note: the Vulkan build runs a strict `common_fit_params` memory-fit step that
  **aborts** with `-ngl 99` when free VRAM is tight (it refused to load with only 22.7 GiB free
  while embeddings was resident). SYCL has no such abort. Another reason to stay on SYCL.

### KV-cache precision: `q8_0` vs `f16` — **KEEP q8_0 (VRAM-bound, not speed-bound)**

- `q8_0` K/V is **required** to fit the 131072 context. The Vulkan abort above and the idle
  free-VRAM figure (~27 GiB, dropping to ~22.7 with embeddings) show headroom is genuinely
  tight against ~20.6 GiB weights. `f16` KV roughly doubles KV size and would not fit at
  131072 alongside weights + embeddings + ComfyUI.
- The documented ~6% SYCL decode cost of `q8_0` is moot: our `q8_0` decode (~61) **already
  exceeds** the `f16`-capable reference (54.7). We are not leaving meaningful decode on the
  table. **No change.**

### Parallel slots — **KEEP auto (do NOT pin)**

- Live server auto-selects `n_parallel=4, kv_unified=true`. With a unified KV cache, a single
  request addresses the full 131072 pool (verified: all 4 slots log `n_ctx=131072`) **and** up
  to 4 fleet requests can run concurrently. This is already the best of both worlds — no 1-vs-2-vs-4
  trade-off to make.
- ⚠️ **Pinning `--parallel 4 --kv-unified` explicitly was tried and reverted.** On `b9592` the
  explicit flags overcommit the KV allocation and thrash the tight 32 GB VRAM — decode collapsed
  to **~0.5 t/s** (slower than CPU; a thrashing signature) even on an idle card, while the *auto*
  path with the identical reported values (`n_parallel=4, kv_unified=true`) runs at ~61 t/s.
  **Leave it auto** — the auto path sizes the shared cache correctly. (Verified in production
  2026-06-27; reverted same day.)

### `--cache-reuse` 256, `UD-Q4_K_XL`, `--threads`

- `--cache-reuse 256`: flag kept only as documentation; every boot logs it disabled because
  the mmproj is loaded (ordinary prefix caching still works). See [section 6](#6-mixed-batch-prefill-fragmentation-2026-09-07).
- `UD-Q4_K_XL`: still skipped - current `UD-Q4_K_M` is the validated quant; model changes are
  out of scope for serving-tuning work.
- `--threads`: still untested under load; banner picks `n_threads = 6` against a `cpu: 2`
  request (flagged in section 6, not changed).

**Matrix conclusion (2026-06-26):** keep backend/FA/KV/auto-slots; do not pin
`--parallel/--kv-unified` (tried, reverted for VRAM thrash). Workload isolation (§4)
remained the then-largest win. **Superseded in part on 2026-09-07:** image `b10820` and
`-b`/`-ub 2048` now ship for loaded prefill - see [section 6](#6-mixed-batch-prefill-fragmentation-2026-09-07).
`--cache-reuse 256` is also a documented no-op here (mmproj disables it).

## 4. Single-card workload isolation (B70 time-slice contention)

`talos-3` has **one** B70. Level Zero discrete consumers (`vllm`, `vllm-embed`, `comfyui`)
request `devic.es/b70` from generic-device-plugin (DRM by-path at `0000:03:00.0`);
`tdarr-node` requests `devic.es/b70-vaapi` for the same card under kernel DRM names
(VA-API cannot use the renamed `b70` nodes - see [`../media-stack.md`](../media-stack.md#verifying-va-api-after-a-gpu-change)).
Placement is that extended resource, not hostname affinity. **As of 2026-06-28 / 2026-07-08, only chat
is on the card by default** among AI workloads. `vllm-embed` is `replicas: 0` (agentmemory
moved to OpenRouter). `comfyui` stays `replicas: 0` except during a deliberate image session.
`tdarr-node` may still co-schedule for light QSV. The B70 has **no hardware compute
partition** (no MIG, no SR-IOV compute slicing), so any second consumer **time-slices** the
GPU and starves chat.

The 2026-06-26 measurements below were taken while embeddings could still be co-resident.
They remain valid as the contention mechanism; they are not today's default inventory.

> ⚠️ `--gpu-memory-utilization`, Intel `sharedDevNum: 99`, and generic-device-plugin
> `count: 99` only divide **VRAM / device-count** - none isolate **compute**. `devic.es/b70`
> is a scheduling identity (share-count token), not VRAM fencing. Chat vs ComfyUI is the
> remaining heavy pair; do not start ComfyUI while `vllm` is up. Re-enabling `vllm-embed`
> would restore the three-consumer problem. Light media (plex/playwright) stays on
> `gpu.intel.com/xe` - see [`../ai-gpu-changelog.md`](../ai-gpu-changelog.md).

### Symptom & measured penalty

- Isolated chat decode ≈ **61 t/s**. Under a synthetic embeddings flood (1183 req in 70 s ≈
  17 req/s) chat collapsed to ≈ **1.6 t/s — a 38× degradation**. Normal sporadic production
  sits ≈ 5.7 t/s (rolling average ≈ 14.6 t/s).
- ComfyUI image generation while chat is resident is the **heaviest** contention source
  (sustained full-GPU diffusion + wants the whole 32 GB in Dedicated-VRAM mode).

### Mechanism (why no knob fixes it)

- `sharedDevNum` / `devic.es/b70` count: multiplex device *count* only — leave both (xe pool for light media; b70 identity for discrete consumers).
- vLLM `--gpu-memory-utilization`: VRAM cap only, zero effect on compute scheduling.
- PriorityClass / in-cluster hooks: govern scheduling/preemption, not GPU time-slice
  arbitration, and would fight Flux — **not used.**
- The only real lever is **admission control**: mutual exclusion of the heavy pair (ComfyUI ↔ chat).

### Mutual-exclusion procedure (chat ↔ ComfyUI)

ComfyUI is pinned `replicas: 0` in git (`kubernetes/apps/base/ai/comfyui/app/helmrelease.yaml`)
and its HelmRelease is suspended (`spec.suspend: true`) - Flux is not reconciling it, so
there is no automatic revert. Run a ComfyUI session **only** after freeing the card from
chat, and manually scale it back to 0 when done (see "End the session" below), then
restore chat.

`vllm-embed` is already `replicas: 0` in git. Do **not** scale it up as part of this
procedure unless you have re-enabled local embeddings on purpose.

**Start a ComfyUI session (free the card from chat first):**
```bash
flux -n ai suspend hr vllm                              # so Flux won't fight the scale
kubectl -n ai scale deploy vllm --replicas=0
kubectl -n ai rollout status deploy/vllm --timeout=120s
kubectl -n ai scale deploy comfyui --replicas=1
kubectl -n ai rollout status deploy/comfyui --timeout=300s
```

**End the session (return the card to chat):**
```bash
kubectl -n ai scale deploy comfyui --replicas=0
kubectl -n ai rollout status deploy/comfyui --timeout=120s
flux -n ai resume hr vllm                               # resume alone does NOT restore replicas
kubectl -n ai scale deploy vllm --replicas=1
kubectl -n ai rollout status deploy/vllm --timeout=600s
```

> Both controllers use `strategy: Recreate` + `terminationGracePeriodSeconds: 60`, so each
> releases its GPU cleanly before the other claims it. Do **not** skip the `rollout status`
> waits — starting the second workload before the first's pod is gone re-creates the
> contention you are avoiding.

### Embeddings caveat (don't re-enable it casually)

`vllm-embed` is **off** (`replicas: 0` since 2026-06-28 / #1098). agentmemory embeds via
OpenRouter, not the B70. The 38× collapse was measured against a synthetic flood of a
then-resident embeddings server; that is not the default path anymore.

If you restore `vllm-embed` to `replicas: 1`, it is still capped at
`--gpu-memory-utilization=0.20` (~6.5 GiB) and still time-slices the card. The flood risk
is a **consumer** hammering it, not the VRAM cap. **Do not** lower the embeddings VRAM cap
to "fix" compute contention. Also re-validate `--ctx-size 262144` before leaving both
chat and embed up.

### The structural fix

This procedure **manages** contention; it does not eliminate it. The only way to remove it
is a **second B70** (one model per card, no time-slicing) — currently **deferred**; see
[`b70-second-card-decision.md`](./b70-second-card-decision.md).



## 5. Change log

| Date | Change | Result |
| --- | --- | --- |
| 2026-06-26 | Baseline + SYCL-vs-Vulkan A/B + tuning matrix | SYCL kept (61 vs 36 t/s); config validated; isolation identified as the win |
| 2026-07-08 | `--ctx-size` 131072 → 262144 (native max, no yarn) | Live-tested with `vllm-embed`/`comfyui` both at `replicas: 0`: loads clean (0 restarts), decode 68.4 t/s (no regression). Motivated by 115 "failed to find free space in the KV cache" warnings at 131072 - 4 concurrent slots oversubscribing the shared unified pool, not one long chat. The then-recorded "KV ~2.6→~5.2 GiB" figure was wrong for this hybrid arch; measured 2026-09-07 as 2720 MiB @262k (see [section 6](#6-mixed-batch-prefill-fragmentation-2026-09-07)). Re-enabling embeddings or ComfyUI still needs re-validation for compute contention. |
| 2026-08-21 | Evaluated Qwen3.6 → 3.8 upgrade | **No change - staying on Qwen3.6-35B-A3B.** No Qwen3.8 MoE (35B-A3B class) release exists; Alibaba has only shipped dense `Qwen3.8-27B` and `Qwen3.8-2.4T-A95B` (too large for one B70). `Qwen3.8-27B` GGUF quants exist (unsloth, bartowski) but the architecture is 48/64 Gated DeltaNet + 16/64 full-attention layers, requiring SSM kernels `ggml-sycl` doesn't implement (see §2) - would crash on load regardless of image tag. Revisit when either a comparable MoE 3.8 ships or SYCL SSM support lands upstream. |
| 2026-09-07 | `-ub` 512 → 2048 **and** image `b9592` → `b10820` | **Loaded throughput 2.46x.** Production-shaped concurrent benchmark wall 129.3s → 52.5s; aggregate prefill 506 → 1246 t/s, decode 7.9 → 19.5 t/s; idle decode flat ~70 t/s. The two levers compound (either alone is only ~12%). Root cause was ubatch fragmentation from `split_equal` on this hybrid arch, measured on production traffic (shared-batch prefill 200 → 401 t/s). `-ub 4096` tested and rejected (best idle, worst loaded). Context, KV precision, backend and auto slot config all unchanged; VRAM ~7.2 GiB free. See [section 6](#6-mixed-batch-prefill-fragmentation-2026-09-07). |


## 6. Mixed-batch prefill fragmentation (2026-09-07)

Builds on sections 1-4: backend stays SYCL, KV stays `q8_0`, `--parallel`/`--kv-unified`
stay auto. This section changes only the **image** and the **micro-batch size**, and
corrects three claims made above.

### What the workload actually is

Read off the live server, not assumed:

| Measurement | Value | Source |
| --- | --- | --- |
| prompt : completion ratio | **7.5 : 1** | `prompt_tokens_total` / `tokens_predicted_total` |
| lifetime prefill rate | 247 t/s | `/metrics` |
| **largest context ever seen** | **109,990 tokens** | `llamacpp:n_tokens_max` (38h uptime) |
| per-request context p50 / p90 / max | 50,414 / 84,888 / 98,690 | 307 completed requests in the server log |
| requests over 131,072 | **0** | same |
| truncations | **0** | `truncated = 0` on all 307 |
| average busy slots per decode | **1.38** | `n_busy_slots_per_decode` |
| sole client | `hermes` (10.42.0.147) | agentgateway `internal-noauth` access log |

So **prefill is 88% of the tokens**, real concurrency is ~1.4 slots (not 4), and the
262144 window is ~2.4x larger than anything ever requested. Gateway-side, the local
model's median end-to-end latency was **57.5s** (max 1442s) against 10.3s for the
OpenRouter fallback, and 17 of 148 requests were abandoned by the client at a 180s
timeout.

### Mechanism: why one decode token halves prefill

`Qwen3.6-35B-A3B` is hybrid: only **10 of 40 layers are full-attention**, the rest are
linear/recurrent. llama.cpp therefore routes it through `llama_memory_hybrid::init_batch`,
which splits ubatches with **`split_equal`** (`src/llama-memory-hybrid.cpp`). In
`split_equal` every sequence in a ubatch must contribute an **equal** number of tokens,
and the ubatch ends as soon as the shortest sequence runs out. A single decode token
joining a 2047-token prefill therefore truncates the ubatch to 2 tokens and fragments the
rest of the chunk. `n_rs_seq = 0` in the banner confirms this branch (not `split_seq`).

Measured directly from production log chunks (incremental rate between consecutive
`prompt processing` reports, so each row is the same server at the same instant, which
makes it immune to how busy the card happened to be):

| Build / `-ub` | chunk == 2048 (unshared) | chunk < 2048 (shared with decode) | penalty |
| --- | --- | --- | --- |
| `b9592`, `-ub` 512 (default) | 543.2 t/s (n=133) | **200.1 t/s** (n=569) | **2.71x** |
| `b9592`, `-ub` 2048 | 649.2 t/s (n=56) | **401.0 t/s** (n=158) | **1.62x** |

**85% of production prefill chunks are shared**, so the shared column is the one that
matters. `split_equal` is byte-identical on `master` (b10830), so no build fixes this
mechanism; only a larger `-ub` gives the fragments room.

### A/B matrix

Harness: 4 phases against the live server, all numbers from the server's own per-request
`timings`, ambient production load sampled around every phase. P1/P2 are single-stream
(idle), P3 adds a concurrent decoder, P4 is the production-shaped concurrent load
(4 clients x ~16k prompt + 256 completion, unique non-cacheable prompts). **P4 is the
figure of merit**; the first rep after any restart is discarded as cold.

| Config | Build | `-ub` | idle prefill | idle decode | P3 prefill | P3 decode | **P4 wall** | P4 prefill | P4 decode |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A (was live) | b9592 | 512 | 1011.7 | 69.8 | 905.8 | 19.3 | 129.3s | 505.7 | 7.9 |
| B | b9592 | 2048 | 1257.4 | 69.8 | 1188.8 | 19.9 | 113.9s | 573.9 | 9.0 |
| C | b9592 | 4096 | 1334.2 | 71.0 | 756.3 | 36.9 | 207.6s | 314.9 | 4.9 |
| E | b10820 | 512 | 1525.8 | 70.8 | 1463.6 | 47.4 | 113.6s | 575.6 | 9.0 |
| **D (shipped)** | **b10820** | **2048** | **2294.4** | **70.8** | **2101.7** | **50.4** | **52.5s** | **1245.5** | **19.5** |

- The two levers **compound superlinearly**: A->B is -12% wall, A->E is -12% wall, A->D is
  **-59%**. Neither alone predicts the pair.
- **`-ub 4096` (C) is rejected.** It has the best idle prefill of any b9592 config and the
  *worst* loaded behaviour (P4 wall 207.6s, 1.8x worse than `-ub 2048`). This is the
  clearest example in this repo of an idle benchmark pointing the wrong way.
- Idle decode is flat across every config (69.8 - 71.0 t/s), so nothing here trades decode
  for prefill.

### Verification (read back, not assumed)

`/props` does **not** expose batch sizes and the default verbosity banner does not print
them. Add `-lv 6` to get the real readback:

```
llama_context: n_batch  = 2048
llama_context: n_ubatch = 2048      <- the setting actually in effect
llama_context: kv_unified = true    <- auto path preserved on b10820
llama_context: n_rs_seq = 0         <- confirms the split_equal branch
graph_reserve: reserving a graph for ubatch with n_tokens = 2048
```

Quality: 6 greedy prompts (`temperature 0`, `top_k 1`, fixed seed), **5/6 byte-identical**
between A and D. The 6th is an open-ended summarisation where both builds fabricate
different card specs, i.e. a near-tie broken differently by batch reduction order, not a
regression. Tool calling (`finish_reason: tool_calls`, correct arguments) and multi-turn
recall both verified on b10820.

### Corrections to earlier sections

1. **KV at 262144 is 2720 MiB, not "~5.2 GiB".** Only 10 of 40 layers are full-attention.
   Full budget at `-ub 2048` on b10820: weights 20,583 MiB + KV 2,720 + recurrent 251 +
   compute buffer 1,776 = 25,330 MiB of 32,656, leaving **~7.2 GiB free**. The window is
   therefore not the tight fit sections 1-4 assume, and **no context reduction was needed
   or made** (capability unchanged at 262144, which is 2.4x the largest request ever seen).
2. **`--cache-reuse 256` is a no-op here.** Every boot logs `cache_reuse is not supported
   by multimodal, it will be disabled` because the mmproj is loaded. Ordinary prefix
   prompt-caching and context checkpoints still work. Section 3's "Keep" verdict was
   correct by accident; the flag does nothing.
3. **Measuring this server against ambient load will lie to you.** The card is a live
   inference server for `hermes` and is frequently at 1-3 concurrent requests with no idle
   window. An early A/B here read as a "5.3x prefill win" purely because the control ran
   during a busy period and the candidate during a quiet one; on a quiet card the same
   control does 1011 t/s, not 184. Always record `requests_processing` around every phase,
   and prefer either a self-generated concurrent load (P4) or the within-config chunk
   comparison above, which cancels ambient out.

### Not changed, with reasons

| Lever | Verdict |
| --- | --- |
| `--ctx-size` | **Unchanged at 262144.** VRAM is not the constraint (7.2 GiB free), so shrinking it buys nothing and would cut capability. |
| Speculative decoding (`-md`) | **Unavailable.** No Qwen3.6 draft model below **9B** exists; that exceeds the target's 3B *active* parameters, so drafting would cost more than it saves. `/props` confirms `speculative.types: none`. |
| `--parallel` / `--kv-unified` | **Still auto.** Re-read on b10820: `n_parallel=4`, `kv_unified=true`. Section 3's pinning warning carries forward untested. |
| KV `q4_0` | Not tested. VRAM is not the binding constraint, so there is nothing to buy with the quality loss. |
| `--threads` | Not tested, but worth a look: the banner picks `n_threads = 6` while the pod requests `cpu: 2`. |
| Embeddings contention | Not reproducible today. `vllm-embed` and `comfyui` are both `replicas: 0`; the sole consumer is `hermes`. The 38x figure in section 4 remains historical. |

### Reproduce

`scripts/bench/b70-serving-harness.py` (committed alongside this doc). Port-forward first,
then e.g.:

```bash
kubectl -n ai port-forward deploy/vllm 18000:8000 &
python3 scripts/bench/b70-serving-harness.py --phases 1234 --reps 2 \
    --prompt-tokens 16384 --concurrency 4 --out result.json
```

Disruption note: every row above required restarting the live chat server. Runs were kept
short (2-8 minutes each) and the deployment was returned to its git-declared state after
each one; `hermes` has an agentgateway failover chain (local -> OpenCode Go -> OpenRouter)
which absorbed the restarts, and its pod never restarted.
