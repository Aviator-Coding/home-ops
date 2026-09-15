# Moving the embedder onto the Arc Pro B70 - analysis

> **Status:** analysis only. No manifest change ships with this document.
> **Date:** 2026-09-15. **Card:** single Intel Arc Pro B70 (32656 MiB) on `talos-3`.
> **Companion evidence:** [`b70-llm-serving-tuning.md`](./b70-llm-serving-tuning.md) (sections 4 and 6),
> [`b70-second-card-decision.md`](./b70-second-card-decision.md),
> [`../talos-3-scheduling-truth.md`](../talos-3-scheduling-truth.md).

The proposal was to move `ai/mcp-tools-embedding` off CPU and onto the B70, so that a
154,715-node backfill stops taking ~8 hours. This document records what was measured, three
premises that turned out to be wrong, the serving-path comparison, and the one question that
is genuinely the operator's to answer.

## 1. Three premises that do not survive checking

### 1a. The card is not 80% free. It is ~77% used.

The proposal read `--gpu-memory-utilization=0.20` and "~5GB VRAM" off
`kubernetes/apps/base/ai/vllm/app/helmrelease.yaml` and concluded the LLM leaves ~80% of the
card unused. Those two strings belong to the **`vllm-embed` controller**, which is
`replicas: 0` and has no pod. The live chat server is the `vllm` controller, and it is
**llama.cpp, not vLLM** - it has no `--gpu-memory-utilization` flag at all.

What the live server actually holds (measured 2026-09-07 at `-lv 6` against the exact image
`server-intel-b10820` and args `-b/-ub 2048`, `--ctx-size 262144` that are still live today -
see `helmrelease.yaml`'s own header and section 6 of the tuning runbook):

| item | VRAM |
| --- | ---: |
| weights (`Qwen3.6-35B-A3B` UD-Q4_K_M + mmproj) | 20.10 GiB |
| KV cache @ 262144, `q8_0/q8_0` (only 10/40 layers full-attention) | 2.66 GiB |
| recurrent state | 0.25 GiB |
| compute buffer @ `-ub 2048` | 1.73 GiB |
| **resident total** | **~24.7 GiB** |
| **free of 31.89 GiB** | **~7.2 GiB** |

So the headroom is ~23%, not ~80%. It is still enough (section 3), but the arithmetic has to
start from the right number.

Live cross-checks run for this analysis, read-only, without touching the server process:
`kubectl logs` on the running pod confirms `n_slots = 4, n_ctx_slot = 262144,
kv_unified = 'true'`, matching the configuration those measurements were taken against.
Per-layer VRAM lines are only emitted at `-lv 6`; the live pod runs `verbosity = 3`, so
re-reading them would require a restart, which is out of scope here. The 2026-09-07 figures
are used instead and are labelled as such.

### 1b. The model swap is already done, and it landed two days ago.

`Qwen/Qwen3-Embedding-0.6B` is **already live on the CPU embedder**. It shipped in PR #1681
(`fe57a764`, 2026-09-13) with the LiteLLM half in the same change. Confirmed live from the
running server's own `GET /info`:

```
model_id: Qwen/Qwen3-Embedding-0.6B      model_dtype: float32
pooling: last_token                      max_input_length: 384
max_batch_tokens: 384                    max_batch_requests: 4
auto_truncate: true                      version: 1.9.4
```

This matters for the re-index question. The 384 -> 1024 dimension break **already happened on
2026-09-13**; it is not a consequence of moving to the GPU. Moving the same model to the GPU
changes no dimension. What *does* carry over is the underlying rule, in a sharper form:

> Vectors are only comparable if they come from the same model **and the same tokenization and
> pooling**. A GPU path that tokenizes differently from TEI produces a different vector space
> while reporting the same model name and the same 1024 dimensions - a silent corruption, not
> an error. See section 4.

There are 0 embeddings stored today, so nothing is at risk right now. Any future swap of
serving path, not just of model, has to be treated as a re-index event.

### 1c. "The slowness is simply that it is the CPU build" is only part of it.

Three separate throttles are in play, and two are configuration:

1. It is the CPU build (`text-embeddings-inference:cpu-latest`).
2. TEI's candle CPU backend **upcasts to float32** (`model_dtype: float32` above), so every
   forward pass streams ~2.2 GiB of weights instead of ~1.1 GiB.
3. `--max-batch-tokens 384` and the resulting `max_batch_requests: 4` cap how much work a
   single forward pass can carry. That value was chosen deliberately, and for good reasons
   recorded at length in `toolhive/config/embeddingserver.yaml`: raising it re-pays a steeply
   scaling warmup cost on every pod start (54s at 384; minutes at 512+), and it does **not**
   fix the silent-truncation gap it might appear to.

Point 3 means there is a CPU-side lever that has not been priced, and it is discussed as
option B in section 6.

## 2. The finding that actually decides this: compute, not VRAM

**This exact thing was already built, measured, and switched off.**

`vllm-embed` is a GPU embedding server on this card, fully configured, in this repo today. It
is pinned `replicas: 0`, and the reason is recorded in four places
(`helmrelease.yaml:88`, `backends/embedding-local.yaml`, `models/embedding-local-cpu.yaml`,
tuning runbook section 4):

> Isolated chat decode ~61 t/s. Under a synthetic embeddings flood (1183 req in 70 s,
> ~17 req/s) chat collapsed to ~1.6 t/s - **a 38x degradation**.

The mechanism is hardware, and no knob in this cluster addresses it:

- The B70 has **no compute partitioning** - no MIG, no SR-IOV compute slicing. Any second
  consumer time-slices the whole card.
- `devic.es/b70` `count: 99` is a **scheduling identity token**, not a fence. The plugin mounts
  the same `/dev/dri/card0` and `renderD128` up to 99 times. "98 free slots" is 98 free
  *tokens*, not 98 free *shares of the GPU*.
- `--gpu-memory-utilization` caps VRAM only, with zero effect on compute scheduling.
- The runbook's own conclusion: *"The only real lever is admission control."*

So the proposal's framing - free VRAM implies room for a second tenant - is measuring the
wrong resource. VRAM is not the binding constraint here and never was.

**But the contention is bounded, and that is the part worth being precise about.** The 38x was
measured against a *sustained flood*. An embedding endpoint that exists and is idle costs
essentially no GPU compute; the steady-state consumers are ToolHive's vmcp tool-selection index
(~959 vectors, rebuilt in-memory on pod restart) and nothing else -
`embedding-external` still has no live consumer. The contention event is the **backfill job**,
and its duration is the honest unit of the cost (section 5).

## 3. VRAM coexistence, with numbers

Official Qwen GGUF artefact sizes (read from the HuggingFace API, `Qwen/Qwen3-Embedding-0.6B-GGUF`);
the model is 595,776,512 parameters, `qwen3` architecture, 32768 context:

| build | file size | as GiB |
| --- | ---: | ---: |
| `Qwen3-Embedding-0.6B-Q8_0.gguf` | 639,150,592 B | 0.60 GiB |
| `Qwen3-Embedding-0.6B-f16.gguf` | 1,197,629,632 B | 1.12 GiB |

Adding a compute buffer sized the same way the LLM's is (attention score matrix at `-ub 2048`:
2048^2 x 16 heads x 2 B = 128 MiB, plus activations; budget <= 0.5 GiB):

| | weights | buffers | total | free after |
| --- | ---: | ---: | ---: | ---: |
| f16 | 1.12 GiB | <= 0.5 GiB | **~1.6 GiB** | **~5.6 GiB** |
| Q8_0 | 0.60 GiB | <= 0.5 GiB | ~1.1 GiB | ~6.1 GiB |

**VRAM coexistence is not in doubt** - roughly a 4.5x margin on the f16 build. This is
arithmetic from published artefact sizes plus the LLM's measured footprint, not a live
co-residency test; a live test would mean running a second workload on the card, which is
exactly the thing under review.

Note this is also a reason to reject vLLM specifically: vLLM pre-allocates
`--gpu-memory-utilization` as a fraction of the card's **total** memory. Even the existing
`0.20` would claim 6.4 GiB of the 7.2 GiB free, leaving under a gigabyte. llama.cpp allocates
what the model actually needs.

## 4. Serving path

| path | verdict |
| --- | --- |
| **TEI `xpu-ipex-latest`** | **Rejected.** TEI publishes an XPU image and even uses this exact model in its docs, but the IPEX build inside it predates B-series silicon and does not recognise device ID `0xE223` - which is this card's ID, read live from `/sys/bus/pci/devices/0000:03:00.0/device`. Building TEI from source against a current IPEX is possible (Intel validates PyTorch 2.10.0+xpu on Arc Pro B-series) but makes the fleet the owner of a bespoke image. |
| **vLLM (`intel/llm-scaler-vllm`)** | **Rejected.** It is the `vllm-embed` shape already here and it does run on this card, but it needs a wrapper to present a TEI-shaped endpoint, and its VRAM pre-allocation model is a poor fit for a card that is 77% committed (section 3). |
| **llama.cpp SYCL** | **Recommended, with one unverified risk.** Lowest-risk stack: it is what already runs on this card. Qwen ships an official GGUF and an official command line: `llama-server -m model.gguf --embedding --pooling last -ub 8192 --verbose-prompt`. |

### The unverified risk on the llama.cpp path

Qwen3-Embedding uses **last-token pooling**, so the embedding is read off the final token. The
reference implementation tokenizes with the HuggingFace tokenizer, which appends
`<|endoftext|>`. llama.cpp issue [#14234](https://github.com/ggml-org/llama.cpp/issues/14234)
("Bad output from Qwen3-Embedding-0.6B", ~20% worse retrieval) was **closed as completed** with
exactly that answer from the maintainer: *"Looks like you have to add the EOS token manually."*

Whether current llama.cpp appends it automatically for this GGUF depends on
`tokenizer.ggml.add_eos_token` in the file, which the HuggingFace API does not expose and which
**cannot be settled without running the model**. If it does not, every vector is pooled off the
wrong token, ~20% of retrieval quality disappears, and **nothing reports an error** - the server
returns 200 with a correctly-shaped 1024-dimension vector.

This repo has a standing allergy to exactly this failure shape. So the llama.cpp path carries a
mandatory acceptance gate: before any backfill, embed a fixed probe set through both the live
TEI server and the new GPU server and compare cosine similarity per item. Near-1.0 means the
spaces agree; anything lower means the GPU path is not a drop-in and the corpus must never mix
the two. That gate needs the GPU server to be running, so it belongs after the go-ahead, not
before it.

## 5. Throughput: what was measured, and what is estimated

### Measured, live, 2026-09-15 (CPU baseline)

Method: `kubectl port-forward` to `mcp-tools-embedding`, then batch-of-32 `POST /v1/embeddings`
at client concurrency 3 for 100s, with the server's own `/metrics` counters sampled before and
after so the numbers are server-side and exclude port-forward overhead. TEI's counters were
static before and after the run, confirming no other traffic was competing.

| | |
| --- | ---: |
| sustained throughput | **2.23 embeddings/sec** |
| token throughput | **51 tokens/sec** |
| tokens per forward pass | 87.3 |
| items per forward pass | 3.80 |
| attributed inference per embedding | 1.78 s |
| single 13-token request, isolated | **17.2 s** |

Corpus size: 154,715 nodes. The server's lifetime mean input length is 31.5 tokens
(`te_request_input_length_sum / te_request_input_length_count` = 836,596 / 26,588), so the
corpus is **~4.87 M tokens**.

- At the measured 51 tok/s: **~26.5 hours**.
- At the ~170 tok/s implied by the previously reported 8-hour figure: **~8.0 hours**.

Both are reported because they disagree by 2.4x and the difference is almost certainly client
concurrency and batch shape, not the server. The 8-hour figure is used as the optimistic CPU
baseline below, so the comparison is not flattered.

### Estimated (GPU), and the limits of the estimate

This **was not measured**, and measuring it means running a workload on the card, which is the
decision being escalated. The estimate is anchored on this same card's own measured prefill
rate rather than on vendor numbers:

- The B70 sustains **1246 tok/s aggregate prefill** on `Qwen3.6-35B-A3B` at `-ub 2048`
  (measured 2026-09-07, tuning runbook section 6). That model activates ~3B parameters per
  token; `Qwen3-Embedding-0.6B` is 0.596B dense, so ~5x less arithmetic per token, and
  embedding is prefill-only with no decode phase.
- **Conservative bound:** discard the 5x model-size advantage entirely as small-model kernel
  inefficiency and assume the embedder merely matches the 35B's prefill rate, 1246 tok/s ->
  **~65 minutes**.
- **Optimistic bound:** claim half the model-size advantage, ~3100 tok/s -> **~26 minutes**.

So: **roughly 0.5 to 1 hour, against 8 hours on CPU - an 8x to 16x improvement.** The bound is
deliberately pessimistic at the conservative end; the real figure is unlikely to be worse than
65 minutes and could be considerably better.

## 6. Node capacity on `talos-3`

The GPU embedder can only run on `talos-3` - `devic.es/b70` is capacity 99 there and 0
elsewhere - and `talos-3` is the node `../talos-3-scheduling-truth.md` exists to protect.

Measured live 2026-09-15:

| | |
| --- | ---: |
| allocatable | 93,604 Mi |
| requested (36 pods) | 88,002 Mi |
| **margin** | **5,602 Mi** |
| `devic.es/b70` allocated | **1 of 99** (`ai/vllm` alone) |

A llama.cpp embedder with `-ngl 99` keeps weights in VRAM, so its host-RAM request is runtime
plus load buffers - budget ~2 Gi, which fits inside the margin with room left.

Two cautions. The margin was **678 Mi** when that document was written on 2026-09-14 and is
5,602 Mi today; the figure oscillates with ephemeral CI runner pods (one 512 Mi runner was
resident during this measurement), so it must be re-read immediately before shipping rather
than inherited from here. And the new workload belongs in **its own HelmRelease**, not as a
third controller inside `ai/vllm`: adding to that release means a Helm upgrade of the release
that owns the live LLM, and `ai/vllm` uses `strategy: Recreate` with a ~21 GB model load, so
any pod-template churn is a multi-minute outage.

## 7. The question for the operator

VRAM fits, the node fits, and the serving path is available. The single open question is not
technical:

> The backfill takes ~8 hours on CPU with **zero** effect on the LLM, or ~0.5-1 hour on the
> GPU during which chat decode degrades - historically by up to 38x. Which is preferred?

Three shapes, all buildable:

- **A. GPU endpoint, scheduled contention.** Idle cost is ~1.6 GiB VRAM and one `b70` token;
  the LLM is untouched until the backfill runs, and degraded for the ~0.5-1 hour it runs.
  Best if re-embedding recurs or the graph keeps growing.
- **B. Stay on CPU, buy back throughput there.** Section 1c shows the 8 hours is not purely
  "the CPU build". Zero GPU risk, and the card is never shared - but it re-pays the warmup
  cost that `embeddingserver.yaml` documents at length, and it is strictly slower.
- **C. Mutual exclusion.** Scale chat to 0, run the backfill on the whole card, scale chat back.
  Fastest and contention-free, but the LLM is **down** for the window. This is the shape the
  ComfyUI procedure in tuning runbook section 4 already documents.

**Recommendation: A**, on the grounds that the cost is bounded, one-time per backfill, and
schedulable, while the idle endpoint is genuinely close to free - provided the operator accepts
that window. If the backfill is a genuine one-off and nothing else will need re-embedding, **B
overnight is the lower-risk answer** and the GPU move can wait for a second card
(`b70-second-card-decision.md`).

Whichever is chosen, the section 4 cosine-parity gate applies before any vector is stored.
