# Home Assistant single sign-on via the hass-oidc-auth custom component.
# CREATED by this stack (no import block). Public client + PKCE: there is no client secret, so
# nothing flows through 1Password and the client_id is a plain literal that
# the Home Assistant ConfigMap repeats. Kubernetes side: skill home-automation
# (references/home-assistant.md).

locals {
  # Public identifier. Must match client_id in
  # kubernetes/apps/base/home-automation/home-assistant/app/resources/auth_oidc.yaml.
  home_assistant_client_id = "home-assistant"
}

resource "authentik_provider_oauth2" "home_assistant" {
  name          = "Home Assistant"
  client_id     = local.home_assistant_client_id
  client_type   = "public"
  sub_mode      = "hashed_user_id"
  issuer_mode   = "per_provider"
  logout_method = "backchannel"

  authorization_flow = data.authentik_flow.provider_authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  # MUST be declared on a CREATED provider, see litellm.tofu.
  grant_types = ["authorization_code"]

  include_claims_in_id_token = true
  # Default id_token_signing_alg in hass-oidc-auth is RS256, which this key pair produces.
  signing_key       = data.authentik_certificate_key_pair.self_signed.id
  property_mappings = data.authentik_property_mapping_provider_scope.oauth2_default.ids

  access_code_validity    = "minutes=1"
  access_token_validity   = "minutes=5"
  refresh_token_validity  = "days=30"
  refresh_token_threshold = "seconds=0"

  # Path fixed by hass-oidc-auth: <HA URL>/auth/oidc/callback.
  allowed_redirect_uris = [
    {
      matching_mode     = "strict"
      redirect_uri_type = "authorization"
      url               = "https://hass.${var.cluster_domain}/auth/oidc/callback"
    }
  ]
}

resource "authentik_application" "home_assistant" {
  name               = "Home Assistant"
  slug               = "home-assistant"
  protocol_provider  = authentik_provider_oauth2.home_assistant.id
  meta_launch_url    = "https://hass.${var.cluster_domain}"
  group              = ""
  open_in_new_tab    = false
  policy_engine_mode = "any"
}
