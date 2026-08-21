# ai namespace

GitOps for this cluster's AI stack. Flux entry: `kustomization.yaml` in this
directory. Live apps (not kagent, kmcp, or LiteLLM):

| Job | App | Notes |
| --- | --- | --- |
| LLM routing | `agentgateway/` | Unified OpenAI-style `/v1`. Manifests under `agentgateway/app/` (especially `httproute-unified.yaml` and `gateways/`). |
| Agent runtime | `hermes/` | Homelab operator. Config is GitOps (`app/resources/config.yaml`). |
| MCP servers | `toolhive/` | Operator + `toolhive.stacklok.dev/v1alpha1` `MCPServer` CRs. **Not** kagent.dev's `MCPServer`. |
| Local chat GPU | `vllm/` | llama.cpp SYCL on the B70; service keeps the `vllm` name. |
| Chat UI | `open-webui/` | `https://chat.${SECRET_DOMAIN}` (also `open-webui.${SECRET_DOMAIN}`). |
| Memory | `agentmemory/` | Long-term memory for Hermes and ToolHive. Embeddings via OpenRouter, not the B70. |

kagent and kmcp were removed on 2026-06-07 (#941 / #942). Their remaining docs
are tombstones: `docs/ai-system/kagent/README.md`, `docs/ai-system/kmcp/README.md`.

GPU runbooks: `docs/ai/`, incident log `docs/ai-gpu-changelog.md`.
