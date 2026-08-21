# kmcp - not deployed here

kmcp was removed from this cluster on 2026-06-07 (`d16d3189` / #941,
`b49c8e25` / #942). Do not install kmcp charts, the kmcp CLI, or
`kagent.dev` `MCPServer` resources against this cluster.

This cluster's MCP operator is **ToolHive**. Live GVK:

```yaml
apiVersion: toolhive.stacklok.dev/v1alpha1
kind: MCPServer
```

That is a different API group from kmcp's `kagent.dev/v1alpha1` `MCPServer`
(some older dumps also used `kmcp.kagent.dev/v1alpha1`). Same kind name,
different operator, different spec. `kubectl apply` of a kmcp example will
not create a ToolHive server. `kubectl get mcpserver` is ambiguous if both
CRDs were ever installed; always name the group.

Authoritative manifests: `kubernetes/apps/ai/toolhive/`
(operator, `VirtualMCPServer/mcp-gateway-internal`, and per-server
`MCPServer` CRs under `mcp-servers/`). Hermes consumes the federated
gateway at `http://vmcp-mcp-gateway-internal.ai.svc.cluster.local:4483/mcp`.

The agent runtime is Hermes (`kubernetes/apps/ai/hermes`), not kagent.
See the [kagent tombstone](../kagent/README.md).

Upstream (historical): https://github.com/kagent-dev/kmcp
