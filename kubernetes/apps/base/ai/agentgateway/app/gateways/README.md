# AgentGateway data-plane Gateways

Three `gatewayClassName: agentgateway` Gateways in namespace `ai`. Cilium `lbipam.cilium.io/ips` pins keep the Service IPs across recreation (the `ai-system` -> `ai` namespace move already ran). HTTPS listeners terminate with `sklab-dev-production-tls` for `*.${SECRET_DOMAIN}`.

| Gateway | LB IP | Auth | Typical clients |
|---------|-------|------|-----------------|
| [`internal.yaml`](./internal.yaml) | `10.50.0.27` | Authentik extAuth on **https** | Browser / SSO |
| [`internal-noauth.yaml`](./internal-noauth.yaml) | `10.50.0.28` | None (keyless) | In-cluster workloads: `http://internal-noauth.ai.svc.cluster.local/v1` |
| [`public.yaml`](./public.yaml) | `10.50.0.29` | API-key Strict on **http**; Authentik on **https** | External API via Envoy `llm-api.${SECRET_DOMAIN}` -> `public:80` |

[`tls-redirect.yaml`](./tls-redirect.yaml) is a catch-all HTTP->HTTPS 301 on all three `http` listeners. Path-specific `/v1` routes on `public` http win for API traffic, which is how Envoy can forward `llm-api.${SECRET_DOMAIN}` to `public:80` without the redirect stealing those requests.

Policies:

- Authentik: `../policies/authentik-policy.yaml` targets `internal` https and `public` https.
- API keys: `../policies/apikey-policy.yaml` targets `public` http. Keys from 1Password item `ai-gateway-keys`.
- Model routing: `../policies/model-routing-policy.yaml` copies JSON `model` into `x-model` before route selection on all three Gateways.
- Tracing: `../policies/tracing-policy.yaml` OTLP gRPC to Tempo `:4317` on all three.

The LLM entry point is **not** these Gateway YAML files - it is the unified `/v1` HTTPRoute:

- [`../httproute-unified.yaml`](../httproute-unified.yaml) - model-name routing (read the header comments)
- [`../httproute-models.yaml`](../httproute-models.yaml) - static `GET /v1/models` catalog

Admin UI (port 15000 `/ui/`):

- [`../httproute.yaml`](../httproute.yaml) - `agentgateway.${SECRET_DOMAIN}`
- [`../httproute-internal.yaml`](../httproute-internal.yaml) - `llm.${SECRET_DOMAIN}`
- [`../service-admin-ui.yaml`](../service-admin-ui.yaml)

External API:

- [`../httproute-api-external.yaml`](../httproute-api-external.yaml) - `llm-api.${SECRET_DOMAIN}`
