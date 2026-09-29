# What runs in `ai`

Flux Kustomizations under `kubernetes/apps/main/ai/`. Each row names the
skill that owns the pins. LiteLLM rows are skill `litellm-proxy` and are
listed only so a retirement does not treat them as unused.

| Kustomization | Role | Skill |
|---|---|---|
| `hermes` | Operator agent | `hermes-agent` |
| `agentgateway`, `agentgateway-crds`, `agentgateway-dashboards` | OpenAI-compatible gateway | `agentgateway` |
| `litellm`, `litellm-operator`, `litellm-pgvector` | Governance, keys, vector store | `litellm-proxy` |
| `toolhive-crds`, `toolhive`, `toolhive-config`, `toolhive-mcp-servers` | MCP CRDs, operator, vmcp, servers | this skill |
| `vllm` | Chat llama.cpp on the B70. The Service name stays `vllm` | `b70-llm-serving` |
| `embedding-gpu` | Embedding llama.cpp on the same card | `b70-llm-serving` |
| `opencode`, `repo-wiki` | LiteLLM consumers | this skill |
| `searxng` | Web search backend Hermes calls | this skill (URL pin is `hermes-agent`) |
| `samba`, `ai-pvc` | Shared storage | `hermes-agent` |
| `gpu-node-dashboard` | B70 hwmon panels | `intel-gpu` |

Live MCP servers (each a `toolhive.stacklok.dev/v1alpha1` `MCPServer`):
`arr`, `flux`, `github`, `grafana-mcp`, `kubectl`, `kubesearch`.
`VirtualMCPServer/mcp-gateway-internal` federates them.
`EmbeddingServer/mcp-tools-embedding` is the CPU model on the session path.
Hermes connects to
`http://vmcp-mcp-gateway-internal.ai.svc.cluster.local:4483/mcp`.

## Not deployed

- kagent, kmcp (PR #941, PR #942). Do not install their charts or
  `kagent.dev` CRDs.
- kgateway. AgentGateway is the standalone chart at
  `oci://ghcr.io/agentgateway/charts/agentgateway`. Skill `agentgateway`.
- `open-webui`, `kokoro`, `miso-gallery`, `open-notebook`, `perplexica`,
  `qdrant` (retired together). `agentmemory`. `comfyui` and `comfyui-mcp`.
  Revival is [retirement.md](retirement.md), not a compatibility check
  against the old objects.
- `vllm-embed` is a controller inside the `vllm` HelmRelease held at
  `replicas: 0`. It is not the live embedder. Skill `b70-llm-serving`.

`kubernetes/apps/base/ai/Readme.md` is the short human index. When it and
this table disagree, trust `kubectl -n ai get ks` and the overlay
directory, then fix the index.
