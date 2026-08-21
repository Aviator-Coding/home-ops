# MCP connectivity

This cluster does **not** federate MCP through AgentGateway.

There is no `AgentgatewayBackend` with `type: MCP`, no `appProtocol: kgateway.dev/mcp`, and no kubernetes-mcp-server Deployment in `ai`. Guides that show `kind: Backend` MCP CRs (including the old kgateway copies) do not apply here.

## What runs instead: ToolHive

MCP servers are ToolHive `MCPServer` CRs under [`kubernetes/apps/ai/toolhive/`](../../../kubernetes/apps/ai/toolhive/):

- `mcp-servers/` - github, ha, flux, kubectl, talos, arr, seerr, comfyui, garmin-connect, agentmemory, ...
- `config/` - MCP group, virtual MCP server, HTTPRoute, telemetry, embedding server
- Flux Kustomizations `toolhive` + `toolhive-crds` in namespace `ai`

Point MCP clients at ToolHive, not at `internal-noauth` `/mcp` or similar AgentGateway routes.

Whether upstream AgentGateway can still federate MCP is a product capability. This cluster chose ToolHive. Do not add AgentGateway MCP Backends unless that is an explicit new design.
