# Decision Memo: Second Intel Arc Pro B70 for LLM Serving

> **Status:** Recommendation only — **NO purchase authorized**. This memo weighs the
> cost (~$949) of a second Intel Arc Pro B70 (Xe2 / Battlemage G31, 32 GB) against the
> three concrete benefits it would unlock on `talos-3`, and records the open hardware
> question that gates any of them. Written 2026-06-26.

## TL;DR

A second B70 is **not worth buying today**, but there is one specific condition under
which it becomes a clear yes (see [Recommendation](#recommendation)). Today's single-card
chat performance is already good (≈54.7 t/s single-stream on the exact chat model on SYCL —
see [`PMZFX/llm-benchmarks.md`][pmzfx-llm]), the most compelling dual-card win
(vLLM tensor-parallel) is **blocked by an unresolved upstream OOM bug** that a second card
does **not** automatically fix ([`intel/llm-scaler#382`][382], still OPEN), and the
physical ability to seat a second GPU in `talos-3` is **unverified** (likely needs an
OCuLink/M.2 adapter).

## Current State (the problem a 2nd card would solve)

`talos-3` runs **three GPU workloads contending for one 32 GB B70** via the Intel GPU
device plugin (`gpu.intel.com/xe`):

| Workload | Engine | VRAM appetite | Notes |
| --- | --- | --- | --- |
| Chat (Qwen3.6-35B-A3B MoE, UD-Q4_K_M) | `llama.cpp:server-intel` (SYCL) | ~22–24 GB (weights + 128k KV q8_0) | Single-stream, ≈54.7 t/s |
| Embeddings | `intel/llm-scaler-vllm:0.14.0-b8.3.1` | a few GB (pooling) | |
| ComfyUI | `intel/llm-scaler-omni` | bursty, multi-GB images | Forced to ceph-block RWO, pinned talos-3 |

These coexist via VRAM partitioning runbooks (e.g. scale vllm→0 for big ComfyUI jobs),
i.e. **contention is managed manually, not eliminated**. A second card is fundamentally a
way to remove that contention and/or unlock capabilities one 32 GB card cannot deliver.

## Cost

- **~$949** for a second Arc Pro B70 32 GB (street price, single unit).
- Plus **non-trivial hidden cost**: an OCuLink or M.2-to-PCIe adapter + external GPU
  enclosure/riser **if** `talos-3` cannot seat a second card internally (see
  [hardware constraints](#hardware-constraint-talos-3-expansion-open-question)).
- Plus power: each B70 draws ~114 W under llama.cpp load and up to its board limit under
  vLLM ([`PMZFX/llm-benchmarks.md`][pmzfx-llm]).

## Benefit Options

### Option (a) — Independent models per card

Run **chat on GPU0** and **embeddings + ComfyUI on GPU1**. This cleanly removes today's
three-workloads-on-one-card contention and gives **full single-card performance to each
workload simultaneously** (no more "scale vllm→0 to run ComfyUI" dance).

- **Pro:** Simplest, lowest-risk, no new software stack. Each workload keeps the perf it
  has today but stops fighting for VRAM/compute. Device selection is trivial — the device
  plugin hands each pod its own `renderD12X` and SYCL/Level-Zero device index.
- **Con:** Buys *isolation*, not *more capability*. Does not make chat faster, does not
  enable >32 GB models, does not fix prefill throughput. Pure quality-of-life.
- **Verdict:** The safest reason to buy, but the weakest ROI — it solves a problem we are
  already managing acceptably by hand.

### Option (b) — vLLM tensor-parallel across 2 cards (`-tp=2`)

Serve the 35B MoE on **vLLM-XPU with tensor parallelism**, gaining two things llama.cpp
SYCL cannot give us:

1. **XMX / DPAS flash-attention prefill.** `PMZFX/engine-comparison.md` measures vLLM-XPU
   prompt processing at **2.4× (pp128), 4.5× (pp512), 15.3× (pp2048)** faster than
   llama.cpp SYCL, because **"llama.cpp SYCL's attention path is scalar FP16, no XMX"** —
   the repo calls the XMX FA kernel *"one of the biggest open performance projects for the
   SYCL backend"* ([`PMZFX/engine-comparison.md`][pmzfx-engine]). For long-context / RAG /
   agent prompts this is the single biggest prefill win available.
2. **VRAM headroom** that *might* clear the MoE warmup OOM.

> ⚠️ **Critical caveat — the OOM is NOT auto-solved by a 2nd card.**
> [`intel/llm-scaler#382`][382] is literally *"Unable to run Qwen3.6-35B-A3B FP8 on **2× B70s**
> using `llm-scaler-vllm:0.14.0-b8.2`"* — it fails with `UR_RESULT_ERROR_OUT_OF_RESOURCES`
> **even on two cards** with `-tp=2`. The issue is **still OPEN** (as of this memo); the only
> contributor guidance is to *lower* `gpu-memory-util` (e.g. 0.8) and try an older build.
> So Option (b)'s headroom thesis is **unproven** — buying a 2nd card to "fix the OOM" is a
> bet on an unmerged fix, not a guaranteed outcome. Decode itself is roughly a tie with
> llama.cpp (both memory-bandwidth-bound, ~tie at ~36 t/s on the 14B dual-GPU test in
> [`PMZFX/engine-comparison.md`][pmzfx-engine]) — so the win is **prefill, not tg**.

- **Pro:** Real, large prefill speedup; proper continuous batching; the "right" engine for
  multi-user / agent traffic.
- **Con:** OOM risk unresolved upstream; decode is not faster than what we have; more
  operational complexity than llama.cpp.
- **Verdict:** The most *capable* option, but currently the most *risky* because of #382.

### Option (c) — Layer-split for >32 GB models

Two cards = 64 GB usable, enabling models that do not fit one B70:

| Model | Size | dual-B70 tg | Power | Source |
| --- | --- | --- | --- | --- |
| Qwen3-Coder-Next 80B-A3B Q4_K_M (MoE) | 45.1 GiB | **43.4 t/s** | 79 W | [`PMZFX/multi-gpu.md`][pmzfx-multi] |
| Qwen 3.5-35B-A3B Q8_0 (MoE) | 34.4 GiB | 36.5 t/s | 91 W | [`PMZFX/multi-gpu.md`][pmzfx-multi] |
| DeepSeek-R1-Distill-Llama-70B Q4_K_M (dense) | 39.6 GiB | **11.5 t/s** | — | [`PMZFX/multi-gpu.md`][pmzfx-multi] |
| Llama 3.3-70B Instruct Q4_K_M (dense) | 39.6 GiB | 11.5 t/s | — | [`PMZFX/multi-gpu.md`][pmzfx-multi] |

The pattern is decisive: **MoE layer-splits beautifully (80B-A3B at 43 t/s), dense 70B does
not (11.5 t/s)** because llama.cpp layer-split is sequential — *"both GPUs sit idle half the
time"* ([`PMZFX/multi-gpu.md`][pmzfx-multi]). And the repo's own guidance: *"models that fit
one card should stay on one card"* (a dual-GPU 27B is ~4% **slower**). So Option (c) only
pays off if we actually want a **>32 GB MoE** (e.g. an 80B-A3B coder) — for which it is
excellent — and is a poor reason to buy if we only ever run ≤35B.

## Hardware Constraint: talos-3 expansion (OPEN QUESTION)

`talos-3` is a **mini-PC-class node**. The existing B70 was only enumerated after adding
`pci=realloc assign-busses` kernel args (it sits at `03:00.0`, 32 GB ReBAR) — consistent
with an **OCuLink / external-GPU** attachment rather than a clean internal x16 slot. The
`PMZFX/multi-gpu.md` reference rig runs **both cards at CPU-direct PCIe 5.0 x8** (it has the
lanes); whether `talos-3` can do the same is **unknown and must be physically verified**:

- Does the chassis have a **second OCuLink port** or a free **M.2 slot** usable via an
  M.2→PCIe/OCuLink adapter?
- Does the platform support **x8/x8 bifurcation** (or will a 2nd card force x4/x4, throttling
  TP all-reduce)?
- Is there physical space + power + cooling for a second external enclosure?

> 🚩 **This is the gating open question.** Until someone inspects the physical `talos-3`
> chassis and BIOS bifurcation options, the cost of a 2nd card is *unknown* (card alone vs.
> card + adapter + enclosure) and the benefit ceiling is *unknown* (x8 vs x4 link width
> materially affects TP scaling). Resolve this **before** any purchase decision.

## Single-card alternative: batched request-density (no 2nd card)

A different optimization target entirely. The r/LocalLLaMA B70 author reported **235 t/s
aggregate across 100 concurrent requests** using a **dense Gemma-3-27B Intel AutoRound**
quant on **vLLM-XPU** on a *single* card. That is **aggregate batched throughput**, a
fundamentally different metric from our **single-stream MoE decode** on llama.cpp.

- **Trade-off:** vLLM-XPU gives real continuous batching + XMX prefill on **one** card, but
  at **lower single-stream tg** and only for a **dense** model — our **MoE-on-vLLM path is
  still blocked by [#382][382]**. So this is a single-card software change (engine + model +
  quant swap), not a hardware purchase, and it optimizes *concurrency*, not *latency*.
- **Decision input only:** if the real need is "serve many simultaneous users," the cheapest
  first experiment is vLLM-XPU batching on the *current* card with a dense AutoRound model —
  **before** spending $949. If the need is "one fast single-stream MoE chat," we already have
  it (54.7 t/s) and neither this nor a 2nd card improves single-stream decode much.

## Recommendation

**DEFER. Do not buy a second B70 now.** Conditions, in priority order:

1. **Buy-if (the clear yes):** we commit to running a **>32 GB MoE model** in production
   (e.g. an 80B-A3B coder at ~43 t/s, Option c) **AND** `talos-3` is confirmed able to seat
   a second card at **≥ PCIe x8**. That combination is the one place a 2nd card delivers a
   capability we cannot get any other way, at good performance. Resolve the
   [hardware open question](#hardware-constraint-talos-3-expansion-open-question) first.
2. **Buy-if (secondary):** vLLM-XPU prefill throughput (Option b) becomes a hard requirement
   for agent/RAG traffic **AND** [#382][382] is fixed upstream (a newer `llm-scaler-vllm`
   tag closes it or `gpu-memory-util`/`enforce_eager`/`--max-num-seqs` tuning is proven to
   host the 35B MoE). Until #382 closes, this is a bet, not a buy.
3. **Probably-not-worth-it:** Option (a) (pure isolation) alone. Today's manual VRAM
   partitioning is acceptable; $949 to remove a manageable inconvenience is poor ROL.
4. **Try first / instead:** the [single-card vLLM-XPU batching experiment](#single-card-alternative-batched-request-density-no-2nd-card)
   if the underlying need turns out to be *concurrency*. It is free of hardware spend.

**What would change this recommendation:**
- [#382][382] closes (then Option b moves from "bet" to "buy-if-needed").
- A concrete need for a >32 GB MoE appears (then Option c is the justification).
- `talos-3` is confirmed to have a usable second high-bandwidth GPU attachment (removes the
  cost/benefit uncertainty).
- vLLM gains an XMX FA kernel *in llama.cpp* (would erase Option b's prefill advantage and
  further weaken the case for a 2nd card).

## Sources

- [PMZFX — LLM benchmarks (single-card B70)][pmzfx-llm]
- [PMZFX — engine comparison (SYCL vs vLLM-XPU; XMX FA)][pmzfx-engine]
- [PMZFX — multi-GPU (layer-split / dual-card)][pmzfx-multi]
- [PMZFX — methodology][pmzfx-method]
- [PMZFX — upstream contributions][pmzfx-upstream]
- [intel/llm-scaler#382 — vLLM MoE OOM on B70 (OPEN)][382]

[pmzfx-llm]: https://github.com/PMZFX/intel-arc-pro-b70-benchmarks/blob/master/llm-benchmarks.md
[pmzfx-engine]: https://github.com/PMZFX/intel-arc-pro-b70-benchmarks/blob/master/engine-comparison.md
[pmzfx-multi]: https://github.com/PMZFX/intel-arc-pro-b70-benchmarks/blob/master/multi-gpu.md
[pmzfx-method]: https://github.com/PMZFX/intel-arc-pro-b70-benchmarks/blob/master/methodology.md
[pmzfx-upstream]: https://github.com/PMZFX/intel-arc-pro-b70-benchmarks/blob/master/upstream-contributions.md
[382]: https://github.com/intel/llm-scaler/issues/382
