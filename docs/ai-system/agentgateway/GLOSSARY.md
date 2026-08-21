# AgentGateway glossary (this cluster)

Terms as they are used in `kubernetes/apps/ai/agentgateway/`. Upstream product glossary: <https://agentgateway.dev/>.

## AgentGateway

Standalone Rust AI gateway. Chart `oci://cr.agentgateway.dev/charts/agentgateway` tag `v1.4.1`, namespace `ai`. Not a kgateway feature flag.

## AgentgatewayBackend

`agentgateway.dev/v1alpha1` CR that names an upstream LLM provider (or a failover group). Live files: `app/backends/*.yaml`. There is no `kind: Backend` and no `gateway.kgateway.dev` API in this install.

## AgentgatewayPolicy

`agentgateway.dev/v1alpha1` CR for traffic/backend/frontend policy. Live policies: Authentik extAuth, API-key Strict, model-routing (`model` -> `x-model`), tracing to Tempo, models-catalog directResponse, llm-chat-failover health.

## AgentgatewayParameters

`agentgateway.dev/v1alpha1` CR referenced from Helm `gatewayClassParametersRefs`. Live object only sets `ADMIN_ADDR=0.0.0.0:15000`. Not the old `GatewayParameters` / `spec.kube.agentgateway` shape.

## Gateway / HTTPRoute

Standard Gateway API objects. `gatewayClassName: agentgateway` selects this dataplane. Routing objects are `Gateway` and `HTTPRoute`; LLM backend refs use `group: agentgateway.dev`, `kind: AgentgatewayBackend`.

## Unified `/v1`

Single OpenAI-style entry point. Clients POST `/v1/chat/completions` (or `/v1/embeddings`, `/v1/messages`) with a `model` field. `AgentgatewayPolicy/model-routing` copies that field to header `x-model` (PreRouting); `httproute-unified.yaml` matches it. Per-provider path prefixes (`/openai`, `/groq`, ...) are retired.

## Envoy Gateway

General HTTP ingress in `network/` (`envoy-internal`, `envoy-external`). Fronts the admin UI and `llm-api.${SECRET_DOMAIN}`. Independent of AgentGateway and of kgateway.

## kgateway

CNCF Envoy-based Kubernetes gateway. Split from AgentGateway in 2026. **Not installed** in this cluster. See [../kgateway/README.md](../kgateway/README.md).

## ToolHive

MCP server platform in `kubernetes/apps/ai/toolhive/`. This cluster's MCP path. AgentGateway MCP/A2A Backends are not used.

## LiteLLM

Former unified LLM proxy. Removed. AgentGateway's own `/v1` router replaced it.

## `internal` / `internal-noauth` / `public`

The three data-plane Gateways (IPs `10.50.0.27` / `.28` / `.29`). See [gateways/README.md](../../../kubernetes/apps/ai/agentgateway/app/gateways/README.md).

## `ai-keys` / `ai-gateway-keys`

1Password items. `ai-keys` holds provider credentials (templated to secret key `Authorization`). `ai-gateway-keys` holds consumer Bearer keys for `llm-api.*`.

## Dormant backend

An `AgentgatewayBackend` (and its secret) that exists but has no rule in `httproute-unified.yaml`. Today: `zai`, `togetherai`, `opencodeai`.
