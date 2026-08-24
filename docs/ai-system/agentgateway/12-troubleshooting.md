# Troubleshooting (namespace `ai`)

Wrong-namespace / wrong-kind commands (`-n ai-system`, `kubectl get backend`, `-l app.kubernetes.io/name=kgateway-controller`, `flux reconcile source oci kgateway`) will not show this install.

## Inventory

```bash
kubectl -n ai get gateway,httproute
kubectl -n ai get agentgatewaybackend,agentgatewaypolicy,agentgatewayparameters
kubectl -n ai get helmrelease,ocirepository
kubectl -n ai get externalsecret
kubectl -n ai get podmonitor,prometheusrule
flux get ks -n ai
flux get hr -n ai
```

Gateways are named `internal`, `internal-noauth`, `public` - not `agentgateway`. OCIRepositories are `agentgateway` and `agentgateway-crds`.

## Controller vs dataplane

```bash
kubectl -n ai get pods -l app.kubernetes.io/name=agentgateway
kubectl -n ai logs -l app.kubernetes.io/name=agentgateway --tail=100
kubectl -n ai get pods -l gateway.networking.k8s.io/gateway-class-name=agentgateway -o wide
```

Dataplane Services follow Gateway names (`internal`, `internal-noauth`, `public`). Admin UI is Service `agentgateway-admin-ui:15000`. Metrics are scraped from pod port **15020** (`PodMonitor/agentgateway-data-plane`).

## Routing misses

1. Read [`httproute-unified.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/httproute-unified.yaml) header. Confirm the `model` id matches a regex (or falls through to OpenAI).
2. Confirm `AgentgatewayPolicy/model-routing` is Accepted. `/v1/models` and `/v1/messages` skip the body transform on purpose.
3. Vendor slugs `moonshotai/...` must hit Groq, so the Groq rule stays **above** the OpenRouter catch-all.
4. Dormant backends (`zai`, `togetherai`, `opencodeai`) will never receive traffic until a unified-route rule exists.
5. Prefix paths `/openai`, `/groq`, `/openrouter` are gone - clients must use `/v1`.

## Auth failures

- In-cluster: use `internal-noauth`, not `public`.
- `llm-api.*`: Bearer token from `ai-gateway-keys`. Gatus expects **401** without a key on `GET /v1/models`.
- If Strict API key ignores `Authorization: Bearer`, confirm `apikey-policy.yaml` still sets `location.header` explicitly.
- Provider 401s: ExternalSecret synced? Secret key is `Authorization`?

## TLS to providers

Custom-host OpenAI-compat backends need `policies.tls: {}`. Symptom without it: "The plain HTTP request was sent to HTTPS port". Native openai/anthropic/gemini handle TLS themselves.

## Local model 503 / no failover

- `vllm-embed` is scaled to 0; default embedding ids 503.
- Chat failover requires `AgentgatewayPolicy/llm-chat-failover-health`. Without it group 2 is never tried.
- OpenCode Go failover `pathPrefix` must be `/zen/go/v1`, not `/zen/go`.

## Flux

```bash
flux reconcile ks agentgateway-crds -n ai --with-source
flux reconcile ks agentgateway -n ai --with-source
kubectl -n ai describe helmrelease agentgateway
```

## Health URLs that do not exist

There is no `http://ai.sklab.dev/health` listener in the HTTPRoutes. Use `/v1/models` or the admin `/ui/`.
