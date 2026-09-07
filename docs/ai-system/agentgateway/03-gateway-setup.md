# Gateway setup (three data planes)

There is no single Gateway named `agentgateway` and no LB `10.50.0.30`.

**Source of truth for listeners, Service types, auth attachment, DNS opt-outs, and the http-listener exposure rule:** [`app/gateways/README.md`](../../../kubernetes/apps/base/ai/agentgateway/app/gateways/README.md). The YAML header comments on those Gateway files are the rest of the explanation. Do not restate the gateway table here.

## Admin UI routing notes

Admin UI HTTPRoutes (`httproute.yaml`, `httproute-internal.yaml`) 302 `/` -> `/ui/` because the dataplane's own 308 to `http://` dies behind TLS.

## Parameters

[`agentgatewayparameters.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/agentgatewayparameters.yaml) is the GatewayClass-level `AgentgatewayParameters` (`agentgateway.dev/v1alpha1`) and only sets `ADMIN_ADDR=0.0.0.0:15000`. It is not `gateway.kgateway.dev/GatewayParameters` and has no `spec.kube.agentgateway` block.

`internal-noauth` also references a Gateway-level `AgentgatewayParameters/internal-noauth-params` (defined in the same file as that Gateway) that sets `spec.service.spec.type: ClusterIP`. Gateway-level parameters **merge** with the GatewayClass-level ones rather than replacing them.

## Admin UI Service

[`service-admin-ui.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/service-admin-ui.yaml) selects the `internal` Gateway pods on port 15000. Envoy HTTPRoutes point at that Service, not at the LLM listeners.
