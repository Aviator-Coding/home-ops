# Observability

Do not import kgateway-org Grafana dashboards 24590/24965 or scrape `kgateway_controller_*` on port 9092. Grafana values say those gnet entries are gone (old unified-proxy LiteLLM dashboard retired with #941; kgateway-org dashboard superseded). The 2026-08-26 LiteLLM governance layer scrapes via its own ServiceMonitor/alerts (`../litellm/README.md`), not those gnet IDs.

## Metrics

- Chart ServiceMonitors: enabled in [`helmrelease.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/helmrelease.yaml) (`monitoring.serviceMonitor.enabled: true`).
- Extra [`podmonitor.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/podmonitor.yaml) scrapes data-plane pods on port **15020** (`metrics`). Needed because the chart ServiceMonitor selects a `metrics` port on per-Gateway Services, which this deployer does not create (Services only expose listener ports).
- Consequence: every agentgateway pod is scraped by **two** jobs (e.g. `internal-noauth` +
  `ai/agentgateway-proxy`), so any single-metric series like `agentgateway_build_info` has a
  duplicate per pod. A PromQL `group_left` join against it directly errors with "many-to-many
  matching not allowed" - always wrap it in `group by (pod, namespace, <label you need>) (...)`
  first, not `sum by (...)`: `agentgateway_build_info` is a standard `_info` gauge fixed at
  value 1, so `sum by` on the duplicated series collapses to value 2 and silently doubles
  anything joined against it with `*`, while `group by` always emits 1 regardless of input
  count. Fixed this way in `agentgateway.json`'s Memory/CPU panels 2026-08-21.
- `agentgateway_build_info` (and the other agentgateway metrics) carry **no**
  `gateway_networking_k8s_io_gateway_name` label on this cluster, so the `$gateway_name`/`$gateway`
  dashboard variables have no real values to offer (checked live 2026-08-21) - harmless today
  because they default to matching everything, but the dropdowns themselves are non-functional.
  Not fixed here; would need picking a different, actually-populated label.

Token/cost series used by the LLM spend rules: `agentgateway_gen_ai_client_token_usage` (histogram, labels `gen_ai_request_model` + `gen_ai_token_type`). See `15-optimization.md` and `app/rules/cost.yaml`.

## Grafana dashboards

Chart dashboard JSON is **disabled** (`monitoring.grafanaDashboard.enabled: false`). Vendored copies:

- [`kubernetes/apps/base/ai/agentgateway-dashboards/`](../../../kubernetes/apps/base/ai/agentgateway-dashboards/)
- `agentgateway.json` (from the official chart; re-vendor on chart bumps)
- `llm-cost.json` - linked from/to `agentgateway.json` via dashboard `links` rather than merged in,
  so the vendored chart JSON survives re-vendoring without losing the custom cost panels
- sidecar annotations `grafana_folder: AI/ML`, `grafana_dashboard: "true"` - **known bug**: both
  dashboards actually land in a folder titled `ML`, not `AI/ML` (verified live 2026-08-21). The
  Grafana chart's sidecar appears to treat the `/` in the annotation as a path separator rather
  than a literal folder-name character. A separate `AI/ML` folder is also created by the Grafana
  HelmRelease's `ai-ml` file-based dashboard provider (used since 2026-08-29 for
  `toolhive-mcp-gateway` and any other `dashboards.ai-ml` entries). Not fixed here - couldn't
  verify a rename live without applying to the cluster; a real fix needs a cluster-side check
  after deploy, not just a JSON diff.

## Tracing

[`policies/tracing-policy.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/policies/tracing-policy.yaml) - `AgentgatewayPolicy/tracing-policy` on all three Gateways:

- OTLP gRPC to `tempo.monitoring:4317`
- 100% sampling (`randomSampling: "1.0"`)
- `gen_ai.prompt` from the request body on `/chat/completions` only
- **Does not** capture `response.body` (that would buffer and break token streaming)

This is not `GatewayParameters.spec.rawConfig.config.tracing`.

## Admin / debug

Dataplane admin UI: port **15000** at `/ui/` (Envoy HTTPRoutes). `AgentgatewayParameters` sets `ADMIN_ADDR=0.0.0.0:15000`. There is no documented control-plane debug port 9095 in this install.

**The admin UI is not an audit surface in this install.** In Kubernetes/XDS mode it has two
open upstream display bugs that make it actively misleading, not just incomplete:
[agentgateway/agentgateway#1370](https://github.com/agentgateway/agentgateway/issues/1370)
(Traffic Routes page shows `/` for every HTTPRoute path) and
[agentgateway/agentgateway#2658](https://github.com/agentgateway/agentgateway/issues/2658)
(route inventory shows "Policies: 0" even when an `AgentgatewayPolicy` is attached and
enforcing). Both were still open as of 2026-08-21. Treat `kubectl get httproute,agentgatewaypolicy
-o yaml` plus the Grafana `agentgateway` dashboard and Tempo traces as ground truth for
routes/policies instead of the UI. Once both issues close upstream, re-check whether this
caveat still applies.

MCP: this cluster's agentgateway install proxies no MCP traffic (`agentgateway_mcp_requests_total`
has zero series on live Prometheus, checked 2026-08-21) - MCP is handled entirely by the separate
ToolHive `VirtualMCPServer` (`kubernetes/apps/base/ai/toolhive/`), which Hermes talks to directly. The
Grafana `agentgateway` dashboard's MCP row was dropped for this reason; re-add it if agentgateway
ever gains real MCP routes.

## Gatus

HTTPRoute annotations probe:

- `https://agentgateway.${SECRET_DOMAIN}/ui/` expect 200
- `https://llm.${SECRET_DOMAIN}/ui/` expect 200
- `https://llm-api.${SECRET_DOMAIN}/v1/models` expect **401** (unauthenticated)
