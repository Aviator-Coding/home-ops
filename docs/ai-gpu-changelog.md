# AI / B70 GPU Stack Change Log

Track record of **deliberate configuration changes** to the `ai` namespace and the Intel
Arc Pro B70 GPU it runs on — what changed, why, the evidence behind it, and how to roll it
back. Mirrors [`ceph-cluster-changelog.md`](./ceph-cluster-changelog.md) (deliberate Ceph
changes) and [`hardware-incidents.md`](./hardware-incidents.md) (hardware failures), but for
the GPU / LLM-serving stack. Goal: when something breaks, answer "what did we change
recently, and why?" without spelunking git.

- **Hardware failures** → [`hardware-incidents.md`](./hardware-incidents.md)
- **Ceph config changes** → [`ceph-cluster-changelog.md`](./ceph-cluster-changelog.md)
- **This file** → chronological log of applied AI / GPU changes

> ⚠️ All AI config is GitOps under `kubernetes/apps/ai/`. Changes go through the relevant
> HelmRelease (e.g. `kubernetes/apps/ai/vllm/app/helmrelease.yaml`); Flux reconciles.
> Record every deliberate change here when you merge it.

---

## Current baseline (2026-06-14)

| Layer | Value |
|-------|-------|
| **GPU** | 1× Intel Arc Pro B70 (Battlemage G31, 32 GiB / 32656 MiB reported), on talos-3 via OCuLink |
| **Device plugin** | `gpu.intel.com/xe: 99` shared units — a **scheduling token only, no VRAM fencing** |
| **Co-resident** | `vllm` (chat) + `vllm-embed` + `comfyui` all share the one physical card |

### Workloads on the B70

| Pod | Image | Role | VRAM |
|-----|-------|------|------|
| `vllm` | `ghcr.io/ggml-org/llama.cpp:server-intel` | Chat — **llama.cpp SYCL**, `Qwen3.6-35B-A3B UD-Q4_K_M`. Keeps the `vllm` name so the service + gateway backend stay stable; real vLLM OOMs the MoE warmup (intel/llm-scaler#382). | ~21.4 GiB weights + KV |
| `vllm-embed` | `intel/llm-scaler-vllm` | Embeddings — `Qwen3-VL-Embedding-2B`, `--gpu-memory-utilization 0.20` | ~6.5 GiB |
| `comfyui` | `intel/llm-scaler-omni` | Image generation, on-demand model loads | multi-GB |

### SYCL / Battlemage constraints (do **not** "optimize" into these)

- ❌ **No flash attention** (`-fa`) — corrupts output on Intel Arc/Xe2 (ggml-org/llama.cpp#19276)
- ❌ **No quantized KV cache** (`--cache-type-k/-v q8_0`) — segfaults on SYCL (it depends on FA); keep fp16
- ✅ For heavy ComfyUI work, scale `vllm`→0 first — the card is shared with no memory fencing

---

## How to add an entry

When you merge an AI / GPU config change, prepend an entry (newest first):

```markdown
## [YYYY-MM-DD] Short title  (PR #NNN)
Change · Why · Evidence · Risk/rollback · Verify
```

---

## [2026-06-14] vllm chat context 32k → 128k  (PR #982)

**Change:** `--ctx-size 32768` → `131072` in `kubernetes/apps/ai/vllm/app/helmrelease.yaml`
(the llama.cpp chat server). Raises the per-request context window from 32k to **128k**.

**Why it fits the 32 GiB card:**
- `Qwen3.6-35B-A3B` native window is **262144** (`max_position_embeddings`, `rope_type: default`,
  no yarn) → 128k needs **no rope/yarn scaling**.
- Hybrid arch: 40 layers, `full_attention_interval: 4` → only **10 layers** keep a growing KV
  cache; the other 30 are Gated DeltaNet (fixed-size state). With `num_key_value_heads: 2`,
  `head_dim: 256`, KV ≈ **20 KiB/token → ~2.6 GiB at 128k**.
- Live server runs `n_parallel = 4, kv_unified = true`, so the KV cache is one shared pool sized
  to `--ctx-size`; a single request can use the whole pool. Raising `--ctx-size` raises the
  per-request ceiling directly (no `-np` change needed).

**Evidence (live pod, 2026-06-14):**
`SYCL0 : Intel(R) Arc(TM) Pro B70 Graphics (32656 MiB, 32574 MiB free)`;
`n_parallel is set to auto, using n_parallel = 4 and kv_unified = true`; warning
`n_ctx_seq (32768) < n_ctx_train (262144)` confirmed 32k was the prior ceiling.

**Deliberately NOT changed:** no `-fa`, no `q8_0` KV (both broken on SYCL — see baseline). KV is
small enough without them.

**Risk / rollback:** a bigger KV pool narrows the VRAM headroom for ComfyUI + chat + embed running
at peak together. On a VRAM OOM (`out of device memory` in the `vllm` pod, especially with ComfyUI
active), step `--ctx-size` down to `98304` then `65536`.

**Verify:** `kubectl -n ai logs deploy/vllm -c app | grep -E "n_ctx|kv_unified|out of device memory"`
→ slots show `n_ctx = 131072`, no OOM; then a ~70k-token request through the `/vllm` gateway returns
a coherent, non-truncated response.
