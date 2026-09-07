# Session management

Not configured in this cluster.

The old guide used `GatewayParameters.spec.rawConfig` session settings. Live GatewayClass-level [`agentgatewayparameters.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/agentgatewayparameters.yaml) only sets `ADMIN_ADDR=0.0.0.0:15000`; the Gateway-level `internal-noauth-params` only sets Service `type: ClusterIP`. Neither carries session config. There is no session CR or policy under `app/`.

Chat Completions are request/response (streaming included). Do not expect gateway-side session resume, sticky MCP SSE sessions, or reconnection state from these manifests.
