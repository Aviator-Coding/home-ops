# LiteLLM UI single sign-on.
#
# Unlike every other resource in this stack, this one is NEW: there is no
# matching object on the live instance and no `import` block for it. It is the
# first thing this stack creates rather than adopts, so it is the only place a
# plan is expected to show an addition.
#
# The credential handling is therefore the opposite of applications.tofu, and
# deliberately so. There, `client_secret` is left undeclared because declaring it
# would rotate a live secret. Here there is no live secret to protect, and
# LiteLLM has to be told what it is, so both halves are generated in OpenTofu and
# surfaced through outputs.tofu. Neither is ever written into Git.

resource "random_string" "litellm_client_id" {
  length  = 40
  special = false
  # Authentik's own client ids are 40 alphanumeric characters; matching that
  # keeps the object indistinguishable from a UI-created one.
}

resource "random_password" "litellm_client_secret" {
  length  = 128
  special = false
  # Alphanumeric on purpose: this value is handed to LiteLLM through an
  # environment variable, and punctuation in env values is a portability trap.
}

resource "authentik_provider_oauth2" "litellm" {
  name          = "LiteLLM"
  client_id     = random_string.litellm_client_id.result
  client_secret = random_password.litellm_client_secret.result
  client_type   = "confidential"
  sub_mode      = "hashed_user_id"
  issuer_mode   = "per_provider"
  logout_method = "backchannel"

  # Implicit consent, matching open-webui: this is an interactive UI login for
  # an operator tool, and an explicit consent screen on every sign-in buys
  # nothing here. The three imported OAuth2 providers use the same two stock
  # flows; nothing new is introduced.
  authorization_flow = data.authentik_flow.provider_authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id

  # openid + email + profile, which is exactly LiteLLM v1.98.0's default
  # GENERIC_SCOPE ("openid email profile", read from its ui_sso.py). Keeping the
  # provider's mappings and the app's requested scope identical means a scope
  # mismatch cannot be the cause of a failed login.
  property_mappings = data.authentik_property_mapping_provider_scope.oauth2_default.ids

  access_code_validity    = "minutes=1"
  access_token_validity   = "minutes=5"
  refresh_token_validity  = "days=30"
  refresh_token_threshold = "seconds=0"

  # LiteLLM builds this as get_custom_url(PROXY_BASE_URL) + "/sso/callback"
  # (ui_sso.py get_redirect_url_for_sso, sso_callback_route="sso/callback").
  # Read out of the running v1.98.0 image rather than from documentation, since
  # a wrong redirect URI fails the login with an opaque provider-side error.
  allowed_redirect_uris = [
    {
      matching_mode     = "strict"
      redirect_uri_type = "authorization"
      url               = "https://litellm.${var.cluster_domain}/sso/callback"
    }
  ]
}

resource "authentik_application" "litellm" {
  name               = "LiteLLM"
  slug               = "litellm"
  protocol_provider  = authentik_provider_oauth2.litellm.id
  meta_launch_url    = "https://litellm.${var.cluster_domain}"
  group              = ""
  open_in_new_tab    = false
  policy_engine_mode = "any"
}
