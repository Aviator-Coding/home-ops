# What the stack owns

Blueprints own the flows, stages, policies, property mappings, groups, brand,
certificates and built-in source. Leave those as `data` sources.

OpenTofu resources:

| Object | How |
|---|---|
| Applications `coder`, `pg-admin`, `echo` and their providers | `import` blocks. Omit `client_secret`. Declare `property_mappings` |
| Proxy provider `sklab-externel-auth-provider` | imported. Live spelling is `externel`. Renaming it is a live rename of the ExtAuth provider. `mode = forward_domain`, `skip_path_regex = ""` |
| LiteLLM provider, application, `litellm_role` scope mapping, LiteLLM-only invalidation flow | created in `litellm.tofu`. `client_secret` is generated. `grant_types = ["authorization_code"]` |

`open-webui` was removed from Authentik and from the stack (`tofu state rm`).
Do not re-import it.

`authentik_outpost.embedded` is a data source
(`managed = goauthentik.io/outposts/embedded`). Authentik reconciles that
outpost's config.

## ExtAuth surfaces

One provider, `forward_domain`, `external_host` `https://auth.${SECRET_DOMAIN}`,
`cookie_domain` `.${SECRET_DOMAIN}`. SecurityPolicies in git:

- `network/echo`
- `home-automation/home-assistant`
- `monitoring/kromgo`
- `ai/opencode`
- `database/falkordb`
- `flux-system/headlamp`

ReferenceGrant `allow-authentik-access` allows `from` those six namespaces to
Service `ak-outpost-authentik-embedded-outpost`. A SecurityPolicy in a new
namespace is accepted and then resolves its backend to nothing until the
grant lists that namespace.

The outpost's pre-auth check returns 302 to login for a Host that maps to no
route. Coverage of a new hostname is an authenticated login after merge, not
a curl.

agentgateway's Authentik policy
(`kubernetes/apps/base/ai/agentgateway/app/policies/authentik-policy.yaml`)
sits on the `internal` and `public` listeners. A diff on the proxy provider
covers that path too.

## Created vs imported

| Field | Imported provider | Created provider |
|---|---|---|
| `client_secret` | omit (optional+computed, adopts live) | declare (nothing to adopt) |
| `grant_types` | omit | declare `["authorization_code"]` |
| `property_mappings` | declare | declare |
| flow ids | `.uuid`, never `.id` | `.uuid` |
