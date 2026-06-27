# B70 LLM Serving — Benchmark & Tuning Runbook

> **Hardware:** single Intel Arc Pro B70 (Xe2 / Battlemage G31, 32 GB) on `talos-3`.
> **App:** `kubernetes/apps/ai/vllm/app/helmrelease.yaml` (chat = llama.cpp SYCL named
> `vllm`; embeddings = vLLM named `vllm-embed`). **Date:** 2026-06-26.
>
> This runbook records the measured baseline, the SYCL-vs-Vulkan backend decision, the
> tuning A/B matrix, and the single-card workload-isolation design. It is the evidence
> trail for any change to `helmrelease.yaml`. Reproduction commands are inline.

## TL;DR

- **Backend stays SYCL.** Measured on our `b9592` build: SYCL decode **~61 t/s** vs Vulkan
  **~36 t/s** on the exact MoE chat model — SYCL wins **1.68×**. The Reddit "Vulkan 2.5–3×
  faster on MoE" finding was from broken build `8739`; the SYCL MoE expert-dispatch penalty
  is **fixed** in `b9592` (PRs #21527/#21638 merged). **No image/backend change.**
- **Current llama.cpp args are already near-optimal.** `q8_0` KV is required for the 131072
  context to fit in VRAM; `kv_unified=true` with 4 auto slots already gives single requests
  the full window *and* fleet concurrency. No arg changes improve single-stream throughput.
- **The real bottleneck is single-card compute contention, not config.** Chat decode
  collapses **38×** (61 → 1.6 t/s) when embeddings runs flat-out; the Intel GPU plugin
  time-slices compute with no hardware partition. The durable win is workload isolation
  (below) and, structurally, a second card (see
  [`b70-second-card-decision.md`](./b70-second-card-decision.md)).

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
| Slots / KV | `n_parallel=4` (auto), `kv_unified=true` | **all 4 slots report `n_ctx=131072`** → single req gets full window |
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

## 3. Tuning A/B matrix

Each row is an isolated test on the live card; backend A/B led because it gates the rest.

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

### Parallel slots — **KEEP auto (4 + kv_unified); pin for determinism**

- Live server auto-selects `n_parallel=4, kv_unified=true`. With a unified KV cache, a single
  request addresses the full 131072 pool (verified: all 4 slots log `n_ctx=131072`) **and** up
  to 4 fleet requests can run concurrently. This is already the best of both worlds — no 1-vs-2-vs-4
  trade-off to make. Recommend pinning `--parallel 4` + `--kv-unified` explicitly so the
  behaviour can't silently change on an image bump (see §4).

### `--cache-reuse` 256, `UD-Q4_K_XL`, `--threads`

- `--cache-reuse 256`: no evidence to change; multi-turn agents already benefit. **Keep.**
- `UD-Q4_K_XL`: VRAM is tight; a larger quant erodes the q8_0-KV headroom and risks the fit.
  **Skip** — current `UD-Q4_K_M` validated against the reference.
- `--threads`: **excluded** per plan (confirmed no-op at `-ngl 99`).

**Matrix conclusion:** keep the current image and all llama.cpp args. The only config change
worth shipping is pinning the slot/KV behaviour for determinism (§4); the real win is
workload isolation (§4) and, structurally, the second card.

## 4. Single-card workload isolation (B70 time-slice contention)

`talos-3` runs three GPU workloads against **one** B70 via the Intel GPU device plugin:
**chat** (`vllm` = llama.cpp SYCL, service `vllm-app`), **embeddings** (`vllm-embed`), and
**ComfyUI** (`comfyui`, default-off). The B70 has **no hardware compute partition** (no MIG,
no SR-IOV compute slicing), so co-resident workloads **time-slice** the GPU and starve each
other's compute when both are active.

> ⚠️ `--gpu-memory-utilization` and the device plugin's `sharedDevNum: 99`
> (`kubernetes/apps/system/intel-device-plugin-operator/gpu/`) only divide **VRAM /
> device-count** — neither isolates **compute**. The plugin advertises the one card as 99
> schedulable slots, so all three pods hold a `gpu.intel.com/xe: 1` claim on the *same*
> physical card. The only effective control is to keep two heavy compute consumers from
> being active at the same time.

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

ComfyUI is pinned `replicas: 0` in git (`kubernetes/apps/ai/comfyui/app/helmrelease.yaml`).
Run a ComfyUI session **only** after freeing the card from chat, and restore chat afterward.
Flux (`interval: 1h`) reconciles ComfyUI back to 0 on its own, so the git default is the net.

**Start a ComfyUI session (free the card from chat first):**
```bash
flux -n ai suspend hr vllm                              # so Flux won't fight the scale
kubectl -n ai scale deploy vllm vllm-embed --replicas=0
kubectl -n ai rollout status deploy/vllm --timeout=120s
kubectl -n ai scale deploy comfyui --replicas=1
kubectl -n ai rollout status deploy/comfyui --timeout=300s
```

**End the session (return the card to chat):**
```bash
kubectl -n ai scale deploy comfyui --replicas=0
kubectl -n ai rollout status deploy/comfyui --timeout=120s
flux -n ai resume hr vllm                               # resume alone does NOT restore replicas
kubectl -n ai scale deploy vllm vllm-embed --replicas=1
kubectl -n ai rollout status deploy/vllm --timeout=600s
```

> Both controllers use `strategy: Recreate` + `terminationGracePeriodSeconds: 60`, so each
> releases its GPU cleanly before the other claims it. Do **not** skip the `rollout status`
> waits — starting the second workload before the first's pod is gone re-creates the
> contention you are avoiding.

### Embeddings caveat (don't flood it)

`vllm-embed` is capped at `--gpu-memory-utilization=0.20` (~6.5 GiB) and is low-rate in
normal use — it coexists with chat fine at steady state. The 38× collapse only reproduced
under a *synthetic* flood. The risk is a **consumer** flooding it (e.g. agentmemory
auto-compress / per-observation embedding storms — already disabled, commit `daa291d8`), not
VRAM. If chat decode tanks while ComfyUI is at 0, check whether something is hammering
`vllm-embed` before suspecting the chat server. **Do not** lower the embeddings VRAM cap to
"fix" this — it is a request-rate problem, not a memory problem.

### The structural fix

This procedure **manages** contention; it does not eliminate it. The only way to remove it
is a **second B70** (one model per card, no time-slicing) — currently **deferred**; see
[`b70-second-card-decision.md`](./b70-second-card-decision.md).



## 5. Change log

| Date | Change | Result |
| --- | --- | --- |
| 2026-06-26 | Baseline + SYCL-vs-Vulkan A/B + tuning matrix | SYCL kept (61 vs 36 t/s); config validated; isolation identified as the win |
