# Authentik

Manages the configuration of the live Authentik instance at
`https://auth.sklab.dev` as code.

Authentik is the SSO for the whole cluster. Read
[`docs/authentik/terraform.md`](../../docs/authentik/terraform.md) before
touching anything here: it holds the inventory this code was written from, the
import strategy, the state bucket bootstrap, and the apply approval gate.

## What this stack owns

| Object | Managed | Why |
| ------ | ------- | --- |
| 3 OIDC applications and their OAuth2 providers (`coder`, `open-webui`, `pg-admin`) | yes | hand-created in the UI |
| 1 forward-auth proxy provider and its `echo` application | yes | hand-created in the UI; this is the cluster's ExtAuth |
| Binding of that provider to the embedded outpost | yes | hand-created in the UI |
| Flows, stages, policies, property mappings, scope mappings | no, data sources only | all reconciled by Authentik's own built-in blueprints |
| Groups, users, brand, certificates, sources | no, data sources only | all Authentik defaults; nothing hand-made |

The split is not arbitrary. Every object in the "no" rows is created by one of
the 28 built-in `blueprintinstance` entries on this instance. Declaring those as
resources would put OpenTofu and Authentik's blueprint reconciler in a fight over
the same objects.

## Safety properties worth keeping

- **`client_secret` is never declared.** It is optional+computed in the provider
  schema, so `import` adopts the live value and no plan can rotate it. Declaring
  it with a wrong value silently breaks every login for that application.
- **`client_id` is fed from 1Password as the existing value.** The schema
  requires it, so it must be declared; it is never generated.
- **`property_mappings` is declared explicitly on every provider.** It is a plain
  optional list, not optional+computed, so omitting it would plan the removal of
  the scopes a provider currently has.
- **The proxy provider is the highest-blast-radius object here.** Two Envoy
  Gateway SecurityPolicies route every request on their listeners through it.
  Treat any plan diff on it as stop-and-review.
