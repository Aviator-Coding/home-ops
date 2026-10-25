# Flags that are load-bearing

Image for both live controllers: `ghcr.io/ggml-org/llama.cpp:server-intel-b10820`.
A tag bump is one evaluation for both. Re-read the banner after it.
`b10820` with `-ub 2048` is the chat serving shape (prefill chunks stop
fragmenting when one decode token shares a batch). `-ub 4096` was
rejected: better idle prefill, worse under concurrency.

Chat model: `IsValorum/Qwen3.8-35B-A3B-Distill-MTP-APEX-I-MiniPlus-V2.1-Abliterated-GGUF`
with no quant tag (default file is IQ3_XXS; mmproj auto-loads). Its served name
is `CHAT_MODEL_ID` (below), not a hardcoded alias. Embedding model:
`Qwen/Qwen3-Embedding-0.6B-GGUF:f16`.

## Model naming: one real name, stable roles

- The loaded model's real name is written once: `CHAT_MODEL_ID` in
  `kubernetes/apps/main/ai/chat-model.yaml`. Flux substitutes it (the `vllm`,
  `litellm` and `agentgateway` Kustomizations list the ConfigMap in
  `substituteFrom`) into the llama-server `--alias`, the `params.model` of the
  local LiteLLM CRs, the agentgateway failover provider, and the `model` label
  on the chat metrics.
- Consumers use roles, never the model name: `chat-local` (agentgateway, so
  Hermes and Linkwarden; LiteLLM keys and repo-wiki), `chat-ha`, `auto`.
  `GET /v1/models` on the gateway lists `chat-local` with the real name as
  `root`.
- The value is one token with no `/` (a slash matches the OpenRouter route).
- Gate: `scripts/ci/chat-model-id-test.py` (no stale literal, wiring intact);
  `litellm-fallback-chain-test.py` renders the CRs the way Flux does.

### Swapping the chat model

1. Change the `-hf` repo in `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`.
2. Change `CHAT_MODEL_ID` in `chat-model.yaml` to the new repo's model name.
3. Re-measure (`scripts/bench/b70-serving-harness.py --label <name>`), check
   VRAM headroom, and update the `ctx-size`/KV rows below if they moved.
4. Nothing else is renamed: Hermes, Linkwarden, LiteLLM keys and the dashboards
   follow. The new `model` label starts a fresh series, which is intended.
5. The chat pod restarts on merge (Recreate strategy).

## Chat (`vllm` controller)

| Pin | Value | If you drop it |
|---|---|---|
| `--ctx-size` | `262144` (native window, no yarn) | KV was measured 2720 MiB at this window with q8_0, not the older ~5.2 GiB estimate |
| `--flash-attn` | `on` | Serving shape changes; re-measure |
| `--cache-type-k/v` | `f16` / `f16` | q8_0 loses 4% decode at 2K and 16% at 64K against f16 (bench in `scripts/bench/results/`). The oneDNN pin stays until its own 48 h check; changing this re-opens [memory.md](memory.md). Keep the servicemonitor `kv` label equal to it |
| `--cache-ram` | `4096` | Silent 8192 MiB host cache |
| `GLIBC_TUNABLES` | `glibc.malloc.mmap_threshold=131072` | Logical cache bound does not return RSS. Do not remove when retuning `--cache-ram` |
| `GGML_SYCL_FA_ONEDNN` | `"0"` | Unbounded oneDNN SDPA partition cache. Must be numeric |
| `-b` / `-ub` | `2048` / `2048` | Default `-ub 512` splits a 2048 prefill into four passes |
| `--parallel`, `--kv-unified` | unset (auto) | Explicit pin overcommits KV and collapses decode |
| `--no-mmap` | set | Not the host-leak fix. Weights are file-backed and reclaimable. Do not treat adding or removing it as the memory remedy |
| `--cache-reuse` | `256` | No-op while the mmproj is loaded (multimodal disables it). Dropping the mmproj removes vision |

`n_parallel=4` and `kv_unified=true` must still appear in the banner
after a tag bump. Do not "make them explicit" to match the embedding
server.

## Embeddings

| Pin | Value | If you drop it |
|---|---|---|
| `--embedding` + `--pooling last` | on | Mean pooling is a different vector space. `pooling type = 3` in the banner |
| `--parallel` | `2` | Auto selected 4 slots and OOM-killed the pod under backfill |
| `--kv-unified` | on | Required **with** the pin above. Without it `n_ctx_slot` becomes 256 |
| `--cache-ram` | `0` | Write-only cache, 112 KiB/token, default ceiling 8192 MiB. `0` also disables the idle-slot writer. A small non-zero cap still allocates |
| `--ctx-size`, `-b`, `-ub` | `512`, `512`, `512` | One decision. Embedding mode forces `n_batch = n_ubatch`. 2048 fits but spends VRAM the chat restart needs |
| `--no-cont-batching` | on | Continuous batching is what steals the card from chat. Slot count does not do this |
| `--timeout` | `30` | Image default is 3600. A socket timeout only: a queued request made no socket IO and is never cut (live: 1042 requests at 28-31 s, all 200). Not a request bound; LiteLLM's `timeout` and caps are (contention.md) |
| `<|endoftext|>` added by the caller | do not | llama.cpp already applies EOS for last-token pooling. Hand-appended EOS measured worse agreement with the CPU server |

There is a real KV pool on the embedding server (56 MiB, 112 KiB/token
at ctx 512, F16). The "embedding has no KV" reading was the reserve pass
of a two-pass init. The oneDNN pin is still not required: the key space
is bounded and working set stayed flat.

Startup banner after any arg edit must show `n_slots = 2`,
`n_ctx_slot = 512`, `kv_unified = true`.

## `vllm-embed` controller

Same HelmRelease, `replicas: 0`, image
`docker.io/intel/llm-scaler-vllm:0.14.0-b8.3.1`, `--runner=pooling`.
Memory request 4Gi / limit 16Gi. It is mutually exclusive with chat on
this card (decode collapse under an embeddings flood). The live embedder
is `ai/embedding-gpu`. `--gpu-memory-utilization` belongs to this
controller only. llama.cpp will not honor it.

Do not put the oneDNN env var on this controller to "be consistent".
The CI gate fails if `GGML_SYCL_FA_ONEDNN` moves off the live `vllm`
container.
