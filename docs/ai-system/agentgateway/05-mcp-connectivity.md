# MCP connectivity

This cluster does **not** federate MCP through AgentGateway.

There is no `AgentgatewayBackend` with `type: MCP`, no `appProtocol: kgateway.dev/mcp`, and no kubernetes-mcp-server Deployment in `ai`. Guides that show `kind: Backend` MCP CRs (including the old kgateway copies) do not apply here.

## What runs instead: ToolHive

MCP servers are ToolHive `toolhive.stacklok.dev/v1alpha1` `MCPServer` CRs under [`kubernetes/apps/base/ai/toolhive/`](../../../kubernetes/apps/base/ai/toolhive/):

- `mcp-servers/` - active set and deactivated notes live in that directory's `kustomization.yaml`
- `config/` - MCP group, virtual MCP server, HTTPRoute, telemetry, embedding server
- Flux Kustomizations `toolhive` + `toolhive-crds` (+ `toolhive-config` / `toolhive-mcp-servers`) in namespace `ai`

That is a different API group from the removed kmcp `kagent.dev/v1alpha1` `MCPServer`. Same kind name, different operator, different spec. Do not apply kmcp examples against this cluster. See the [kmcp tombstone](../kmcp/README.md). `kubectl get mcpserver` is ambiguous if both CRDs were ever installed; always name the group.

Point MCP clients at ToolHive, not at `internal-noauth` `/mcp` or similar AgentGateway routes.

Whether upstream AgentGateway can still federate MCP is a product capability. This cluster chose ToolHive. Do not add AgentGateway MCP Backends unless that is an explicit new design.
