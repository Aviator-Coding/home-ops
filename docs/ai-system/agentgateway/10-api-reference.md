# API reference (kinds this cluster uses)

Upstream CRD docs: <https://agentgateway.dev/>. This page lists **what GitOps actually applies**. The old catalog of `gateway.kgateway.dev` kinds (`Backend`, `TrafficPolicy`, `GatewayParameters`, `HTTPListenerPolicy`, `BackendConfigPolicy`, `DirectResponse`) is not installed.

## `agentgateway.dev/v1alpha1`

| Kind | Live names | Where |
|------|------------|-------|
| `AgentgatewayBackend` | openai, anthropic, gemini, groq, mistral, deepseek, xai, togetherai, openrouter, perplexity, opencodeai, opencodego, vllm, vllm-embed, llm-chat-failover, zai | `app/backends/` |
| `AgentgatewayPolicy` | authentik-policy, apikey-policy, model-routing, tracing-policy, models-catalog, llm-chat-failover-health | `app/policies/` + backends/vllm.yaml + httproute-models.yaml |
| `AgentgatewayParameters` | agentgateway-params | `app/agentgatewayparameters.yaml` |

HTTPRoute `backendRefs` for LLM upstreams use:

```yaml
backendRefs:
  - name: openai
    group: agentgateway.dev
    kind: AgentgatewayBackend
```

## Gateway API `v1`

| Kind | Live names |
|------|------------|
| `Gateway` | internal, internal-noauth, public |
| `HTTPRoute` | llm-unified, llm-models, tls-redirect, agentgateway, agentgateway-internal, agentgateway-api |
| `GatewayClass` | `agentgateway` (chart) |

## Other CRs in the app

| Kind | Name | File |
|------|------|------|
| `HelmRelease` | agentgateway | `app/helmrelease.yaml` |
| `OCIRepository` | agentgateway | `app/ocirepository.yaml` |
| `ExternalSecret` | per-provider + `agentgateway-api-keys` + TLS | `app/backends/*`, `externalsecret-*.yaml` |
| `PodMonitor` | agentgateway-data-plane | `app/podmonitor.yaml` |
| `PrometheusRule` | agentgateway-llm-cost | `app/rules/cost.yaml` |
| `Service` | agentgateway-admin-ui | `app/service-admin-ui.yaml` |

CRDs themselves come from HelmRelease `agentgateway-crds` (`crds/`).

```bash
kubectl api-resources | grep agentgateway
# AgentgatewayBackend   backends.agentgateway.dev
# AgentgatewayPolicy    agentgatewaypolicies.agentgateway.dev
# AgentgatewayParameters
```
