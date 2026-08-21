# AgentGateway

> Standalone AgentGateway `v1.4.1` in namespace `ai`. Official product docs: <https://agentgateway.dev/>. This tree describes **this cluster's GitOps install**, not the retired kgateway-bundled chart.

## June 2026 split

AgentGateway and kgateway started as one product. They split: kgateway remains an Envoy Kubernetes gateway; AgentGateway is a standalone Rust AI gateway with its own charts (`cr.agentgateway.dev`) and 1.x versioning.

This cluster moved on 2026-06-06 in `e4321cb2` (#938) from `oci://ghcr.io/kgateway-dev/charts/agentgateway` tag `v2.3.0-main` (a stale kgateway-dev snapshot) to `oci://cr.agentgateway.dev/charts/agentgateway` starting at `v1.2.1`. That commit dropped kgateway-era values (`agentgateway.enabled`, `KGW_*` env). The next day `0e0a5fb9` (#943) merged namespace `ai-system` into `ai`. The live tag is `v1.4.1`.

kgateway is **not** deployed here. Non-AI ingress is Envoy Gateway in `network/`. See [kgateway stub](../kgateway/README.md).

A March 2026 lab notebook still exists as a [historical testing report](../agentgateway-testing-report.md). Do not copy its commands.

## What is live

| Item | Live value | Source of truth |
|------|------------|-----------------|
| Namespace | `ai` | `kubernetes/apps/ai/agentgateway/ks.yaml` |
| Chart | `oci://cr.agentgateway.dev/charts/agentgateway` `v1.4.1` | `app/ocirepository.yaml` |
| CRDs | `oci://cr.agentgateway.dev/charts/agentgateway-crds` `v1.4.1` | `crds/ocirepository.yaml` |
| API | `agentgateway.dev/v1alpha1` | kinds below |
| Kinds | `AgentgatewayBackend`, `AgentgatewayPolicy`, `AgentgatewayParameters` | `app/backends/`, `app/policies/`, `app/agentgatewayparameters.yaml` |
| Gateway class | `agentgateway` | `app/gateways/` |
| LLM entry | unified OpenAI-style `/v1` | `app/httproute-unified.yaml` |
| MCP | ToolHive, not AgentGateway MCP Backends | `kubernetes/apps/ai/toolhive/` |
| LiteLLM / kagent / kmcp / kgateway | **not deployed** | `kubernetes/apps/ai/kustomization.yaml`. Tombstones: [kagent](../kagent/README.md), [kmcp](../kmcp/README.md). Live MCP GVK is `toolhive.stacklok.dev/v1alpha1` `MCPServer`. |

Manifests live under `kubernetes/apps/ai/agentgateway/`. Prefer those YAML files (especially the header comments on `httproute-unified.yaml` and `gateways/*.yaml`) over copying config into prose.

### Data-plane Gateways

| Gateway | LB IP | Auth |
|---------|-------|------|
| `internal` | `10.50.0.27` | Authentik on https |
| `internal-noauth` | `10.50.0.28` | keyless (in-cluster) |
| `public` | `10.50.0.29` | API-key Strict on http; Authentik on https |

Details: [`app/gateways/README.md`](../../../kubernetes/apps/ai/agentgateway/app/gateways/README.md).

### How clients reach it

| Audience | URL | Auth |
|----------|-----|------|
| In-cluster LLM | `http://internal-noauth.ai.svc.cluster.local/v1` | none |
| External / scripts | `https://llm-api.${SECRET_DOMAIN}/v1` (Envoy -> `public:80`) | Bearer key from 1Password item `ai-gateway-keys` |
| Admin UI | `https://agentgateway.${SECRET_DOMAIN}/ui/` and `https://llm.${SECRET_DOMAIN}/ui/` | Envoy + Authentik on the UI hostnames |

Send OpenAI Chat Completions to `/v1/chat/completions` with a provider-native `model` id. The `model-routing` policy copies `model` into `x-model`; the unified HTTPRoute picks the backend. Clients never pick a `/openai` or `/groq` path prefix - those routes were retired.

### Secrets

External Secrets Operator + ClusterSecretStore `onepassword`. Provider keys come from 1Password item `ai-keys`; the Kubernetes secret **key must be `Authorization`**. Consumer API keys come from item `ai-gateway-keys`. Never put secrets in Git.

## Documentation index

### Cluster install

| Document | Description |
|----------|-------------|
| [01-quickstart.md](./01-quickstart.md) | Call the live `/v1` endpoint |
| [02-installation.md](./02-installation.md) | Flux chart, CRDs, Helm values |
| [03-gateway-setup.md](./03-gateway-setup.md) | Three Gateways and listeners |
| [11-cluster-deployment.md](./11-cluster-deployment.md) | Live tree map (pointers, not a copy of manifests) |

### Traffic

| Document | Description |
|----------|-------------|
| [04-llm-providers.md](./04-llm-providers.md) | Backends, model routing, dormant providers |
| [05-mcp-connectivity.md](./05-mcp-connectivity.md) | MCP is ToolHive, not this gateway |
| [06-agent-connectivity.md](./06-agent-connectivity.md) | In-cluster clients; kagent is not deployed |

### Operations

| Document | Description |
|----------|-------------|
| [07-security.md](./07-security.md) | Authentik + API keys |
| [08-observability.md](./08-observability.md) | Vendored Grafana dashboards + Tempo traces |
| [09-advanced-features.md](./09-advanced-features.md) | Failover groups, embeddings, dormant backends |
| [12-troubleshooting.md](./12-troubleshooting.md) | Commands against namespace `ai` |
| [15-optimization.md](./15-optimization.md) | Cost metering (`rules/cost.yaml`) |

### Reference

| Document | Description |
|----------|-------------|
| [10-api-reference.md](./10-api-reference.md) | Live CRD kinds used here |
| [GLOSSARY.md](./GLOSSARY.md) | Terms |
| [MIGRATION.md](./MIGRATION.md) | kgateway-dev 2.x -> standalone 1.x |

### Not wired through this gateway

| Document | Why it exists |
|----------|---------------|
| [13-function-calling.md](./13-function-calling.md) | No AgentGateway MCP Backends |
| [14-session-management.md](./14-session-management.md) | No session config in live `AgentgatewayParameters` |

## Adding a model

The catalog comment in `httproute-models.yaml` is the checklist: a routing rule in `httproute-unified.yaml` (if the family is new), a price row in `rules/cost.yaml`, and a catalog entry. Re-enabling a dormant backend (zai / togetherai / opencodeai) is one new unified-route rule.

*Last updated: 2026-08-21 against chart `v1.4.1`.*
