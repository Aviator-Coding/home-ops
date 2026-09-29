# Platform: operator delivery, scope, route, SSO, pod posture

## What LiteLLM is here

A narrow governance layer beside `agentgateway`, not the cluster's unified LLM
proxy (that role was removed in #941 and agentgateway's own `/v1` replaced it).
It adds per-consumer virtual keys, budgets, fallback chains and an auto-router.
It never replaces agentgateway, and no agentgateway traffic is routed through it.

Scope rules (captain decisions B4/D4, still binding):

- **Never the public listener.** No `envoy-external` parentRef and no
  `AgentgatewayBackend`. The only route is the internal one
  (`litellm.${SECRET_DOMAIN}` on `envoy-internal`). In-cluster callers use
  `http://litellm.ai.svc.cluster.local:4000`.
- No changes to `ai/agentgateway` or `ai/vllm`. LiteLLM only reads
  `vllm-app`'s `/v1` as a backend.
- D4: every consumer gets a model allow-list and a spend/rate budget
  ([governance-keys.md](governance-keys.md)).

## Delivery: home-operations litellm-operator (decision O1)

- Chart: `kubernetes/apps/base/ai/litellm-operator/app/helmrelease.yaml`
  (OCI `ghcr.io/home-operations/charts/litellm-operator`, tag pinned there).
- CRs in `kubernetes/apps/base/ai/litellm/app/`: `LiteLLMProxy`
  (`litellmproxy.yaml`), one `LiteLLMModel` per model (`models/`), one
  `LiteLLMVirtualKey` + `PushSecret` per consumer (`virtualkeys/`).
- The operator owns the proxy Deployment, its Service and the rendered
  `config.yaml` ConfigMap (`litellm-config`, named `<proxy>-config`). None of
  them appear in Git.
- `applyMode: file`: models render into `config.yaml` and the Deployment rolls
  on change (its own `config-hash` pod annotation). `api` mode would turn
  `store_model_in_db` on and make the running DB, not Git, the source of truth.
- `generalSettings.store_model_in_db: false`, explicitly. Side effects: the
  Admin UI cannot edit models (keys are DB rows; UI/API edits to them are
  reverted by the operator), and UI "make public" returns 500
  (see [model-catalog.md](model-catalog.md), AI Hub).
- `llmkube.autoRegister: false` on the operator: LLMKube is not installed here,
  and a chart-default flip must not start minting undeclared `LiteLLMModel`s.
- The operator's ValidatingWebhookConfiguration is `failurePolicy: Fail` for
  every LiteLLM kind. With the operator down, the API server **rejects** the
  CRs at admission. That is why `kubernetes/apps/main/ai/litellm.yaml`
  `dependsOn` `litellm-operator`, whose overlay health-checks the Deployment.
- `routerSettings` is a free-form passthrough in the CRD
  (`x-kubernetes-preserve-unknown-fields`). The API server validates nothing
  there, so a typo lands silently in the rendered config.
- Operator CRD install: the chart ships its CRDs in `crds/`. `CreateReplace`
  comes from the cluster-apps Kustomization, so do not restate it on the HR.

## Image and version floor

- `LiteLLMProxy.spec.image` is `ghcr.io/berriai/litellm-non_root:vX.Y.Z`
  (plain tags; upstream dropped `main-vX.Y.Z-stable` after v1.81.x).
- **Floor v1.93.0**: `classifier_type: llm` first exists there; below it the
  auto-router config parses and is silently ignored
  ([auto-router.md](auto-router.md)). Enforced by
  `.renovate/overrides.json5` (`allowedVersions: ">=1.93.0"`) and
  `scripts/ci/litellm-auto-router-test.py`.
- Renovate picks the image up from the CR's `image:` field; no custom manager.

## Why Postgres, why Dragonfly

- **Postgres**: LiteLLM cannot declare a budgeted or rate-limited key from
  config. Keys exist only through `/key/generate`, and without a
  `DATABASE_URL` they live in memory and vanish on restart. So the proxy
  depends on the shared `postgres-17` CNPG cluster (`litellm` database).
- **Dragonfly** (`litellm-dragonfly`, `kubernetes/components/dragonfly` on the
  overlay): LiteLLM runs several workers inside one pod, so without a shared
  Redis-API store rate limits, budgets and router state are per worker and
  spend overshoots. `replicas: 1` does not avoid it. Wired as
  `routerSettings.redis_host/redis_port` and `litellmSettings.cache`
  (exact-match response cache, 300s TTL). Never set
  `LITELLM_DISABLE_NO_REDIS_WARNING`; the fix is the store. No password: the
  component ships none, same as every other Dragonfly consumer.

## Database bootstrap

`app/dbinit.yaml` is a `postgres-init` Job, not a CNPG `Database` CR: that
CRD requires `spec.owner` and does not create the role (roles live in
`spec.managed.roles` on the shared Cluster, which 15+ apps depend on). There
are no initContainers on `LiteLLMProxySpec` either. The Job is idempotent and
carries `kustomize.toolkit.fluxcd.io/force: enabled` (the only value Flux
honours) so an image bump can recreate the immutable Job. Re-run by hand:
`kubectl -n ai delete job litellm-db-init`. On a first deploy the proxy may
crashloop for a few seconds until the Job finishes.

## Internal route

`app/httproute-internal.yaml`, a hand-written `HTTPRoute` named
`litellm-internal`:

- **Never name it `litellm`.** When `spec.route` is absent the operator
  deletes any HTTPRoute named after the `LiteLLMProxy`, as an orphan. It does
  so on the next reconcile, so the route works at first and vanishes later.
- **Never set `spec.route`.** The operator's route schema is
  `{hostnames, parentRefs, filters}` with no annotations, so it cannot carry
  the Gatus/Homepage annotations (DNS is not the reason: external-dns takes
  the target from the parent Gateway). It would also create a second,
  competing route.
- The Gatus check targets `/health/readiness`, which returns
  `{"status":"healthy","db":"connected"}` and so covers Postgres too.
  Health endpoints are unauthenticated; `/metrics` needs a bearer.
- A `/anthropic` PathPrefix rule plus `HTTPRouteFilter`
  `litellm-anthropic-passthrough-block` returns 404 on the hostname path.
  It is the outer layer; the proxy's `pass_through_endpoints` also close the
  Service DNS path ([passthrough-lockdown.md](passthrough-lockdown.md)).
- No ExtAuth `SecurityPolicy`, deliberately: the internal gateway is the
  boundary, and `/v1/*` without a key returns 401.

## UI single sign-on (Authentik OIDC)

- Authentik side is OpenTofu: `terraform/authentik/litellm.tofu` (created, not
  imported). Never `tofu apply` without a current go-ahead (skill
  `authentik-terraform`).
- Credential hop: tofu output -> `litellm-sso-credentials` Secret (created once
  by hand) -> `app/pushsecret-sso.yaml` -> 1Password `Automation/litellm-sso`
  (single-vault `onepassword-automation` store) -> `app/externalsecret.yaml`
  -> `GENERIC_CLIENT_ID/SECRET`. Runbook: skill `authentik-terraform`, `references/apply-runbook.md` (push a generated LiteLLM client).
- Non-secret SSO settings are plain `env` on `litellmproxy.yaml`:
  - `PROXY_BASE_URL` drives the redirect URI (unset, LiteLLM falls back to
    the in-cluster Service URL, which a browser cannot follow)
    (`<PROXY_BASE_URL>/sso/callback`). Change it only together with the
    allowed redirect URI in `litellm.tofu`, or every login fails with an
    opaque redirect_uri error.
  - `GENERIC_SCOPE` must include `litellm_role`, matching the provider's
    property mappings. Without it first login lands as
    `INTERNAL_USER_VIEW_ONLY`.
  - `GENERIC_USER_ROLE_ATTRIBUTE=litellm_role`; the tofu scope mapping
    returns exactly `proxy_admin`, so SSO users land as proxy admins.
  - `AUTO_REDIRECT_UI_LOGIN_TO_SSO=true`; master-key browser login moves to
    `/fallback/login`. `PROXY_LOGOUT_URL` ends the Authentik session through
    a LiteLLM-only invalidation flow.
- Probing: the SSO start path is `/sso/key/generate` (there is no
  `/sso/login`) and `/ui/` is a client-side SPA, so bare HTTP probes read
  404/200 even when SSO works. Drive the real authorize handshake.
- Tofu traps (also in skill `authentik-terraform`): reference the
  invalidation flow by `.uuid`, never `.id` (the id is the slug); a created
  provider must declare `grant_types` or Authentik rejects every authorize.

## Pod security posture (accepted gap)

`LiteLLMProxySpec` has no `securityContext`, `serviceAccountName`,
`automountServiceAccountToken`, `strategy`, `initContainers` or
`startupProbe`. So the proxy pod has no pod/container hardening, the `default`
SA token is mounted (that SA holds no RoleBindings in `ai`), and rollouts are
RollingUpdate (two proxies may briefly share the DB; LiteLLM tolerates it).
The image `litellm-non_root` still drops to a non-root UID. Do not patch the
operator's Deployment with kustomize: the operator reverts it. Reopen when the
CRD grows a securityContext field, and restore the old app-template values:
uid 1000, gid/fsGroup 100, seccomp `RuntimeDefault`, no privilege escalation,
drop ALL, `automountServiceAccountToken: false`, `strategy: Recreate`. Keep
`readOnlyRootFilesystem: false`: this image needs a writable root.

Not gaps:

- Secret rotation: `spec.podAnnotations` carries
  `reloader.stakater.com/auto`. Reloader falls back to pod-template
  annotations when the workload has none.
- No startupProbe: liveness waits 240s (`initialDelaySeconds`) then keeps a
  30s x 3 window, emulating the old 300s boot budget.

## Resources

`resources.limits.memory: 4Gi`. The bare `GET /spend/logs` (no `request_id`
or date bounds) walks the whole spend-log table in-process and OOM-killed the
proxy at the old 2Gi limit. The raise is only a backstop
([spend-logs.md](spend-logs.md)).

## Metrics scrape

`app/servicemonitor.yaml` scrapes `/metrics` with `LITELLM_MASTER_KEY`
(deliberate: no second secret). Consequence: Prometheus holds the full admin
credential, which since request logging also reads prompt/response bodies.
Revisit before any broader exposure. The operator labels its Service
`app.kubernetes.io/name=litellm`, and the Service name gives `job="litellm"`.
Renaming the `LiteLLMProxy` CR changes `job=` and breaks `LiteLLMProxyDown`.
`/metrics` 307-redirects to `/metrics/`; Prometheus follows it.
