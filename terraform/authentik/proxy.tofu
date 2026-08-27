# The forward-auth proxy provider behind the cluster's ExtAuth.
#
# THIS IS THE HIGHEST-BLAST-RADIUS OBJECT IN THE STACK. Two Envoy Gateway
# SecurityPolicies send every request on their listeners to the Authentik embedded
# outpost that this provider is bound to:
#
#   kubernetes/apps/base/network/echo/app/securitypolicy.yaml
#   kubernetes/apps/base/ai/agentgateway/app/policies/authentik-policy.yaml
#
# The second one covers the agentgateway `internal` AND `public` https listeners.
# A wrong change to `mode`, `external_host`, `cookie_domain` or `skip_path_regex`
# does not degrade one app - it locks or unlocks every SSO-gated surface at once.
# Treat any plan diff on this resource as a stop-and-review, not a routine update.

# Read-only. The embedded outpost is Authentik's own
# (`managed = goauthentik.io/outposts/embedded`) and its 20-key `_config` blob is
# reconciled by Authentik itself.
data "authentik_outpost" "embedded" {
  name = "authentik Embedded Outpost"
}

resource "authentik_provider_proxy" "forward_auth" {
  # Live name, typo included. It is `externel`, not `external`, on the running
  # instance. Correcting the spelling here is a rename of a live object that the
  # ExtAuth path depends on - leave it alone.
  name = "sklab-externel-auth-provider"

  mode          = "forward_domain"
  external_host = "https://auth.${var.cluster_domain}"
  cookie_domain = ".${var.cluster_domain}"

  authorization_flow = data.authentik_flow.provider_authorization_explicit_consent.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  intercept_header_auth        = true
  basic_auth_enabled           = false
  internal_host_ssl_validation = true

  # Unused in forward_domain mode, but declared so the value is pinned rather than
  # left to drift. `skip_path_regex` in particular is an auth bypass if it is ever
  # set by accident - an empty value means nothing bypasses ExtAuth.
  internal_host   = ""
  skip_path_regex = ""

  access_token_validity  = "hours=24"
  refresh_token_validity = "days=30"

  property_mappings = data.authentik_property_mapping_provider_scope.proxy_default.ids
}

resource "authentik_application" "echo" {
  name               = "echo"
  slug               = "echo"
  protocol_provider  = authentik_provider_proxy.forward_auth.id
  meta_launch_url    = ""
  group              = ""
  open_in_new_tab    = false
  policy_engine_mode = "any"
}

# The binding of this provider onto Authentik's embedded outpost.
#
# This is what actually makes ExtAuth work: the SecurityPolicies above forward to
# the `ak-outpost-authentik-embedded-outpost` Service, and that outpost only
# answers for providers attached to it.
#
# Adopting it is safe in both directions. The resource's create path checks
# `slices.Contains(outpost.Providers, providerID)` first and returns without
# issuing a PATCH when the binding already exists, and the import below means even
# that read-only create never runs. Only `outpost` and `protocol_provider` are
# touched - the outpost's own 20-key `_config` blob is left to Authentik.
#
# What to be careful with is the reverse: this resource's *delete* rewrites the
# outpost's provider list. Removing this block, or running `tofu destroy`, unbinds
# ExtAuth for the whole cluster. See the destroy warning in the runbook.
resource "authentik_outpost_provider_attachment" "forward_auth" {
  outpost           = data.authentik_outpost.embedded.id
  protocol_provider = authentik_provider_proxy.forward_auth.id
}
