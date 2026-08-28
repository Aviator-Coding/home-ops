# Authentik

Manages the configuration of the live Authentik instance at
`https://auth.sklab.dev` as code.

Authentik is the SSO for the whole cluster. Read
[`docs/authentik/terraform.md`](../../docs/authentik/terraform.md) before
touching anything here: it holds the inventory this code was written from, the
import strategy, the state bucket bootstrap, and the apply approval gate.

`secrets-ci.vals.yaml` is a CI-only mirror of `secrets.vals.yaml` (same
1Password item and fields, resolved via 1Password Connect instead of the
interactive `op` CLI) that lets `.github/workflows/terraform-diff.yaml` run a
real, read-only plan on every pull request - see that doc's section 9 for the
mechanism and the one GitHub Actions secret it requires. `tofu apply` is
unaffected by any of this and stays behind section 7's approval gate.

## What this stack owns

| Object | Managed | Why |
| ------ | ------- | --- |
| 2 OIDC applications and their OAuth2 providers (`coder`, `pg-admin`) | yes (adopted) | hand-created in the UI; `import` blocks in `imports.tofu` |
| open-webui | NO, removed 2026-08-28 | deleted from Authentik by the captain on purpose; dropped from this stack rather than recreated |
| 1 forward-auth proxy provider and its `echo` application | yes (adopted) | hand-created in the UI; this is the cluster's ExtAuth |
| Binding of that provider to the embedded outpost | yes (adopted) | hand-created in the UI |
| LiteLLM OAuth2 provider + application | yes (created, `litellm.tofu`) | first stack-created objects; no `import` block; credentials generated in OpenTofu |
| LiteLLM `litellm_role` scope mapping | yes (`litellm.tofu`) | hand-written claim so SSO users land as `proxy_admin`; not a blueprint object |
| LiteLLM-only invalidation flow + user-logout stage + binding | yes (`litellm.tofu`) | RP logout ends the Authentik browser session for LiteLLM only; shared default invalidation flow stays untouched |
| Flows, stages, policies, other property/scope mappings | no, data sources only | all reconciled by Authentik's own built-in blueprints |
| Groups, users, brand, certificates, sources | no, data sources only | blueprint defaults; out-of-band `tofu Writers` role is §5 of the runbook, not a resource here |

The split is not arbitrary. Every object in the "no" rows is created by one of
the 28 built-in `blueprintinstance` entries on this instance. Declaring those as
resources would put OpenTofu and Authentik's blueprint reconciler in a fight over
the same objects. The LiteLLM rows are the deliberate exception: they never
existed on the instance before this stack, so they are created rather than
adopted. Three applies have landed (role mapping, invalidation flow, and the
post-merge `grant_types` fix on the created provider); a fresh plan is empty.
Delivered evidence and traps live in
[`docs/authentik/terraform.md`](../../docs/authentik/terraform.md).

## Safety properties worth keeping

- **`client_secret` on adopted providers is never declared.** It is
  optional+computed in the provider schema, so `import` adopts the live value and
  no plan can rotate it. Declaring it with a wrong value silently breaks every
  login for that application. **LiteLLM is the inverse**: both halves are
  generated in `litellm.tofu` and surfaced via `outputs.tofu`, because there was
  no live secret to protect and the cluster must be told what they are.
- **`grant_types` on created providers must be declared; on adopted ones must
  not.** Same optional+computed shape as `client_secret`, opposite correct
  answer. Omitting it on a create writes `{}` and Authentik rejects authorize
  with `Invalid grant_type for provider` while every offline check stays green.
  Detail: runbook section 7b.
- **`client_id` on adopted providers is fed from 1Password as the existing
  value.** The schema requires it, so it must be declared; it is never generated
  for those apps. LiteLLM's `client_id` is generated (`random_string`) for the
  same reason its secret is.
- **`property_mappings` is declared explicitly on every provider.** It is a plain
  optional list, not optional+computed, so omitting it would plan the removal of
  the scopes a provider currently has.
- **The proxy provider is the highest-blast-radius object here.** Two Envoy
  Gateway SecurityPolicies route every request on their listeners through it.
  Treat any plan diff on it as stop-and-review.
