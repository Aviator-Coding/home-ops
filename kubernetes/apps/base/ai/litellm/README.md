# LiteLLM (governance layer)

[BerriAI/litellm](https://github.com/BerriAI/litellm) proxy, delivered as
`litellm.home-operations.com/v1alpha1` CRs reconciled by the
[home-operations litellm-operator](https://github.com/home-operations/litellm-operator)
(`../litellm-operator/`). The operator owns the proxy Deployment, Service and
rendered `config.yaml`; nothing here renders a Deployment. Agent knowledge
(tripwires, procedures): skill `litellm-proxy`. Human runbooks:
[request logs](../../../../../.agents/skills/litellm-proxy/references/spend-logs.md) and
[Claude Code client setup](../../../../../.agents/skills/litellm-proxy/references/claude-code-subscription.md).

**Scope (captain decision B4):** internal route only - `litellm-internal` on
`envoy-internal` at `litellm.${SECRET_DOMAIN}`, plus
`http://litellm.ai.svc.cluster.local:4000` in-cluster. It never fronts the
public listener: no `envoy-external` parentRef and no `AgentgatewayBackend`,
ever.

| File | Declares |
| --- | --- |
| `app/litellmproxy.yaml` | `LiteLLMProxy`: image, env (SSO), `generalSettings`, pass-through lockdown, AI Hub list, `routerSettings`, vector-store registry |
| `app/models/` | one `LiteLLMModel` per model, including the Claude Code subscription pass-through models (`claude-sonnet-5`, `claude-opus-5`, `claude-haiku-4-5-20251001`, `claude-fable-5-1`) |
| `app/virtualkeys/` | one `LiteLLMVirtualKey` + `PushSecret` per consumer, e.g. `claude-code-subscription` |
| `app/httproute-internal.yaml` | `litellm-internal` route + `/anthropic` 404 filter |
| `app/externalsecret.yaml` | `litellm-secret` |
| `app/pushsecret-sso.yaml`, `app/pushsecret-pgvector.yaml` | one-time credentials into 1Password |
| `app/dbinit.yaml` | `postgres-init` Job for the `litellm` role and database |
| `app/servicemonitor.yaml`, `app/prometheusrule.yaml` | scrape and alerts |

## Prerequisites (before first sync)

1Password item **`litellm`** (vault of the `onepassword` ClusterSecretStore),
the only item created by hand:

| Field | How to generate |
| --- | --- |
| `LITELLM_MASTER_KEY` | `echo sk-$(openssl rand -hex 32)` |
| `LITELLM_SALT_KEY` | `echo sk-$(openssl rand -hex 32)` |
| `POSTGRES_DB_NAME` | `litellm` |
| `POSTGRES_DB_USER_NAME` | `litellm` |
| `POSTGRES_DB_USER_PASSWORD` | `openssl rand -hex 24` |

Already existing and reused: `cloudnative-pg` (`POSTGRES_SUPER_PASS`) and
`ai-keys` (`ANTHROPIC_API_KEY`, `XAI_API_KEY`, `ZAI_API_KEY`,
`OPENROUTER_API_KEY`, the same fields agentgateway reads). Written by
PushSecrets, not by hand: `Automation/litellm-sso` (from OpenTofu,
`.agents/skills/authentik-terraform/references/apply-runbook.md`), `litellm-pgvector` (seed command in
`app/pushsecret-pgvector.yaml`) and every `litellm-consumer-*` item.

Until `litellm` exists the ExternalSecret reports `SecretSyncedError`, the
`litellm-db-init` Job cannot start and the proxy sits in
`CreateContainerConfigError`: an unmet prerequisite, not a manifest bug.

## Granting the jev-decisions key

`app/virtualkeys/jev-decisions.yaml` declares the key; its access to
`POST /openrouter/alpha/decisions` is one admin `/key/update` stored only in
LiteLLM's database (no operator field can express it). Apply it once after
the key is first minted and again after any recreation of the key. It runs in
the proxy pod with that pod's master key, looks the key up by alias and never
prints it:

```bash
kubectl -n ai exec -i deploy/litellm -c litellm -- python3 - <<'PY'
import json, os, urllib.parse, urllib.request
BASE, MASTER = "http://127.0.0.1:4000", os.environ["LITELLM_MASTER_KEY"]
def call(path, body=None, method="POST"):
    req = urllib.request.Request(BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + MASTER, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())
query = urllib.parse.urlencode({"key_alias": "jev-decisions", "return_full_object": "true"})
(row,) = call("/key/list?" + query, method="GET")["keys"]
call("/key/update", {"key": row["token"], "metadata": {"allowed_passthrough_routes": ["/openrouter/alpha/decisions"]}})
print(call("/key/info?key=" + row["token"], method="GET")["info"]["metadata"])
PY
```

It must print `{'allowed_passthrough_routes': ['/openrouter/alpha/decisions']}`.
The body replaces the key's whole metadata, which is safe only because this
key has no other metadata; grant exactly that one path. Never add
`spec.metadata` to the CR: the operator would then overwrite the grant.
