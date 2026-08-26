# Cluster deployment (live tree)

This file used to paste a fictional `kubernetes/apps/ai-system/agentgateway/app/` (single `gateway.yaml`, `backends.yaml`, `httproutes.yaml`, NetworkPolicy, PDB, ServiceMonitor). **Do not apply those snippets.** The live tree is the source of truth.

## Layout

```
kubernetes/apps/main/ai/agentgateway.yaml    # Flux Kustomizations: agentgateway + agentgateway-crds
kubernetes/apps/base/ai/agentgateway/
  crds/   ocirepository.yaml  helmrelease.yaml  kustomization.yaml
  app/
    helmrelease.yaml
    ocirepository.yaml
    agentgatewayparameters.yaml
    kustomization.yaml
    backends/          # one file per provider (+ vllm failover)
    gateways/          # internal, internal-noauth, public, tls-redirect
                       # README.md explains listeners and IPs
    policies/          # authentik, apikey, model-routing, tracing
    httproute-unified.yaml
    httproute-models.yaml
    httproute.yaml                 # admin UI agentgateway.*
    httproute-internal.yaml        # admin UI llm.*
    httproute-api-external.yaml    # llm-api.* -> public:80
    externalsecret-apikeys.yaml
    externalsecret-tls.yaml
    podmonitor.yaml
    rules/cost.yaml
    service-admin-ui.yaml
kubernetes/apps/base/ai/agentgateway-dashboards/
  app/agentgateway.json
  app/llm-cost.json
```

Sibling apps in `ai` (from [`kustomization.yaml`](../../../kubernetes/apps/main/ai/kustomization.yaml)): agentgateway-dashboards, agentmemory, comfyui, hermes, litellm, searxng, toolhive, vllm. `litellm` is a governance-only layer with no gateway wiring (`../litellm/README.md`) - not a sibling in the routing sense. Not listed: kagent, kmcp, kgateway. Retired 2026-08-22: kokoro, miso-gallery, open-notebook, open-webui, perplexica, qdrant ([note](../retired-2026-08-22.md)).

## Flux identity

- Kustomization names: `agentgateway`, `agentgateway-crds`
- `targetNamespace: ai`
- `path: ./kubernetes/apps/base/ai/agentgateway/{app,crds}`
- `dependsOn`: `agentgateway-crds`, `onepassword-store` (security), `certificates-import` (network)
- `postBuild.substituteFrom: cluster-secrets`

## 1Password

| Item | Used for |
|------|----------|
| `ai-keys` | Provider API keys (secret key `Authorization`) |
| `ai-gateway-keys` | Consumer Bearer keys for `llm-api.*` |
| `sklab-dev-tls` | Gateway HTTPS cert |

There is no 1Password item named `agentgateway` for this app.

## Hostnames

| Hostname | Fronted by | Backend |
|----------|------------|---------|
| `agentgateway.${SECRET_DOMAIN}` | Envoy internal | admin UI :15000 `/ui/` |
| `llm.${SECRET_DOMAIN}` | Envoy internal | admin UI :15000 `/ui/` |
| `llm-api.${SECRET_DOMAIN}` | Envoy internal **and** external | Gateway `public` :80 |

In-cluster LLM: `http://internal-noauth.ai.svc.cluster.local/v1`.
