# AI / B70 GPU changelog

One line per change that still matters. Current pins, the VA-API split, and
the DRA stop live in skills `b70-llm-serving` and `intel-gpu`. Git history
has the measurements.

- Chat is llama.cpp SYCL on `server-intel-b10820`, not vLLM (intel/llm-scaler#382 OOMs MoE warmup). Context 32k (PR #982) then 128k then 262144 (`07fe6828`). Flash-attn and q8_0 KV are on (`5d99d64f`). Stay on Qwen 3.6; 3.8 was evaluated and not adopted.
- `vllm-embed` scaled to 0 (PR #1098). Serving shape validated (PR #1092); an explicit `--parallel` pin was reverted (PR #1093).
- B70 split onto `devic.es/b70`; iGPUs force-probed. `siderolabs/i915` restored so `0xa7a0` firmware loads. `allowIDs: "0xa7a0"` scopes `gpu.intel.com/xe` to the iGPU.
- `devic.es/b70-vaapi` added after the PR #1443 rename killed VA-API while Level Zero stayed green. `tdarr-node` must request that resource.
- `pcie_port_pm=off` is in the Talos schematic. Applying it is `just talos upgrade-node`, skill `talos-nodes`.
- DRA was designed and not adopted. Do not ship it, and do not use `adminAccess: true`. Skill `intel-gpu`.
- intel-gpu-plugin `send on closed channel` on kubelet restart: restart the plugin pod, do not treat it as a lost card.
- `-ub 2048` plus image `server-intel-b10820` is the chat serving shape. Re-measure before the next tag.
- `ai/embedding-gpu` is the live embedder on the same card. Throttle it by request rate. `batch_size=4` is the measured-safe point. Larger batches OOM (still open). NaN vectors with a green `/health` recover on pod restart (root cause still open).
- Host prompt cache bounded with `--cache-ram 4096` and `GLIBC_TUNABLES` (PR #1731). Embeddings use `--cache-ram 0`.
- `GGML_SYCL_FA_ONEDNN: "0"` stops the oneDNN SDPA host leak (PR #1754). Numeric only. Post-merge checks are skill `b70-llm-serving` references/memory.md §8a and §8b.
- `VLLMMemoryRetainedAboveBound` ANDs its 8Gi floor with the live series so a terminated pod does not page.
