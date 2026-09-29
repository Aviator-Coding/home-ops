---
name: agentgateway
description: "Read before editing kubernetes/apps/base/ai/agentgateway/** or the agentgateway dashboards, adding a model, provider or backend, changing routes, listeners, API keys or the cost table, or when requests misroute, return 401 or do not fail over. Covers open http listeners, internal-noauth staying ClusterIP, rule order, failover needing the health policy and /v1, and never capturing response bodies."
---

# agentgateway: routes, listeners, backends and failover

Standalone AgentGateway in namespace `ai`, API group `agentgateway.dev/v1alpha1`.
Chart and CRDs are `oci://ghcr.io/agentgateway/charts/agentgateway{,-crds}`
(tag in `app/ocirepository.yaml` and `crds/ocirepository.yaml`). Not
`cr.agentgateway.dev`, and not the retired kgateway-bundled chart. MCP is
ToolHive, not this gateway. LiteLLM is a separate governance layer and is
never this gateway's `/v1`. Skill `litellm-proxy`.

## Tripwires

1. **An `http` listener with no auth policy is an open door to the paid
   backends.** Gateway API path matching is most-specific-wins, so
   `tls-redirect`'s `/` never sees `/v1`. `internal` must stay https-only.
   `public`'s http listener is the one exception, and only because
   `apikey-policy` is bound to that listener. [gateways-auth.md](references/gateways-auth.md)
2. **`internal-noauth` stays `ClusterIP`.** It is the keyless surface
   (`http://internal-noauth.ai.svc.cluster.local/v1`). A LoadBalancer would
   publish it on the LAN. The type comes from that Gateway's own
   `AgentgatewayParameters`, which merge with the class-level parameters
   rather than replace them. Gate: `scripts/ci/agentgateway-exposure-test.py`.
3. **The groq rule stays above the vendor-slug catch-all.** Its family regex
   also matches OpenRouter `moonshotai/...` slugs. Every other unified-route
   regex is mutually exclusive, so order is not load-bearing except here.
   [routing-backends.md](references/routing-backends.md)
4. **Failover needs a health policy and a `/v1` path.**
   `llm-chat-failover-health` must target the failover backend
   (`response.code >= 500 || response.code == 429`, eviction after 2
   failures). Without it, providers are never evicted. A backend
   `pathPrefix` **replaces** the default `/v1` and must include `/v1`, or
   the upstream returns its website.
5. **A new model needs three edits:** a rule in `httproute-unified.yaml`
   when the family is new, a price row in `rules/cost.yaml`, and a catalog
   entry in `httproute-models.yaml`. Re-enabling a dormant backend
   (zai, togetherai, opencodeai) is one new rule. Clients send provider-native
   ids; nothing rewrites them.
6. **The TLS ExternalSecret is never `CreatedOnce`.** That policy syncs
   once and then reports Ready while the certificate expires. The bootstrap
   seed under `network/certificates/import` keeps `CreatedOnce` and is a
   different object. This one refreshes (`refreshInterval: 1h`).
7. **Never reference `response.body` in a tracing CEL expression.**
   Agentgateway buffers a body only when an expression references it.
   Buffering the response breaks token streaming. Request-body capture on
   `/chat/completions` is the allowed one. [observability.md](references/observability.md)
8. **None of the three Gateways publish DNS.** They carry
   `external-dns.alpha.kubernetes.io/controller: none`. The LAN wildcard
   `*.${SECRET_DOMAIN} -> 10.50.0.27` is a hand-made UniFi record (no
   registry TXT twin). external-dns will not delete it. Skill `networking`.
9. **Keep the CRD HelmRelease.** The data-plane chart does not install
   CRDs. Deleting `agentgateway-crds` drops the kinds the routes need.

## Where things live

| What | Path |
|---|---|
| Unified `/v1` rules | `app/httproute-unified.yaml` |
| `GET /v1/models` catalog | `app/httproute-models.yaml` |
| Backends | `app/backends/` |
| Gateways | `app/gateways/` |
| Policies (auth, apikey, routing, tracing) | `app/policies/` |
| Cost table | `app/rules/cost.yaml` |
| Provider keys (`ai-keys`, secret key `Authorization`) and consumer keys (`ai-gateway-keys`) | `app/externalsecret-apikeys.yaml` |
| Serving cert | `app/externalsecret-tls.yaml` |
| Dashboards | `kubernetes/apps/base/ai/agentgateway-dashboards/` |
| Exposure gate | `scripts/ci/agentgateway-exposure-test.py` |

## Procedures

- Listeners, auth binding, ClusterIP, DNS opt-out: [gateways-auth.md](references/gateways-auth.md).
- Adding a backend, the groq exception, failover: [routing-backends.md](references/routing-backends.md).
- Dashboards, traces, cost: [observability.md](references/observability.md).

## Verify

- `python3 scripts/ci/agentgateway-exposure-test.py`
- A 401 on `public` http is the API key, not Authentik. A 401 on `internal`
  https is Authentik. `internal-noauth` has neither, and must not be
  reachable off-cluster.
- Failover: a forced 5xx on the local backend must move the next request
  to group 2. No health policy means it never will, while the route still
  looks Ready.
