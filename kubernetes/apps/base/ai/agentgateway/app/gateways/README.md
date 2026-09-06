# AgentGateway data-plane Gateways

Three `gatewayClassName: agentgateway` Gateways in namespace `ai`. Cilium `lbipam.cilium.io/ips` pins keep the Service IPs across recreation (the `ai-system` -> `ai` namespace move already ran). HTTPS listeners terminate with `sklab-dev-production-tls` for `*.${SECRET_DOMAIN}`.

| Gateway | Service | Listeners | Auth | Typical clients |
|---------|---------|-----------|------|-----------------|
| [`internal.yaml`](./internal.yaml) | LB `10.50.0.27` | **https only** | Authentik extAuth on https | Browser / SSO |
| [`internal-noauth.yaml`](./internal-noauth.yaml) | **ClusterIP** | http + https | None (keyless) | In-cluster workloads: `http://internal-noauth.ai.svc.cluster.local/v1` |
| [`public.yaml`](./public.yaml) | LB `10.50.0.29` | http + https | API-key Strict on **http**; Authentik on **https** | External API via Envoy `llm-api.${SECRET_DOMAIN}` -> `public:80` |

**An `http` listener on a Gateway whose http listener has no auth policy is an open door to the paid backends, not a redirect.** [`tls-redirect.yaml`](./tls-redirect.yaml) is a catch-all HTTP->HTTPS 301, but `llm-unified` and `llm-models` attach with no `sectionName` - so they bind to every listener - and their `/v1/...` matches are more specific than the redirect's `/`. Gateway API path matching is most-specific-wins regardless of rule order, so the redirect never sees `/v1`. That is deliberate and load-bearing on `public` (it is how Envoy forwards `llm-api.${SECRET_DOMAIN}` to `public:80`), and it is exactly why `internal` **must not** have an http listener: it had one until 2026-09-06 and served OpenAI/Anthropic/Gemini/xAI/DeepSeek/Mistral/Groq/Perplexity/OpenRouter with the cluster's own credentials to anything that could reach `10.50.0.27:80`. Before adding an `http` listener to any of these Gateways, bind an auth policy to that exact listener in the same change.

Keyless access is `internal-noauth`, and it is `ClusterIP` on purpose - the keyless surface should not be reachable from the LAN. Its Service type is set by the Gateway-level `AgentgatewayParameters` in [`internal-noauth.yaml`](./internal-noauth.yaml) (`spec.service.spec` is a strategic-merge `ServiceSpec` overlay); Gateway-level parameters merge with the GatewayClass-level [`../agentgatewayparameters.yaml`](../agentgatewayparameters.yaml) rather than replacing them.

None of the three `ai` Gateways may publish DNS. Their https listeners use the `*.${SECRET_DOMAIN}` wildcard hostname, and `tls-redirect`, `llm-unified` and `llm-models` declare no `spec.hostnames` - so external-dns's `gateway-httproute` source falls back to that wildcard and publishes it against the Gateway LB IP, making the AI gateway the LAN's default answer for every unclaimed name. All three carry `external-dns.alpha.kubernetes.io/controller: none` (the same opt-out `network/https-redirect` uses). Real hostnames are published by the envoy-fronted routes instead.

Policies:

- Authentik: `../policies/authentik-policy.yaml` targets `internal` https and `public` https.
- API keys: `../policies/apikey-policy.yaml` targets `public` http. Keys from 1Password item `ai-gateway-keys`. **This is the only http listener with an auth policy** - see the warning above.
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
