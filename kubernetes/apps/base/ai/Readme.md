# ai namespace

GitOps for this cluster's AI stack. Flux overlays: `kubernetes/apps/main/ai/`.

| Job | App | Skill |
| --- | --- | --- |
| Agent runtime | `hermes/` | `hermes-agent` |
| LLM routing | `agentgateway/` | `agentgateway` |
| LLM governance | `litellm/`, `litellm-operator/`, `litellm-pgvector/` | `litellm-proxy` |
| MCP | `toolhive/` | `ai-stack` |
| Local chat and embeddings | `vllm/`, `embedding-gpu/` | `b70-llm-serving` |
| GPU devices and telemetry | `gpu-node-dashboard/` plus the device plugins under `system/` | `intel-gpu` |
| Coding consumers | `opencode/`, `repo-wiki/` | `ai-stack` |
| Web search | `searxng/` | `hermes-agent` (URL pin), `ai-stack` (deploy) |
| Shared files | `samba/`, `pvc/` | `hermes-agent` |

kagent, kmcp, and kgateway are not deployed. Skill `ai-stack` has the
retirement checklist and the one Snapshot CR that must not be deleted.
GPU changelog: `docs/ai-gpu-changelog.md`.
