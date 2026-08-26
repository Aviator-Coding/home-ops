# Quickstart (live cluster)

This is not a Kind + Helm walkthrough and not a kgateway install. The gateway is already deployed by Flux in namespace `ai`.

## 1. Confirm it is running

```bash
kubectl -n ai get gateway internal internal-noauth public
kubectl -n ai get httproute llm-unified
kubectl -n ai get agentgatewaybackend
kubectl -n ai get helmrelease agentgateway
```

Expect three Gateways (`10.50.0.27/28/29`), HTTPRoute `llm-unified`, and `AgentgatewayBackend` objects (not `kind: Backend`).

## 2. Call unified `/v1` from inside the cluster

In-cluster clients use the keyless Gateway:

```bash
curl http://internal-noauth.ai.svc.cluster.local/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}'
```

The `model` string selects the provider. Examples of ids the unified router already matches: `gpt-*`, `claude-*`, `gemini-*`, `grok-*`, `qwen3.6-35b-a3b` (local + failover), `kimi-*` / `glm-*` (OpenCode Go), vendor slugs such as `x-ai/grok-4.3` (OpenRouter). Exact rules: [`httproute-unified.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/httproute-unified.yaml). Catalog of advertised ids: [`httproute-models.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/httproute-models.yaml).

```bash
curl http://internal-noauth.ai.svc.cluster.local/v1/models
```

## 3. Call it from outside (API key)

Envoy fronts `https://llm-api.${SECRET_DOMAIN}/v1` and forwards to `public:80`, where `apikey-policy` requires a Bearer token from 1Password item `ai-gateway-keys`.

```bash
curl "https://llm-api.${SECRET_DOMAIN}/v1/chat/completions" \
  -H "Authorization: Bearer ${GATEWAY_KEY}" \
  -H 'content-type: application/json' \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"ping"}]}'
```

## 4. Admin UI

Open `https://agentgateway.${SECRET_DOMAIN}/ui/` or `https://llm.${SECRET_DOMAIN}/ui/`. Bare `/` is 302'd to `/ui/` on purpose (v1.3.0+ 308 to `http://` would fail behind TLS).

## Do not

- `helm install kgateway ... --set agentgateway.enabled=true`
- `curl .../openai` or `.../groq` (prefix routes are gone)
- `kubectl -n ai-system` or `-n kgateway-system`
- Point gateway clients at LiteLLM - it is a separate in-cluster governance layer with no gateway wiring (`../litellm/README.md`), not this gateway's `/v1`
