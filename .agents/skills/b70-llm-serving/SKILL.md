---
name: b70-llm-serving
description: "Read before editing kubernetes/apps/base/ai/vllm/** or ai/embedding-gpu/** (HelmReleases and PrometheusRules), bumping llama.cpp, changing any llama-server flag, env var or memory request, or verifying an embedding endpoint. Covers --cache-ram on both workloads, GGML_SYCL_FA_ONEDNN, NaN embeddings with green health, throttling by rate, and the memory alert tied to --cache-ram."
---

# B70 LLM serving: vllm and embedding-gpu llama.cpp workloads

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`docs/ai/b70-llm-serving-tuning.md`](../../../docs/ai/b70-llm-serving-tuning.md) - tuning and benchmarking
- [`docs/ai/embedder-gpu-migration-analysis-2026-09-15.md`](../../../docs/ai/embedder-gpu-migration-analysis-2026-09-15.md) - embedding-gpu rate curve and NaN recovery
- [`docs/ai/vllm-host-prompt-cache.md`](../../../docs/ai/vllm-host-prompt-cache.md) - host prompt-cache leak
- [`docs/ai/vllm-onednn-sdpa-leak.md`](../../../docs/ai/vllm-onednn-sdpa-leak.md) - oneDNN SDPA leak
- [`kubernetes/apps/base/ai/vllm/app/prometheusrule.yaml`](../../../kubernetes/apps/base/ai/vllm/app/prometheusrule.yaml) - vllm memory alerts and replay method

`AGENTS.md` entries (search for the opening words):

- The B70 has two tenants and no compute partition
- Both B70 llama.cpp workloads must pin `--cache-ram`
- An embedding endpoint can return vectors of pure NaN

## Related skills

- `intel-gpu`
- `node-scheduling`
- `litellm-proxy`
