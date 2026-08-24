# kagent - not deployed here

kagent was removed from this cluster on 2026-06-07 (`d16d3189` / #941,
`b49c8e25` / #942). Do not install the kagent Helm chart, CLI, or
`kagent.dev` CRDs against this cluster.

This cluster's live AI stack:

| Job | What runs | GitOps |
| --- | --- | --- |
| Agent runtime | Hermes | `kubernetes/apps/base/ai/hermes` |
| LLM routing | agentgateway | `kubernetes/apps/base/ai/agentgateway` |
| MCP servers | ToolHive | `kubernetes/apps/base/ai/toolhive` |

MCP uses ToolHive's `toolhive.stacklok.dev/v1alpha1` `MCPServer`, **not**
kagent's `kagent.dev/v1alpha1` `MCPServer`. Same kind name, different API
group, different spec. See the [kmcp tombstone](../kmcp/README.md).

Upstream (historical): https://github.com/kagent-dev/kagent
