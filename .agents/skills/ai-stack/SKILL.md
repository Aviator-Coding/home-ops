---
name: ai-stack
description: "Read before adding or retiring an app in the ai namespace, editing ToolHive MCPServer, VirtualMCPServer or EmbeddingServer resources, working on opencode, repo-wiki or searxng, or reviving a retired AI app (kagent, kmcp, agentmemory, comfyui). Covers the live inventory, MCPServer being toolhive.stacklok.dev, the vmcp embed timeout, and the retirement checklist."
---

# AI stack: ai namespace inventory, ToolHive and retirements

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`kubernetes/apps/base/ai/Readme.md`](../../../kubernetes/apps/base/ai/Readme.md) - namespace overview (inventory incomplete)
- [`docs/ai-system/toolhive-optimizer-embedding-timeout-2026-09-27.md`](../../../docs/ai-system/toolhive-optimizer-embedding-timeout-2026-09-27.md) - ToolHive embed timeout
- [`docs/ai-system/retired-2026-08-22.md`](../../../docs/ai-system/retired-2026-08-22.md) - apps retired 2026-08-22
- [`docs/ai-system/agentmemory-retirement-2026-08-31.md`](../../../docs/ai-system/agentmemory-retirement-2026-08-31.md) - agentmemory retirement
- [`docs/ai-system/comfyui-retirement-2026-09-15.md`](../../../docs/ai-system/comfyui-retirement-2026-09-15.md) - comfyui retirement
- [`docs/ai-system/kagent/README.md`](../../../docs/ai-system/kagent/README.md) - kagent tombstone
- [`docs/ai-system/kmcp/README.md`](../../../docs/ai-system/kmcp/README.md) - kmcp tombstone
- [`docs/ai-system/kgateway/README.md`](../../../docs/ai-system/kgateway/README.md) - kgateway tombstone
- [`kubernetes/apps/base/ai/opencode/README.md`](../../../kubernetes/apps/base/ai/opencode/README.md) - opencode
- [`kubernetes/apps/base/ai/repo-wiki/README.md`](../../../kubernetes/apps/base/ai/repo-wiki/README.md) - repo-wiki

`AGENTS.md` entries (search for the opening words):

- kagent / kmcp are not deployed
- Retiring an app can break a `scripts/ci/*-test.py` gate

## Related skills

- `hermes-agent`
- `agentgateway`
- `litellm-proxy`
- `b70-llm-serving`
