# Observability

Do not import kgateway-org Grafana dashboards 24590/24965 or scrape `kgateway_controller_*` on port 9092. Grafana values say those gnet entries are gone (LiteLLM removed; kgateway-org dashboard superseded).

## Metrics

- Chart ServiceMonitors: enabled in [`helmrelease.yaml`](../../../kubernetes/apps/ai/agentgateway/app/helmrelease.yaml) (`monitoring.serviceMonitor.enabled: true`).
- Extra [`podmonitor.yaml`](../../../kubernetes/apps/ai/agentgateway/app/podmonitor.yaml) scrapes data-plane pods on port **15020** (`metrics`). Needed because the chart ServiceMonitor selects a `metrics` port on per-Gateway Services, which this deployer does not create (Services only expose listener ports).

Token/cost series used by the LLM spend rules: `agentgateway_gen_ai_client_token_usage` (histogram, labels `gen_ai_request_model` + `gen_ai_token_type`). See `15-optimization.md` and `app/rules/cost.yaml`.

## Grafana dashboards

Chart dashboard JSON is **disabled** (`monitoring.grafanaDashboard.enabled: false`). Vendored copies:

- [`kubernetes/apps/ai/agentgateway-dashboards/`](../../../kubernetes/apps/ai/agentgateway-dashboards/)
- `agentgateway.json` (from the official chart; re-vendor on chart bumps)
- `llm-cost.json`
- sidecar annotations `grafana_folder: AI/ML`, `grafana_dashboard: "true"`

## Tracing

[`policies/tracing-policy.yaml`](../../../kubernetes/apps/ai/agentgateway/app/policies/tracing-policy.yaml) - `AgentgatewayPolicy/tracing-policy` on all three Gateways:

- OTLP gRPC to `tempo.monitoring:4317`
- 100% sampling (`randomSampling: "1.0"`)
- `gen_ai.prompt` from the request body on `/chat/completions` only
- **Does not** capture `response.body` (that would buffer and break token streaming)

This is not `GatewayParameters.spec.rawConfig.config.tracing`.

## Admin / debug

Dataplane admin UI: port **15000** at `/ui/` (Envoy HTTPRoutes). `AgentgatewayParameters` sets `ADMIN_ADDR=0.0.0.0:15000`. There is no documented control-plane debug port 9095 in this install.

## Gatus

HTTPRoute annotations probe:

- `https://agentgateway.${SECRET_DOMAIN}/ui/` expect 200
- `https://llm.${SECRET_DOMAIN}/ui/` expect 200
- `https://llm-api.${SECRET_DOMAIN}/v1/models` expect **401** (unauthenticated)
