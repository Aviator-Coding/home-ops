# LiteLLM (governance layer)

[BerriAI/litellm](https://github.com/BerriAI/litellm) proxy, redeployed 2026-08-26
per captain decisions B4/D4 as a narrow **governance layer**, not a return to its
pre-2026-06-07 role as this cluster's unified LLM proxy (removed in #941 - see
`docs/ai-system/agentgateway/GLOSSARY.md`). Full rationale, the six-line lab
design, and the knobs to raise budgets later: `docs/ai-system/litellm/README.md`.
Fallback chains, allow-list interaction, alerts, internal route, and failover
runbook: `docs/ai-system/litellm/fallbacks.md`.

Since captain decision **O1** (2026-08-26) it is delivered by the
[home-operations litellm-operator](https://github.com/home-operations/litellm-operator)
(`kubernetes/apps/base/ai/litellm-operator/`) rather than a bjw-s app-template
HelmRelease. Nothing in this directory renders a Deployment: the operator owns
the proxy Deployment, its Service, and the `config.yaml` ConfigMap, all derived
from the CRs here.

| File | What it declares |
| --- | --- |
| [`app/litellmproxy.yaml`](app/litellmproxy.yaml) | The `LiteLLMProxy` - image, probes, envFrom, admin-API access, `litellm_settings`. Deliberately **no** `spec.route`. |
| [`app/models/`](app/models/) | Six `LiteLLMModel` CRs: `auto`, `chat-ha`, `claude-opus-5`, `claude-sonnet-5`, `qwen3.6-35b-a3b`, `qwen3.6-35b-a3b-classifier`. `qwen3.6-35b-a3b` is terminal (no fallback) for local-only keys; `chat-ha` is the same backend carrying the cloud fallback for entitled keys. The D3 auto-router lives in `auto.yaml`. |
| [`app/virtualkeys/`](app/virtualkeys/) | One `LiteLLMVirtualKey` + its `PushSecret` per consumer (D4). |
| [`app/httproute-internal.yaml`](app/httproute-internal.yaml) | Standalone internal `HTTPRoute` named `litellm-internal` (not `litellm`) - the operator deletes any route whose name matches the proxy CR when `spec.route` is absent. |
| [`app/dbinit.yaml`](app/dbinit.yaml) | `postgres-init` Job creating the role + database in the shared `postgres-17` cluster. |
| [`app/externalsecret.yaml`](app/externalsecret.yaml) | `litellm-secret` (master/salt key, `DATABASE_URL`, `INIT_POSTGRES_*`, `ANTHROPIC_API_KEY`). |
| `app/servicemonitor.yaml`, `app/prometheusrule.yaml` | Scrape + alerts against the operator-rendered Service. |

**Scope per B4 (public half still non-negotiable):**
- Does **not** front the public listener. No `envoy-external` parentRef, no
  `AgentgatewayBackend`. That half of B4 is unchanged and must stay that way.
- **Internal gateway: captain-approved 2026-08-26.** A standalone
  [`app/httproute-internal.yaml`](app/httproute-internal.yaml) attaches to `envoy-internal` at
  `litellm.${SECRET_DOMAIN}`, alongside the unchanged in-cluster address
  `http://litellm.ai.svc.cluster.local:4000`. This relaxes the previous
  "no route of any kind" posture for the **internal** gateway only.
  The route is hand-written rather than operator-owned (`spec.route`) because
  the operator's route schema is `{hostnames, parentRefs, filters}` with no
  annotations field, and this repo's Gatus/Homepage/external-dns conventions
  are all annotation-driven - rationale and the live DNS evidence are in that
  file's header and in `docs/ai-system/litellm/fallbacks.md`.
- No extra auth surface was invented for it: 49 of the 52 internal HTTPRoutes
  in this cluster carry no `SecurityPolicy`, the internal gateway is itself the
  boundary (private VLAN + split DNS, never internet-reachable), and LiteLLM's
  own master-key/DB login still guards its admin UI and API. Authentik ExtAuth
  is available (`monitoring/kromgo-auth` is the pattern) if SSO in front of the
  UI is wanted later.
- Zero changes to `agentgateway/` (the existing envoy AI gateway) or to
  `vllm/` (`vllm-app.ai.svc.cluster.local:8000`, which this app points at as
  a backend and never modifies).
- Direct local model is still the lab-proven six-line `qwen3.6-35b-a3b`
  entry, now [`app/models/qwen3.6-35b-a3b.yaml`](app/models/qwen3.6-35b-a3b.yaml).
  D3 adds the additive `auto` router alias (classifier + Anthropic tier
  backends) on top - see `docs/ai-system/litellm/auto-router.md`. Local
  entries keep `model_info` governance-accounting prices; cloud tiers use
  LiteLLM's built-in cost map. Prometheus metrics callback is on.

**Per-consumer governance (D4):** virtual keys with a model allow-list and a
deliberately tiny spend/rate budget, one `LiteLLMVirtualKey` per consumer in
[`app/virtualkeys/`](app/virtualkeys/). The operator mints each key through the
proxy's `/key` API, stores it in an operator-owned Secret, and PATCHes the live
key when the spec changes (the credential itself is preserved across a budget
edit). LiteLLM still has no way to declare a budgeted key from config alone,
which is why this app carries a Postgres dependency (see
`docs/ai-system/litellm/README.md#why-postgres`) unlike the rest of this
namespace's stateless-by-preference apps.

## Pod security posture (known gap, accepted deliberately)

`LiteLLMProxySpec` exposes **no** `securityContext`, `serviceAccountName`,
`automountServiceAccountToken`, `strategy`, `initContainers` or `startupProbe`
field, and `buildDeployment` in the operator sets none of them. Compared with
the app-template deployment this replaced, the proxy pod therefore loses:

| Setting | Before (app-template) | After (operator-rendered) |
| --- | --- | --- |
| pod `runAsNonRoot` / `runAsUser: 1000` / `runAsGroup`+`fsGroup: 100` | set | **absent** |
| pod `seccompProfile: RuntimeDefault` | set | **absent** |
| container `allowPrivilegeEscalation: false` | set | **absent** (k8s default: true) |
| container `capabilities: drop [ALL]` | set | **absent** |
| `automountServiceAccountToken: false` | set | **absent** (default SA token is mounted) |
| `strategy: Recreate` | set | RollingUpdate (k8s default) |
| `readOnlyRootFilesystem: false` | explicit (proven needed for this image) | same by default |
| `serviceAccountName` | pinned to `default` | unset, i.e. `default` |

The image itself is `litellm-non_root`, which drops to a non-root UID via its
own Dockerfile `USER`, so the pod does not actually run as root - but that is
now the image's promise rather than a Kubernetes-enforced constraint, and the
`default` ServiceAccount token is mounted where it previously was not (that SA
holds no RoleBindings in `ai`, so it grants only `system:authenticated`).
`strategy` moving to RollingUpdate means a rollout can briefly run two proxy
replicas against the same database; LiteLLM tolerates this, but it is a change.

None of this is fixable from Git today - it needs upstream fields on the CRD.
Do not "fix" it by wrapping the operator's Deployment in a kustomize patch:
the operator reconciles that Deployment and would revert it. This is the
hardening cost the captain accepted with O1; reopen it when the CRD grows a
`podSecurityContext`/`securityContext` field.

Two apparent gaps that turned out **not** to be gaps:
- **Config/secret reload.** The operator rolls the pod on a rendered-config
  change via its own `config-hash` pod annotation. Secret rotation is covered
  by `spec.podAnnotations` carrying `reloader.stakater.com/auto`, because
  Reloader falls back to the **pod template** annotations when the workload
  carries none (`pkg/common/common.go` `ShouldReload`, verified against
  v1.4.21 - the version running in `kube-system`).
- **Renovate.** The image pin on a CR's `spec.image` is picked up by
  Renovate's `kubernetes` manager, which scans every `image:` field under
  `kubernetes/**.yaml` regardless of `kind` (this repo already bumps
  `MCPServer` CR images that way). The `>=1.93.0` floor in
  `.renovate/overrides.json5` matches on the package name, so it keeps
  applying. No custom manager was needed.

## Prerequisites (before first sync)

1Password item **`litellm`** (`onepassword` ClusterSecretStore vault):

| Field | How to generate |
| --- | --- |
| `LITELLM_MASTER_KEY` | `echo sk-$(openssl rand -hex 32)` |
| `LITELLM_SALT_KEY` | `echo sk-$(openssl rand -hex 32)` |
| `POSTGRES_DB_NAME` | `litellm` |
| `POSTGRES_DB_USER_NAME` | `litellm` |
| `POSTGRES_DB_USER_PASSWORD` | `openssl rand -hex 24` |

The shared `cloudnative-pg` 1Password item (`POSTGRES_SUPER_PASS`) already
exists - every `postgres-17` client app reads it the same way (see
`kubernetes/apps/base/database/cloudnative-pg/Readme.md`). So does the
`ai-keys` item, which this app's `ExternalSecret` extracts `ANTHROPIC_API_KEY`
from for the auto-router's cloud tier - the same item every
`agentgateway/app/backends/*.yaml` already reads. **No new 1Password item is
needed for the router or for the operator**; `litellm` above remains the only
one to create.

> Until the `litellm` item exists, this app's `ExternalSecret` reports
> `SecretSyncedError` / `key not found in 1Password Vaults: litellm`, the
> `litellm-db-init` Job cannot start, and the proxy pod stays in
> `CreateContainerConfigError`. That is this prerequisite being unmet, not a
> manifest bug.

The `litellm-consumer-demo` / `litellm-consumer-router-demo` /
`litellm-consumer-ha-demo` 1Password items are **written** by the PushSecrets,
not read - they do not need to pre-exist.

## Database bootstrap

`app/dbinit.yaml` is a standalone `Job`, not a CNPG `Database` CR. The CRD is
available (CNPG 1.30.0 serves `databases.postgresql.cnpg.io/v1`), but its
`spec.owner` is required and CNPG does **not** create the role - declarative
roles are a separate feature on the `Cluster` (`spec.managed.roles`), so using
it would mean editing the shared `postgres-17` Cluster that 15+ other apps
depend on. The Job keeps the same `ghcr.io/home-operations/postgres-init` image
and `INIT_POSTGRES_*` contract every other Postgres-backed app in this repo
uses. It is idempotent, and carries `kustomize.toolkit.fluxcd.io/force: "true"`
so a Renovate image bump can delete-and-recreate an otherwise-immutable Job.

## Verifying end to end

`docs/ai-system/litellm/README.md#verification-runbook` has the exact
`kubectl port-forward` + `curl` commands to call the `demo` consumer key
against `vllm-app` through this proxy, and to confirm the allow-list and
budget are actually enforced (a second model is rejected; a request past
budget fails once spend is exhausted). Spend budgets require the non-zero
`info.extra` per-token prices on the local model CRs - see
`app/models/qwen3.6-35b-a3b.yaml`.

The complexity-tier auto-router (`auto` alias) has its own design, tuning and
verification notes in `docs/ai-system/litellm/auto-router.md`. Fallback chains
(`chat-ha` / `ha-demo`), alerts, and the post-merge failover proof live in
`docs/ai-system/litellm/fallbacks.md`.
