# Agentgateway observability

## Traces

`app/policies/tracing-policy.yaml` exports OTLP gRPC to Tempo `:4317` at
`randomSampling: "1.0"`, on all three Gateways.

`gen_ai.prompt` is `string(request.body)` only when the path ends in
`/chat/completions`. That skips embedding inputs and `GET /v1/models`.
Agentgateway buffers a body only when a CEL expression references it.
Request bodies on chat completions are small and fully received.

Do not add `response.body`. Referencing it buffers the response and breaks
token streaming. Response cost and token counts stay on
`agentgateway_gen_ai_client_token_usage` and on span timing.

## Cost

`app/rules/cost.yaml` is the price table. It keys on the provider-native
model id the client sent (the unified route does not rewrite ids). A new
model with no row is unpriced, not unrouted. Hermes has no direct-provider
path, so its LLM spend is in this table. Skill `hermes-agent`.

## Dashboards

`kubernetes/apps/base/ai/agentgateway-dashboards/` vendors the gateway
dashboard and the LLM cost dashboard as Grafana sidecar JSON. A folder
annotation that contains `/` is treated as a path separator by the Grafana
sidecar; do not "fix" a folder name by inserting one.

The PodMonitor scrapes the data-plane metrics port. `port:` on a
PodMonitor is a container-port **name**, not a number. Skill
`observability`.

## What is not here

Admin UI reachability is an HTTPRoute plus Authentik, not a metric.
`internal-noauth` has no auth failure counter that would page: it is
keyless by design, and the exposure gate is the ClusterIP assertion, not
an alert.
