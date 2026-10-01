---
name: b70-llm-serving
description: "Read before editing kubernetes/apps/base/ai/vllm/** or ai/embedding-gpu/** (HelmReleases and PrometheusRules), bumping llama.cpp, changing any llama-server flag, env var or memory request, or verifying an embedding endpoint. Covers --cache-ram on both workloads, GGML_SYCL_FA_ONEDNN, NaN embeddings with green health, throttling by rate, and the memory alert tied to --cache-ram."
---

# B70 llama.cpp: chat (`ai/vllm`) and embeddings (`ai/embedding-gpu`)

Both workloads are `ghcr.io/ggml-org/llama.cpp:server-intel-b10820` on the
single Arc Pro B70 (`devic.es/b70`, PCI `0000:03:00.0`, device `0xe223`).
The Service is still named `vllm`. Chat is llama.cpp, not the vLLM engine:
real vLLM OOMs this MoE at warmup (intel/llm-scaler#382). Device nodes,
VA-API, and DRA live in skill `intel-gpu`.

Re-measure both workloads before changing the image tag. The two pins
below are invisible when a rename drops them.

## Tripwires

1. **Both pin `--cache-ram`. Absence restores 8192 MiB with no log line.**
   Chat is `"4096"` plus `GLIBC_TUNABLES=glibc.malloc.mmap_threshold=131072`.
   Embeddings are `"0"` (the cache is write-only there). Do not copy
   either value onto the other workload. No `limits.memory` can bound
   the default: the kernel SIGKILLs at page fault, so llama.cpp's
   `bad_alloc` handler never runs. Gates:
   `scripts/ci/vllm-prompt-cache-test.py`,
   `scripts/ci/embedding-gpu-prompt-cache-test.py`.
   [memory.md](references/memory.md)
2. **Chat pins `GGML_SYCL_FA_ONEDNN: "0"`. The value must be numeric.**
   ggml parses it with `sscanf(" %u")`. `"false"` and `"off"` leave the
   leak on. Embeddings do not need the pin (F16 KV, ctx 512, measured
   flat). Lift it only when the pinned tag bounds the oneDNN partition
   cache, and recheck the env name on every image bump. Gate:
   `scripts/ci/vllm-fa-onednn-test.py`. Post-merge checks are §8a and
   §8b in [memory.md](references/memory.md).
3. **Do not pin `--parallel` or `--kv-unified` on chat.** Auto selects
   `n_parallel=4` and `kv_unified=true`. Pinning overcommits KV and
   collapses decode. **Embeddings must pin both** `--parallel 2` and
   `--kv-unified`. `--parallel` alone sets `n_ctx_slot` to 256, half of
   `--ctx-size`. [flags.md](references/flags.md)
4. **Embeddings: `--pooling last`, no `<|endoftext|>` in the input, and
   `--ctx-size` / `-b` / `-ub` stay one triple at 512.** Mean pooling is
   a different vector space. Hand-appended EOS measured worse agreement
   with the CPU server. Embedding mode forces `n_batch = n_ubatch`.
5. **Never judge an embedding by shape, length, or non-zero count.**
   A null-filled vector scores 1024/1024 on `x != 0` (`None != 0` is
   true; nlohmann renders NaN as bare JSON `null`). Assert finite and
   non-zero, and prove the check fails on nulls. `/health` stays ok.
   A pod restart clears the NaN state. The root cause is still
   unattributed and GPU-side. `--kv-unified` is innocent.
   [embeddings.md](references/embeddings.md)
6. **`batch_size=4` is the measured-safe embedding point.** Larger
   batches OOM the pod. That fault is still open and is not the NaN
   drift and not the `--cache-ram` leak. Do not send a catalog-sized
   batch at this endpoint. Skill `ai-stack` for why vmcp stays on CPU.
7. **Throttle embeddings by request rate, not concurrency.** 0.5 req/s
   leaves chat near idle; 4 req/s costs most of chat decode; a full
   queue collapses it. The cap is the `embedding-local` LiteLLMModel
   (`rpm`, `max_parallel_requests`, `timeout`) plus two router settings;
   [contention.md](references/contention.md) says which and why.
   `--timeout 30` on the server does not bound a request. `devic.es/b70` `count: 99` is a share token, not
   a fence. Card memory is held by the chat weights. There is no
   `--gpu-memory-utilization` on llama.cpp. [contention.md](references/contention.md)
8. **`vllm-embed` stays `replicas: 0`.** It is `intel/llm-scaler-vllm`,
   not the live embedder. Re-enabling it is a live re-test, not a one
   line change.
9. **Chat memory request is 12Gi, limit 48Gi.** The request is the
   reservation (baseline plus the cache bound). The limit cannot stop a
   host leak. `VLLMMemoryRetainedAboveBound` floors at 8Gi **and** the
   live instant series, `for: 1h`. `VLLMMemoryExceedsRequest` is ratio
   > 1 for 30m. Raising `--cache-ram` without the floor fails CI
   (`scripts/ci/vllm-memory-alert-test.py`). Do not raise the request to
   silence the retained-memory alert.
10. **Inside a `devic.es/b70` container, `/sys/class/drm/card0` is the
    iGPU `0xa7a0`.** The device node is the B70. Read
    `/sys/bus/pci/devices/0000:03:00.0`. `xe` has no VRAM sysfs counters.
11. **A probe script under a Flux-reconciled file must not contain `$`.**
    envsubst empties `${...}` and can take down the Kustomization. Skill
    `flux-substitution`.

## Where things live

| What | Path |
|---|---|
| Chat HelmRelease (both controllers) | `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml` |
| Chat alerts | `kubernetes/apps/base/ai/vllm/app/prometheusrule.yaml` |
| Embedding HelmRelease | `kubernetes/apps/base/ai/embedding-gpu/app/helmrelease.yaml` |
| Prefill-by-depth bench (read-only) | `scripts/bench/vllm-prefill-by-depth.py` |

## Procedures

- Flag pins and what a banner must show: [flags.md](references/flags.md).
- Host memory, oneDNN, §8a/§8b, alerts: [memory.md](references/memory.md).
- NaN, batch OOM, correctness: [embeddings.md](references/embeddings.md).
- Sharing the card: [contention.md](references/contention.md).
- One-screen change log, one line per change with PR: [changelog.md](references/changelog.md).

## Verify

- `python3 scripts/ci/vllm-prompt-cache-test.py`
- `python3 scripts/ci/vllm-fa-onednn-test.py`
- `python3 scripts/ci/vllm-memory-alert-test.py`
- `python3 scripts/ci/embedding-gpu-prompt-cache-test.py`
- `python3 scripts/ci/embedding-values-test.py`
- After any embedding arg change, read the startup banner
  (`n_slots`, `n_ctx_slot`, `kv_unified`) rather than deriving it.
- An embedding check that cannot return non-200 or non-finite is not a
  check. Run it against a null vector before trusting a green result.
