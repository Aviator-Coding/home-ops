# Gateways, listeners and auth

Three `gatewayClassName: agentgateway` Gateways. `internal` and `public`
pin LoadBalancer IPs with Cilium `lbipam.cilium.io/ips` so a recreate keeps
them. HTTPS listeners terminate with `sklab-dev-production-tls`
(`*.${SECRET_DOMAIN}`).

| Gateway | Service | Listeners | Auth | Clients |
|---|---|---|---|---|
| `internal` | LB `10.50.0.27` | **https only** | Authentik on https | browsers |
| `internal-noauth` | **ClusterIP** | http + https | none | in-cluster, `http://internal-noauth.ai.svc.cluster.local/v1` |
| `public` | LB `10.50.0.29` | http + https | API key Strict on **http**; Authentik on **https** | Envoy `llm-api.${SECRET_DOMAIN}` forwards to `public:80` |

## The open http listener

`tls-redirect` is a catch-all HTTP to HTTPS 301. `llm-unified` and
`llm-models` attach with no `sectionName`, so they bind every listener,
and their `/v1` matches are more specific than the redirect's `/`. The
redirect never sees `/v1`.

That is load-bearing on `public`: it is how Envoy reaches the API. It is
why `internal` must not grow an http listener. It had one, and anything
that could reach `10.50.0.27:80` called the paid backends with the
cluster's own credentials. Before adding an http listener, bind an auth
policy to that exact listener in the same change.

`apikey-policy` is the only http auth policy, and it targets `public` http.
Keys come from 1Password item `ai-gateway-keys`. Authentik
(`authentik-policy`) targets `internal` https and `public` https.

`model-routing-policy` copies JSON `model` into `x-model` before route
selection, on all three Gateways. Clients never pick a `/openai` or
`/groq` path. Those per-provider routes are gone.

## ClusterIP

`internal-noauth`'s Service type is a Gateway-level
`AgentgatewayParameters` overlay (`spec.service.spec` is a strategic-merge
`ServiceSpec`). It merges with the GatewayClass-level
`agentgatewayparameters.yaml`. Replacing the class-level object, or
dropping the Gateway-level one, can turn this Service into a
LoadBalancer. `scripts/ci/agentgateway-exposure-test.py` asserts ClusterIP.

## DNS

No `ai` Gateway may publish DNS. https listeners use the wildcard
hostname, and `tls-redirect`, `llm-unified`, and `llm-models` declare no
`spec.hostnames`, so external-dns's `gateway-httproute` source would
publish the wildcard against the Gateway LB IP. All three Gateways set
`external-dns.alpha.kubernetes.io/controller: none`.

Real hostnames are the envoy-fronted routes (`llm-api`, `agentgateway`,
`llm`). For `gateway-httproute`, the parent Gateway's target annotation
wins over the HTTPRoute's. Skill `networking`.

The live LAN wildcard `*.${SECRET_DOMAIN} -> 10.50.0.27` has no
`k8s.main.<type>-<name>` registry TXT twin. It is hand-made in UniFi.
external-dns `--policy=sync` will not update or delete it. Until it is
removed in the controller, unclaimed names still resolve to `10.50.0.27`,
which is now https-only and Authentik-gated.

Check ownership:
`kubectl -n network port-forward deploy/unifi-dns 18888:8888`, then
`curl -H 'Accept: application/external.dns.webhook+json;version=1' localhost:18888/records`.

## TLS secret

`externalsecret-tls.yaml` copies `network/sklab-dev-production-tls` from
1Password on `refreshInterval: 1h`. `creationPolicy: Orphan` so a missed
sync does not delete the serving secret. Do not set `refreshPolicy:
CreatedOnce` here. That value is correct only on the bootstrap seed, and
the exposure test checks both objects stay that way.

Admin UI is port 15000 `/ui/`, on `agentgateway.${SECRET_DOMAIN}` and
`llm.${SECRET_DOMAIN}`, both behind Envoy + Authentik. It is not an LLM
listener.
