# B70 LLM Serving — Benchmark & Tuning Runbook

> **Hardware:** single Intel Arc Pro B70 (Xe2 / Battlemage G31, 32 GB) on `talos-3`.
> **App:** `kubernetes/apps/ai/vllm/app/helmrelease.yaml` (chat = llama.cpp SYCL named
> `vllm`; embeddings = vLLM named `vllm-embed`, **default-off**). **Updated:** 2026-07-08
> (ctx 262144, embed/ComfyUI `replicas: 0`). The A/B matrix below is still the 2026-06-26
> measurement set.
>
> This runbook records the measured baseline, the SYCL-vs-Vulkan backend decision, the
> tuning A/B matrix, and the single-card workload-isolation design. It is the evidence
> trail for any change to `helmrelease.yaml`. Reproduction commands are inline.

## TL;DR

- **Backend stays SYCL.** Measured on our `b9592` build: SYCL decode **~61 t/s** vs Vulkan
  **~36 t/s** on the exact MoE chat model - SYCL wins **1.68×**. The Reddit "Vulkan 2.5-3×
  faster on MoE" finding was from broken build `8739`; the SYCL MoE expert-dispatch penalty
  is **fixed** in `b9592` (PRs #21527/#21638 merged). **No image/backend change.**
- **Current live args:** `--ctx-size 262144` (native max, 2026-07-08), 4 auto slots with
  unified KV, image `server-intel-b9592`. That window **fits only because `vllm-embed` and
  `comfyui` are both pinned `replicas: 0`**. Re-enabling either needs a re-test (step back
  toward 131072 on VRAM OOM). `kv_unified=true` with 4 auto slots still gives a single
  request the full window *and* fleet concurrency. Do **not** pin `--parallel` /
  `--kv-unified` (#1093).
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

- `--cache-reuse 256`: no evidence to change; multi-turn agents already benefit. **Keep.**
- `UD-Q4_K_XL`: VRAM is tight; a larger quant erodes the q8_0-KV headroom and risks the fit.
  **Skip** — current `UD-Q4_K_M` validated against the reference.
- `--threads`: **excluded** per plan (confirmed no-op at `-ngl 99`).

**Matrix conclusion:** keep the current image and all llama.cpp args **as auto** — no arg
changes ship for the chat server (the one tried, pinning `--parallel/--kv-unified`, was
reverted for VRAM thrash). The real win is workload isolation (§4) and, structurally, the
second card.

## 4. Single-card workload isolation (B70 time-slice contention)

`talos-3` has **one** B70 via the Intel GPU device plugin. **As of 2026-06-28 / 2026-07-08,
only chat is on the card by default.** `vllm-embed` is `replicas: 0` (agentmemory moved to
OpenRouter). `comfyui` stays `replicas: 0` except during a deliberate image session. The B70
has **no hardware compute partition** (no MIG, no SR-IOV compute slicing), so any second
consumer **time-slices** the GPU and starves chat.

The 2026-06-26 measurements below were taken while embeddings could still be co-resident.
They remain valid as the contention mechanism; they are not today's default inventory.

> ⚠️ `--gpu-memory-utilization` and the device plugin's `sharedDevNum: 99`
> (`kubernetes/apps/base/system/intel-device-plugin-operator/gpu/`) only divide **VRAM /
> device-count** - neither isolates **compute**. The plugin advertises the one card as 99
> schedulable slots. Chat vs ComfyUI is the remaining heavy pair; do not start ComfyUI
> while `vllm` is up. Re-enabling `vllm-embed` would restore the three-consumer problem.

### Symptom & measured penalty

- Isolated chat decode ≈ **61 t/s**. Under a synthetic embeddings flood (1183 req in 70 s ≈
  17 req/s) chat collapsed to ≈ **1.6 t/s — a 38× degradation**. Normal sporadic production
  sits ≈ 5.7 t/s (rolling average ≈ 14.6 t/s).
- ComfyUI image generation while chat is resident is the **heaviest** contention source
  (sustained full-GPU diffusion + wants the whole 32 GB in Dedicated-VRAM mode).

### Mechanism (why no knob fixes it)

- `sharedDevNum`: multiplexes device *count* only — leave it (cluster-wide; transcoders depend on it).
- vLLM `--gpu-memory-utilization`: VRAM cap only, zero effect on compute scheduling.
- PriorityClass / in-cluster hooks: govern scheduling/preemption, not GPU time-slice
  arbitration, and would fight Flux — **not used.**
- The only real lever is **admission control**: mutual exclusion of the heavy pair (ComfyUI ↔ chat).

### Mutual-exclusion procedure (chat ↔ ComfyUI)

ComfyUI is pinned `replicas: 0` in git (`kubernetes/apps/ai/comfyui/app/helmrelease.yaml`)
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
| 2026-07-08 | `--ctx-size` 131072 → 262144 (native max, no yarn) | Live-tested with `vllm-embed`/`comfyui` both at `replicas: 0`: loads clean (0 restarts), decode 68.4 t/s (no regression). KV grows ~2.6→~5.2 GiB. Motivated by 115 "failed to find free space in the KV cache" warnings observed in prod at 131072 — 4 concurrent slots were oversubscribing the shared unified pool, not one long chat. Only fits because the card is otherwise empty; re-enabling embeddings or ComfyUI needs re-validation and possibly stepping back down. |
| 2026-08-21 | Evaluated Qwen3.6 → 3.8 upgrade | **No change - staying on Qwen3.6-35B-A3B.** No Qwen3.8 MoE (35B-A3B class) release exists; Alibaba has only shipped dense `Qwen3.8-27B` and `Qwen3.8-2.4T-A95B` (too large for one B70). `Qwen3.8-27B` GGUF quants exist (unsloth, bartowski) but the architecture is 48/64 Gated DeltaNet + 16/64 full-attention layers, requiring SSM kernels `ggml-sycl` doesn't implement (see §2) - would crash on load regardless of image tag. Revisit when either a comparable MoE 3.8 ships or SYCL SSM support lands upstream. |
