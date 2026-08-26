# AI / B70 GPU Stack Change Log

Track record of **deliberate configuration changes** to the `ai` namespace and the Intel
Arc Pro B70 GPU it runs on - what changed, why, the evidence behind it, and how to roll it
back. Mirrors [`ceph-cluster-changelog.md`](./ceph-cluster-changelog.md) (deliberate Ceph
changes) and [`hardware-incidents.md`](./hardware-incidents.md) (hardware failures), but for
the GPU / LLM-serving stack. Goal: when something breaks, answer "what did we change
recently, and why?" without spelunking git.

- **Hardware failures** → [`hardware-incidents.md`](./hardware-incidents.md)
- **Ceph config changes** → [`ceph-cluster-changelog.md`](./ceph-cluster-changelog.md)
- **This file** → chronological log of applied AI / GPU changes

> ⚠️ All AI config is GitOps under `kubernetes/apps/main/ai/` (Flux Kustomizations) and
> `kubernetes/apps/base/ai/` (manifests). Changes go through the relevant HelmRelease
> (e.g. `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`); Flux reconciles.
> Record every deliberate change here when you merge it.

---

## Current baseline (2026-08-25)

| Layer | Value |
|-------|-------|
| **GPU (discrete)** | 1× Intel Arc Pro B70 (Battlemage G31, 32 GiB / 32656 MiB reported), talos-3 OCuLink PCI `0000:03:00.0` |
| **GPU (iGPU)** | Raptor Lake `8086:a7a0` on all three nodes. Schematic carries `xe.force_probe=a7a0` + `i915.force_probe=!a7a0` (captain applies via `just talos apply-node`; not Flux) |
| **B70 resource** | `devic.es/b70: 99` via generic-device-plugin (`--domain=devic.es`, DRM by-path at `0000:03:00.0`) - **scheduling identity only, no VRAM fencing** |
| **xe pool** | `gpu.intel.com/xe: 99` via Intel GpuDevicePlugin - light QSV/browser (jellyfin/plex/playwright). B70 still included until iGPUs are confirmed after force_probe; follow-up `allowIDs: "0xa7a0"` drops B70 from xe |
| **On the B70** | chat (`vllm`) + optional `tdarr-node`. `vllm-embed` and `comfyui` are pinned `replicas: 0` |
| **Chat image** | `ghcr.io/ggml-org/llama.cpp:server-intel-b9592` (pin is load-bearing; do not float to `server-intel`) |
| **Chat window** | `--ctx-size 262144` (native max, no yarn), auto `n_parallel=4` + `kv_unified=true` |
| **Chat KV / FA** | `--flash-attn on`, `--cache-type-k/-v q8_0` (accepted on this pin; see 2026-08-21) |

### Workloads on the B70 (`devic.es/b70`)

| Pod | Image | Role | VRAM |
|-----|-------|------|------|
| `vllm` | `ghcr.io/ggml-org/llama.cpp:server-intel-b9592` | Chat - **llama.cpp SYCL**, `Qwen3.6-35B-A3B UD-Q4_K_M`. Keeps the `vllm` name so the service + gateway backend stay stable; real vLLM OOMs the MoE warmup (intel/llm-scaler#382). | ~21.4 GiB weights + ~5.2 GiB KV @262k q8_0 |
| `vllm-embed` | `intel/llm-scaler-vllm` | Embeddings - `Qwen3-VL-Embedding-2B`. **Default off** (`replicas: 0`); agentmemory moved to OpenRouter 2026-06-28. | (not resident) |
| `comfyui` | `intel/llm-scaler-omni` | Image generation. **Default off** (`replicas: 0`). | (not resident) |
| `tdarr-node` | tdarr (media ns) | QSV AV1 worker; codec needs the discrete card | light |

### SYCL / Battlemage constraints

- ✅ **`--flash-attn on` + `--cache-type-k/-v q8_0`** are the live, measured baseline on `server-intel-b9592` (enabled 2026-06-17, verified 2026-08-21). The 2026-06-14 prohibition citing ggml-org/llama.cpp#19276 does **not** apply to this official SYCL build on the B70.
- ⚠️ **Do not bump the llama.cpp tag without re-measuring.** Later SYCL FA/XMX and Q4_K MoE-reorder work landed after b9592; quality and hang reports on newer builds are a different stack (see 2026-08-21).
- ✅ For heavy ComfyUI work, scale `vllm`→0 first - the card is shared with no memory fencing. `comfyui`'s HelmRelease is suspended (`spec.suspend: true`), so scale it back to `replicas: 0` manually when done - Flux will not revert it.

---

## How to add an entry

When you merge an AI / GPU config change, prepend an entry (newest first):

```markdown
## [YYYY-MM-DD] Short title  (PR #NNN)
Change · Why · Evidence · Risk/rollback · Verify
```

---

## [2026-08-25] Split B70 onto `devic.es/b70`; force-probe a7a0 iGPUs

**Change:**
- `talos/schematic.yaml.j2`: `xe.force_probe=a7a0` + `i915.force_probe=!a7a0` (unquoted, same style as `module_blacklist=igc`). New factory ID `6b46d2c00a2295d31fef568ead1420f3088ac6adadc78abe1ff1c7ea8c1a6ef9` (was `ae2cb5793c9f8e61d2493f652b0e9251b2ffa525c5c17f3e4718b30d19940715`).
- `generic-device-plugin`: advertise discrete B70 as `devic.es/b70` (count 99, DRM by-path `0000:03:00.0` → in-container `card0`/`renderD128`, `--domain=devic.es`).
- Consumers moved off `gpu.intel.com/xe` onto `devic.es/b70`: `vllm`, `vllm-embed`, `comfyui`, `tdarr-node`. Placement is the extended resource, not hostname affinity.
- Intel GpuDevicePlugin keeps B70 in the `gpu.intel.com/xe` pool for now (jellyfin/plex/playwright). Follow-up once iGPUs advertise xe: `allowIDs: "0xa7a0"`.

**Why:** After the i915→xe migration, xe skips Raptor Lake `a7a0` by default, so only the B70 on talos-3 advertised `gpu.intel.com/xe`. When the B70 dropped off the PCIe bus, five GPU pods could not schedule. Restoring iGPUs as xe providers needs force_probe, but `gpu.intel.com/xe` is a bare share-count token with no VRAM fencing - once talos-1/2 advertise xe, the scheduler could place the ~21.4 GiB chat server on an iGPU. A dedicated `devic.es/b70` identity pins discrete consumers to the card without hostname affinity.

**Evidence:** Rendered schematic registered at factory.talos.dev under the new ID; `talosctl validate -m metal` clean on all three node configs; installer v1.13.9 and Kubernetes pins v1.36.3 match live (no downgrade risk). B70 recovered via ordered cold cycle and enumerates as Battlemage G31 at `0000:03:00.0`.

**Risk / rollback:** Schematic/kernel-arg changes are **not** Flux-applied - captain rolls `just talos apply-node` one node at a time. Revert the two force_probe args and re-apply to undo iGPU bind; move consumers back to `gpu.intel.com/xe` and drop the `b70` generic-device-plugin entry to undo the resource split. Do not set `allowIDs: "0xa7a0"` until every node advertises xe from its iGPU, or jellyfin/plex/playwright lose their only xe provider.

**Verify:** After each `apply-node`, confirm iGPU xe allocatable and B70 identity:
```sh
kubectl get nodes -o json | jq -r '.items[] | "\(.metadata.name): xe=\(.status.allocatable."gpu.intel.com/xe" // "0") b70=\(.status.allocatable."devic.es/b70" // "0")"'
```
Expect `devic.es/b70=99` only on talos-3; after force_probe, `gpu.intel.com/xe` on all three.

---

## [2026-08-21] Qwen3.6 → 3.8 upgrade evaluated, staying on 3.6

**Change:** None. HelmRelease, agentgateway config, and hermes config are unchanged -
chat stays on `Qwen3.6-35B-A3B UD-Q4_K_M`. Docs updated with the investigation
(`docs/ai/b70-llm-serving-tuning.md` §2/§5) so this isn't re-litigated from scratch.

**Why considered:** Routine check for a Qwen3.6 → 3.8 model bump on the local chat
model, prompted by the general Qwen3.8 release wave (`Qwen3.8-27B` dense, 2026-08-05).

**Why rejected - two independent blockers:**
- **No comparable model exists.** As of 2026-08-21, Alibaba has shipped only
  `Qwen3.8-27B` (dense) and `Qwen3.8-2.4T-A95B` (2.4T total / 95B active MoE, far too
  large for one 32 GiB card). No `35B-A3B`-class MoE successor to the currently-running
  model exists; a GitHub sighting of one was confirmed a typo, not a real release
  (`Qwen/Qwen3.8-27B` discussion #120).
- **The dense alternative can't run on this backend.** `Qwen3.8-27B` GGUF quants do
  exist (unsloth/bartowski, `UD-Q4_K_M` at 16.5 GiB) and would in principle fit VRAM,
  but the architecture is a hybrid: 48/64 layers Gated DeltaNet (linear attention/SSM),
  16/64 full attention. `ggml-sycl` (this cluster's backend) has no SSM_SCAN/SSM_CONV/
  GATED_DELTA_NET kernels - open upstream gap, ggml-org/llama.cpp#19957 - and there's a
  live report of exactly this shape SIGSEGV-crashing on Intel SYCL for hybrid archs
  (ollama#15966: `qwen3.5moe`, `qwen3next`). This is independent of the llama.cpp image
  tag; bumping past `b9592` does not add the missing kernels.

**Risk/rollback:** n/a, no config changed.

**Verify:** n/a. Revisit when either (a) Alibaba ships a `35B-A3B`-class Qwen3.8 MoE, or
(b) `ggml-sycl` gains SSM/DeltaNet kernel support upstream - check
`ggml-org/llama.cpp#19957` for status before re-attempting the dense 27B path.

---

## [2026-08-21] flash-attn + q8_0 KV accepted on `server-intel-b9592`

**Change:** Docs only. Rewrote the current baseline to match live GitOps and **lifted
the 2026-06-14 "do not enable flash-attn / q8_0 KV" prohibition.** HelmRelease args are
unchanged (`kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`: `--flash-attn on`,
`--cache-type-k/-v q8_0`).

**Why the 2026-06-14 prohibition was wrong for this stack:**
- ggml-org/llama.cpp#19276 (the cited bug) was filed 2026-02-02 against the **IPEX-LLM
  XPU container**, whose llama.cpp is a closed-source fork. intel/ipex-llm was archived
  2026-01-28. Hardware in that report is an **Arrow Lake-H iGPU**, not this Arc Pro B70.
  Maintainer NeoZhangJianyu: IPEX-LLM is unsupported. Closed 2026-03-14 as `not_planned`
  (stale) - never confirmed, never fixed on official SYCL.
- Official ggml-org SYCL already had flash-attention tile/vector kernels by 2026-04-09
  (PR #21654 extends existing FA for head size 512). This cluster's chat server is
  `ghcr.io/ggml-org/llama.cpp:server-intel-b9592` (published 2026-06-10), not IPEX-LLM
  and not vLLM. Embeddings (`intel/llm-scaler-vllm`) never used these flags.
- Same image at the prohibition (PR #982, 2026-06-14) and at enablement (`5d99d64f`,
  2026-06-17). No llama.cpp or vLLM release in that three-day window "fixed" #19276;
  the flags were turned on on the build the changelog had already pinned.

**Evidence these flags are safe on this pin:**
- Enabled 2026-06-17 (`5d99d64f` / `2eabcc67`) and still live 2026-08-21 - **two months**
  of production. No home-ops issue, commit, alert, or AI-doc note of output corruption,
  FA segfaults, or a revert of these flags. The 2026-06-26 tuning runbook
  (`docs/ai/b70-llm-serving-tuning.md`) measured SYCL decode **~61 t/s** with
  `q8_0/q8_0` + `--flash-attn on` and recorded **7 days, 4 slots, no SEGV**.
- 2026-07-08 (`07fe6828`): `--ctx-size 262144` live-tested on the same flags, **0
  restarts, 68.4 t/s decode**. q8_0 is required for that KV to fit (~5.2 GiB at 262k).
- Completions used for those timings returned coherent `predicted_per_second` samples;
  Hermes has used this server as the local chat backend throughout.

**Not a reason to revert live config, but a reason not to float the tag:**
- ggml-org/llama.cpp#25692 (open): FA + q8_0/q8_0 GPU hang (xe CCS engine reset) on a
  B70 under concurrent load, on **newer master**, original host kernel 6.17.0-1028-oem.
  Reporter could not reproduce after moving the same card to a newer xe kernel +
  OCuLink; maintainer suspected that OEM kernel. This cluster is Talos + OCuLink on
  b9592, and has not logged that hang.
- ggml-org/llama.cpp#25761 (closed duplicate): Qwen3.6-35B-A3B thinking-mode gibberish
  on a B70 in the **b9802 → b10034** window (after this pin). XMX FA (PR #25222,
  2026-07-15) also landed after b9592.
- ggml-org/llama.cpp#24168 (open): hybrid-model empty/gibberish on **B60** around
  b9128-b9479. Does not match measured B70 / b9592 production.

**Risk / rollback:** leave HelmRelease as-is. If a future image bump regresses quality
or hangs, revert the tag to `server-intel-b9592` before touching FA/q8_0; those flags
are required for 262k to fit.

**Verify:** `kubectl -n ai exec deploy/vllm -c app -- sh -lc 'curl -s localhost:8000/props'`
shows flash-attn on + q8_0 K/V; a short `/completion` returns coherent text and a
non-zero `predicted_per_second` (see the tuning runbook reproduce block).

---

## [2026-07-08] vllm chat context 128k → 262144  (`07fe6828`)

**Change:** `--ctx-size 131072` → `262144` in `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`.
Native window, no yarn. Fits only because `vllm-embed` and `comfyui` are both `replicas: 0`.

**Why:** 115 "failed to find free space in the KV cache" warnings at 131072 - four
concurrent slots oversubscribing the shared unified pool, not one long chat.

**Evidence:** live-tested on the empty card: clean load, 0 restarts, **68.4 t/s decode**.
KV grows ~2.6 → ~5.2 GiB at q8_0. Recorded in `docs/ai/b70-llm-serving-tuning.md` §5.

**Risk / rollback:** re-enabling embeddings or ComfyUI needs re-validation; step back
toward 131072 on VRAM OOM.

---

## [2026-06-28] scale vllm-embed to 0  (PR #1098)

**Change:** `vllm-embed` `replicas: 0`. Controller + PVC kept. agentmemory moved to
OpenRouter embeddings (`text-embedding-3-small`).

**Why:** free the B70 `gpu.intel.com/xe` slot and ~5-6.5 GiB VRAM so chat can own the
card. Restoring local embeddings is a one-line revert (`replicas: 0` → `1`).

---

## [2026-06-26] B70 serving validated; ComfyUI default-off  (PR #1092)

**Change:** Measured SYCL vs Vulkan and the live llama.cpp args on `server-intel-b9592`.
Pinned ComfyUI `replicas: 0` in git. Full write-up: `docs/ai/b70-llm-serving-tuning.md`.

**Evidence:** SYCL decode **~61 t/s** vs Vulkan ~36 t/s on the MoE chat model. `q8_0` KV
+ `--flash-attn on` kept (VRAM-bound, not a speed trade). Explicit `--parallel` /
`--kv-unified` pin was tried and **reverted the next day** (PR #1093) - on b9592 the
explicit flags overcommit KV and collapse decode to ~0.5 t/s; leave auto.

**Isolation:** chat decode collapses ~38× (61 → 1.6 t/s) under an embeddings flood.
The B70 has no hardware compute partition. Run ComfyUI only after scaling `vllm` to 0.

---

## [2026-06-17] enable flash-attn, q8_0 KV, cache-reuse, no-mmap  (`5d99d64f`)

**Change:** in `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`:
- `--flash-attn` (follow-up `2eabcc67` passes explicit `on`; the flag requires on|off|auto)
- `--cache-type-k/-v q8_0`
- `--cache-reuse 256`
- `--no-mmap`
- `UR_L0_ENABLE_RELAXED_ALLOCATION_LIMITS=1`, `SYCL_PI_LEVEL_ZERO_USE_IMMEDIATE_COMMANDLISTS=1`

**Why (commit rationale):** unlock KV quantization + better attention memory access;
halve KV VRAM at 131k; prefix reuse for Hermes system prompts; avoid Ceph RBD
page-fault jitter on load; Intel L0 alloc + dispatch opts.

**Evidence at the time:** none recorded in this changelog (gap closed 2026-08-21).
Subsequent production measurements are in the 2026-06-26 and 2026-08-21 entries.

---

## [2026-06-14] vllm chat context 32k → 128k  (PR #982)

**Change:** `--ctx-size 32768` → `131072` in `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`
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

**Deliberately NOT changed at this commit:** no `-fa`, no `q8_0` KV. That choice was
the 2026-06-14 belief (citing ggml-org/llama.cpp#19276). **Superseded 2026-06-17** when
those flags were enabled on the same `server-intel-b9592` image; see 2026-08-21 for why
#19276 does not apply here. KV at 128k was small enough without them (~2.6 GiB).

**Risk / rollback:** a bigger KV pool narrows the VRAM headroom for ComfyUI + chat + embed running
at peak together. On a VRAM OOM (`out of device memory` in the `vllm` pod, especially with ComfyUI
active), step `--ctx-size` down to `98304` then `65536`.

**Verify:** `kubectl -n ai logs deploy/vllm -c app | grep -E "n_ctx|kv_unified|out of device memory"`
→ slots show `n_ctx = 131072`, no OOM; then a ~70k-token request through the `/vllm` gateway returns
a coherent, non-truncated response.
