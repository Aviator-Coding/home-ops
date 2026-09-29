---
name: authentik-terraform
description: "Read before touching terraform/** or Authentik SSO: running tofu plan/apply, adding or importing an Authentik application, provider, flow or property mapping, changing the S3 state backend or its credentials, wiring a new SSO-gated route, or diagnosing a terraform-diff CI failure. Covers why tofu apply is never a follow-on to a green PR, and the four traps tofu validate cannot catch."
---

# Authentik SSO and the OpenTofu stack

`terraform/authentik/` is applied and holds live SSO state. Flux does not
reconcile it. Nothing in CI runs `tofu apply`. The directory name stays
`authentik-terraform` (the 1Password item uses the same name).

Server chart tag is the `OCIRepository` in
`kubernetes/apps/base/security/authentik/app/helmrelease.yaml` (`2026.8.3`
when this skill was written). One chart ships server, worker and the embedded
outpost. `postgresql.enabled: false`; the DB is `postgres-17`.

## Tripwires

1. **`tofu apply` and `tofu destroy` need an explicit, current go-ahead.**
   A green PR, a clean plan and a passing `terraform-diff` are not that
   approval. Apply a saved plan from `secrets-apply.vals.yaml`. Never a bare
   `tofu apply`. `AUTHENTIK_APPLY_TOKEN` stays absent from 1Password until
   that go-ahead, so a stray apply fails closed.
   [apply-runbook.md](references/apply-runbook.md)
2. **Four traps `tofu validate` cannot see.**
   - Omitting `grant_types` is correct on an imported provider and fatal on a
     created one (`grant_types = {}`, every authorize returns
     `invalid_request`).
   - `authentik_flow.id` is the slug. `invalidation_flow` and
     `authentik_flow_stage_binding.target` need `.uuid`.
   - The S3 backend fails open to real AWS when `AWS_ENDPOINT_URL_S3` is unset.
     `backend.tofu` omits `endpoints` on purpose. Never set the endpoint to
     `https://s3.sklab.dev` (OpenTofu's signer does not survive Envoy).
   - The write role's model-level `change_flow` reaches every flow, including
     `default-authentication-flow`. Snapshot flows and stage bindings from the
     database before and after an apply.
3. **Imported providers omit `client_secret`.** Declaring it rotates the live
   secret and breaks every login for that app. `property_mappings` is the
   opposite: it must be declared or the plan strips scopes. The created
   LiteLLM provider inverts the secret rule (generated in OpenTofu, pushed to
   the cluster). [inventory.md](references/inventory.md)
4. **Do not turn blueprint `data` sources into resources.** Authentik's
   blueprint reconciler already owns flows, stages, policies and mappings.
   OpenTofu and that reconciler would fight.
5. **State is plaintext OAuth secrets in a hand-made RGW bucket.** Not an
   ObjectBucketClaim (`reclaimPolicy: Delete` plus Flux prune would destroy
   it). [state-and-credentials.md](references/state-and-credentials.md)
6. **ExtAuth is one domain-wide proxy provider** (`forward_domain`, live name
   `sklab-externel-auth-provider`, typo included). `skip_path_regex` is an
   auth bypass if it is ever non-empty. A new namespace needs a `from:` entry
   on `kubernetes/apps/base/security/authentik/app/referencegrant.yaml` or the
   SecurityPolicy's backendRef resolves to nothing. An unauthenticated 302 to
   login is host-agnostic and does not prove that host is covered.
7. **Set the Authentik Base URL system setting before the 2026.11 line.**
   It is optional on 2026.8 and mandatory in 2026.11. 2026.6 and 2026.7 are
   not release lines. [upgrade-checklist.md](references/upgrade-checklist.md)
8. **Never seed `litellm-sso-credentials` with client-side `kubectl apply`.**
   The value lands in `last-applied-configuration`. `kubectl create`, or
   server-side apply. Skill `secrets-1password`.

## Where things live

| What | Path |
|---|---|
| Stack | `terraform/authentik/*.tofu` |
| Plan / CI / apply env | `secrets.vals.yaml`, `secrets-ci.vals.yaml`, `secrets-apply.vals.yaml` |
| Conventions | `terraform/tofu.md` and [conventions.md](references/conventions.md) |
| Server | `kubernetes/apps/base/security/authentik/` |
| ReferenceGrant | `app/referencegrant.yaml` |
| CI plan | `.github/workflows/terraform-diff.yaml` |
| Schema CI | `.github/workflows/validate.yaml` terraform job, `scripts/ci/tofu-validate.sh` |

## Procedures

- Apply, rollback, destroy: [apply-runbook.md](references/apply-runbook.md).
- State, tokens, Connect scope: [state-and-credentials.md](references/state-and-credentials.md).
- What CI plans: [ci-plan.md](references/ci-plan.md).
- Owned objects vs blueprints: [inventory.md](references/inventory.md).
- File layout and style: [conventions.md](references/conventions.md).
- Server upgrade checks: [upgrade-checklist.md](references/upgrade-checklist.md).

## Verify

- `./scripts/ci/tofu-validate.sh` (schema, no credentials).
- `python3 scripts/ci/tofu-authentik-stack-test.py` and
  `python3 scripts/ci/terraform-ci-workflows-test.py`.
- Review plan: `cd terraform/authentik && vals exec -i -f secrets.vals.yaml -- tofu plan`.
- ExtAuth still redirects: `curl -sSI https://echo.${SECRET_DOMAIN} | head -1`.
