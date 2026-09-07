# Security (Authentik + API keys)

Live auth is not Keycloak JWT against `auth.sklab.dev/realms/ai`. It is Authentik extAuth on HTTPS listeners and API-key Strict on `public` HTTP.

## Authentik (browser / SSO)

[`policies/authentik-policy.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/policies/authentik-policy.yaml) targets:

- Gateway `internal`, section `https`
- Gateway `public`, section `https`

extAuth backend: `ak-outpost-authentik-embedded-outpost.security:9000` path `/outpost.goauthentik.io/auth/envoy`. Forwards Cookie/Authorization; returns authentik identity headers.

## API keys (`llm-api.*`)

[`policies/apikey-policy.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/policies/apikey-policy.yaml) targets Gateway `public` section `http`, mode Strict, header `Authorization` prefix `Bearer `.

The 1.2.1 dataplane did not honor the CRD default location ("no API Key found" despite a valid Bearer header), so `location.header` is set explicitly. Do not remove that block without re-testing.

Keys are not in Git. [`externalsecret-apikeys.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/externalsecret-apikeys.yaml) reads 1Password item `ai-gateway-keys` (`GATEWAY_KEY_SASCHA`, `GATEWAY_KEY_AUTOMATION`) into secret `agentgateway-api-keys`. Add a consumer = add a 1Password field + a template entry.

`internal-noauth` stays keyless for trusted in-cluster workloads and is **ClusterIP** on purpose so the LAN cannot reach it. Call it only as `http://internal-noauth.ai.svc.cluster.local/v1`. Gateway listener/Service inventory: [`app/gateways/README.md`](../../../kubernetes/apps/base/ai/agentgateway/app/gateways/README.md).

## Provider secrets

Per-backend ExternalSecrets from 1Password item `ai-keys`. Template key **must** be `Authorization`. Renaming Anthropic to `x-api-key` fails translation (`secret missing Authorization value`). See the historical testing report, item 6.

TLS cert for Gateway HTTPS listeners: [`externalsecret-tls.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/externalsecret-tls.yaml) -> `sklab-dev-production-tls`. That ExternalSecret must **not** use `refreshPolicy: CreatedOnce` (a permanent pin that kept serving an expired cert while Ready=True); it refreshes on `refreshInterval: 1h`. The bootstrap seed under `network/certificates/import/` is the one place `CreatedOnce` is correct.

## What is not deployed

No live `AgentgatewayPolicy` for prompt-guard, tool-poisoning, or CEL RBAC. Those old `TrafficPolicy` examples were kgateway-era and were never applied as `agentgateway.dev` policies here. Do not paste them into `ai`.
