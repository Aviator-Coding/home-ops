# Session management

Not configured in this cluster.

The old guide used `GatewayParameters.spec.rawConfig` session settings. Live [`agentgatewayparameters.yaml`](../../../kubernetes/apps/ai/agentgateway/app/agentgatewayparameters.yaml) only sets `ADMIN_ADDR=0.0.0.0:15000`. There is no session CR or policy under `app/`.

Chat Completions are request/response (streaming included). Do not expect gateway-side session resume, sticky MCP SSE sessions, or reconnection state from these manifests.
