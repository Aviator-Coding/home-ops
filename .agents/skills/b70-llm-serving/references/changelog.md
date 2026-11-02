# AI / B70 GPU change log

One line per change that still matters. Current pins, the VA-API split, and
the DRA stop live in skills `b70-llm-serving` and `intel-gpu`. Git history
has the measurements.

- Chat is llama.cpp SYCL on `server-intel-b10820`, not vLLM (intel/llm-scaler#382 OOMs MoE warmup). Context 32k (PR #982) then 128k then 262144 (direct commit `07fe6828`, no PR). Flash-attn and q8_0 KV are on (direct commit `5d99d64f`, no PR).
- Chat weights are `empero-ai/Qwen3.8-35B-A3B-Distill-GGUF:Q3_K_M` (mmproj auto-loaded), moved from IsValorum's abliterated IQ3_XXS APEX build: decode 63 to 81 t/s, about 25-35% fewer thinking tokens, VRAM 24.1 to 26.4 GiB at 262K, and no loss on the graded suite. Abliteration is gone. Reason and numbers in references/flags.md. The served name is `CHAT_MODEL_ID` (see the naming entry at the end).
- `vllm-embed` scaled to 0 (PR #1098). Serving shape validated (PR #1092); an explicit `--parallel` pin was reverted (PR #1093).
- B70 split onto `devic.es/b70`; iGPUs force-probed (PR #1443). `siderolabs/i915` restored so `0xa7a0` firmware loads (PR #1444). `allowIDs: "0xa7a0"` scopes `gpu.intel.com/xe` to the iGPU (PR #1447).
- `devic.es/b70-vaapi` added after the PR #1443 rename killed VA-API while Level Zero stayed green (fix PR #1490). `tdarr-node` must request that resource.
- `pcie_port_pm=off` is in the Talos schematic (PR #1487). Applying it is `just talos upgrade-node`, skill `talos-nodes`.
- DRA was designed and not adopted (PR #1446). Do not ship it, and do not use `adminAccess: true`. Skill `intel-gpu`.
- intel-gpu-plugin `send on closed channel` on kubelet restart (PR #1607): restart the plugin pod, do not treat it as a lost card.
- `-ub 2048` plus image `server-intel-b10820` is the chat serving shape (PR #1621). Re-measure before the next tag.
- `ai/embedding-gpu` is the live embedder on the same card (PR #1702). Throttle it by request rate at LiteLLM (`rpm`, in-flight cap, timeout on the `embedding-local` CR); PR #1821's `--timeout 30` was disproved as a request bound by a 500-worker live run. `batch_size=4` is the measured-safe point (PRs #1708, #1821). Larger batches OOM (still open). NaN vectors with a green `/health` recover on pod restart (root cause still open; probes PR #1711).
- Host prompt cache bounded with `--cache-ram 4096` and `GLIBC_TUNABLES` (PR #1731). Embeddings use `--cache-ram 0` (PR #1710).
- `GGML_SYCL_FA_ONEDNN: "0"` stops the oneDNN SDPA host leak (PR #1754). Numeric only. Post-merge checks are skill `b70-llm-serving` references/memory.md §8a and §8b.
- `VLLMMemoryRetainedAboveBound` ANDs its 8Gi floor with the live series so a terminated pod does not page (PR #1756).
- Chat KV moved q8_0 to f16 with the oneDNN pin kept: decode +4% at 2K, +14% at 16K, +16% at 64K, prefill unchanged, VRAM 23.5 of 32.6 GiB (bench: `scripts/bench/results/`, depth sweep from PR #2056). Lifting the pin gave +20% prefill at 32K and +40% at 64K with no leak in a 0.8M-token census, but §8a needs 48 h of real traffic first, so the pin is still on.
- Chat decode fell to about 40 t/s while `tdarr-node` held 12 of talos-3's 20 cores (64 t/s with it stopped). Fix: chat `-t 8` and `cpu: "8"` requested, `tdarr-node` limited to 6 cores. Detail in references/contention.md.
- Chat moved to `-b 8192 -ub 4096` and `--spec-type ngram-mod` after a paused-window A/B: prefill +14% at 16K and +11% at 64K, decode unchanged, 4 concurrent 16K requests 6% faster. `draft-mtp` measured slower at short context and under concurrency, so it is not on. `-t` 4, 8 and 12, `SYCL_PI_LEVEL_ZERO_USE_IMMEDIATE_COMMANDLISTS=0` and the sergiiob.dev env set made no difference with tdarr stopped; `--flash-attn off` needs a 32 GiB compute buffer at 262K and does not start. Results are `scripts/bench/results/{night,day}-*.json`.
- Chat got `PriorityClass chat-gpu-high` (1100000) so its 8-CPU request cannot be starved or preempted by a CI burst on talos-3, and `tdarr-node` dropped to 4 cores (16K prefill 1853 to 1951 t/s with tdarr saturated, 2079 with it stopped). After the `-ub 4096` rollout VRAM was 24.75 of 31.89 GiB used, about 7.1 GiB free (was about 9).
- The stale `qwen3.6-35b-a3b` name is gone: the loaded model's real name is `CHAT_MODEL_ID` in ConfigMap `chat-model` (apps/main/ai), substituted by Flux into the llama-server alias, the local LiteLLM CRs, the agentgateway failover provider and the `model` metrics label. Consumers use roles (`chat-local`): Hermes and Linkwarden moved off the raw name, the demo fixture is `demo-priced-local`, the classifier is `chat-local-classifier`. A model swap is two edits (`-hf` and `CHAT_MODEL_ID`); procedure in references/flags.md.
