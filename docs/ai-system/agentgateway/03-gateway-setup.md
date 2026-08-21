# Gateway setup (three data planes)

There is no single Gateway named `agentgateway` and no LB `10.50.0.30`. Live Gateways:

| Name | File | IP | Listeners |
|------|------|----|-----------|
| `internal` | `app/gateways/internal.yaml` | `10.50.0.27` | http:80, https:443 `*.${SECRET_DOMAIN}` |
| `internal-noauth` | `app/gateways/internal-noauth.yaml` | `10.50.0.28` | same |
| `public` | `app/gateways/public.yaml` | `10.50.0.29` | same |

All use `gatewayClassName: agentgateway`. HTTPS terminates with secret `sklab-dev-production-tls`. IPs are pinned with `lbipam.cilium.io/ips` so recreation (including the already-done `ai-system` -> `ai` move) keeps them.

Read the in-tree notes first: [`app/gateways/README.md`](../../../kubernetes/apps/ai/agentgateway/app/gateways/README.md). The YAML header comments on those Gateway files are the rest of the explanation.

## Auth attachment (by listener)

- `internal` **https**: Authentik extAuth (`policies/authentik-policy.yaml`)
- `internal-noauth`: none, on purpose
- `public` **http**: API-key Strict (`policies/apikey-policy.yaml`) - this is what Envoy hits for `llm-api.*`
- `public` **https**: Authentik

## Redirects

`app/gateways/tls-redirect.yaml` is a catch-all HTTP->HTTPS 301 on all three `http` listeners. Path-specific `/v1` HTTPRoutes on `public` http take precedence, so API traffic to port 80 is not redirected.

Admin UI HTTPRoutes (`httproute.yaml`, `httproute-internal.yaml`) 302 `/` -> `/ui/` because the dataplane's own 308 to `http://` dies behind TLS.

## Parameters

[`agentgatewayparameters.yaml`](../../../kubernetes/apps/ai/agentgateway/app/agentgatewayparameters.yaml) is kind `AgentgatewayParameters` (`agentgateway.dev/v1alpha1`) and only sets `ADMIN_ADDR=0.0.0.0:15000`. It is not `gateway.kgateway.dev/GatewayParameters` and has no `spec.kube.agentgateway` block.

## Admin UI Service

[`service-admin-ui.yaml`](../../../kubernetes/apps/ai/agentgateway/app/service-admin-ui.yaml) selects the `internal` Gateway pods on port 15000. Envoy HTTPRoutes point at that Service, not at the LLM listeners.
