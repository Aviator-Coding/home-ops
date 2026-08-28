# Scope mappings are READ-ONLY here, for the same reason as flows.tofu: the stock
# property mappings on this instance carry a `managed` marker (they come from the
# `system/providers-*.yaml` blueprints). Exactly one hand-written mapping exists
# outside this file: authentik_property_mapping_provider_scope.litellm_role in
# litellm.tofu (unmanaged, second-apply create path).
#
# `property_mappings` on a provider is a plain optional list, NOT optional+computed
# - leaving it out of a provider block would make OpenTofu plan the removal of the
# scopes that provider currently has. Every provider below therefore sets it
# explicitly to match live.

# openid + email + profile: the set carried by all three OAuth2 providers.
data "authentik_property_mapping_provider_scope" "oauth2_default" {
  managed_list = [
    "goauthentik.io/providers/oauth2/scope-openid",
    "goauthentik.io/providers/oauth2/scope-email",
    "goauthentik.io/providers/oauth2/scope-profile",
  ]
}

# The proxy provider additionally carries the entitlements and proxy scopes.
data "authentik_property_mapping_provider_scope" "proxy_default" {
  managed_list = [
    "goauthentik.io/providers/oauth2/scope-openid",
    "goauthentik.io/providers/oauth2/scope-email",
    "goauthentik.io/providers/oauth2/scope-profile",
    "goauthentik.io/providers/oauth2/scope-entitlements",
    "goauthentik.io/providers/proxy/scope-proxy",
  ]
}
