---
name: ai-stack
description: "Read before adding or retiring an app in the ai namespace, editing ToolHive MCPServer, VirtualMCPServer or EmbeddingServer resources, working on opencode, repo-wiki or searxng, or reviving a retired AI app (kagent, kmcp, agentmemory, comfyui). Covers the live inventory, MCPServer being toolhive.stacklok.dev, the vmcp embed timeout, and the retirement checklist."
---

# AI stack: inventory, ToolHive, and retirements

Namespace `ai` holds the agent runtime, MCP, local models, and the two
coding consumers. Routing, governance, and the B70 llama.cpp pins live in
other skills. This one owns what is deployed, how ToolHive is wired, and
what a retirement must not destroy.

## Tripwires

1. **`MCPServer` is `toolhive.stacklok.dev/v1alpha1`.** Never
   `kagent.dev`. Same kind name, different spec. kagent and kmcp were
   removed (PR #941, PR #942). kgateway is not deployed; non-AI ingress
   is Envoy Gateway in `network`.
2. **Do not point vmcp at `ai/embedding-gpu`.** The optimizer re-embeds
   the whole catalog on every session. The GPU path was measured and
   rejected (batch OOM, NaN, shared card). The CPU model stays
   `BAAI/bge-small-en-v1.5`. [toolhive.md](references/toolhive.md)
3. **`talos-mcp` is not live.** The Talos ServiceAccount controller
   rejects namespace `ai`. Re-enable needs `ai` and `os:reader` in
   `talos/machineconfig.yaml.j2`, then `just talos apply-node` on all
   three nodes. That file is not Flux. Skill `talos-nodes`.
4. **`MCPServer.spec.resources` lands on the proxy Deployment**, not the
   MCP container. A limit written there does not bound the server.
5. **grafana-mcp's Reloader annotation sits on
   `spec.podTemplateSpec.metadata.annotations`**
   (`secret.reloader.stakater.com/reload: toolhive-grafana`). The token
   is injected into the MCP StatefulSet. `resourceOverrides.proxyDeployment`
   does not roll that consumer. Env-injected secrets never refresh
   without a Reloader annotation on the workload that holds them.
6. **`kubectl-mcp-readonly` has an `aggregationRule`.** Hand-written
   rules on that ClusterRole are discarded. Grants live on
   `kubectl-mcp-readonly-base`, labeled into the aggregate. The binding
   targets the aggregate. Core Secrets stay omitted.
7. **Retiring an app: `grep -rn '<app>' scripts/ci/` first.** Several
   gates assert `path.is_file()` on a manifest. `task flux:test:all`
   stays green either way. Never delete a kopiur `Snapshot` CR: its
   finalizer deletes the kopia snapshot. Keep
   `ai/agentmemory-ceph-20260831215039`
   (kopia `b383822fe09da5adeaf99997bc977845`).
   [retirement.md](references/retirement.md)
8. **The MCP gateway's bearer token is enforced by Envoy on the LAN route
   only.** vmcp has no static-token mode, so the in-cluster Service stays
   open. Token: 1Password `mcp-gateway`, generated and pushed by
   `toolhive/config`. [toolhive.md](references/toolhive.md)
9. **opencode and repo-wiki call LiteLLM**, never a provider key.
   `http://litellm.ai.svc.cluster.local:4000/v1` via a
   `LiteLLMVirtualKey`. Skill `litellm-proxy`.

## Where things live

| What | Path |
|---|---|
| Namespace inventory (human pointer) | `kubernetes/apps/base/ai/Readme.md` |
| ToolHive operator, vmcp, EmbeddingServer | `kubernetes/apps/base/ai/toolhive/` |
| MCP servers | `kubernetes/apps/base/ai/toolhive/mcp-servers/` |
| OpenCode, repo-wiki | `kubernetes/apps/base/ai/opencode/`, `repo-wiki/` |
| SearxNG | `kubernetes/apps/base/ai/searxng/` |
| Session probe gate | `scripts/ci/toolhive-session-probe-test.py` |

Hermes, agentgateway, LiteLLM, vllm, and embedding-gpu each have their
own skill. Do not restate their pins here.

## Procedures

- What is deployed and which skill owns it: [inventory.md](references/inventory.md).
- ToolHive session embed, deactivated servers, RBAC: [toolhive.md](references/toolhive.md).
- Retire or revive an app: [retirement.md](references/retirement.md).
- OpenCode and repo-wiki wiring: [consumers.md](references/consumers.md).

## Verify

- `python3 scripts/ci/toolhive-session-probe-test.py`
- A green `/health` on vmcp does not mean a session can open. The Gatus
  check `ToolHive MCP Session` POSTs `initialize`. A fast embed failure
  still returns 200 and then drops the session; that shape is not covered
  by Gatus.
- Live (read-only): `kubectl -n ai get mcpserver,virtualmcpserver,embeddingserver`.
  `talos-mcp` absent is the healthy state.
