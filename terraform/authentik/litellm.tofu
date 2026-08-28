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

  # Implicit consent: this is an interactive UI login for
  # an operator tool, and an explicit consent screen on every sign-in buys
  # nothing here. Authorization reuses the stock implicit-consent flow;
  # invalidation is LiteLLM-only (see litellm_invalidation below) so logout
  # ends the Authentik browser session without changing any other provider.
  authorization_flow = data.authentik_flow.provider_authorization_implicit_consent.id
  # .uuid, NOT .id. authentik_flow's terraform id is the SLUG (the create
  # returned id=litellm-invalidation-flow), while the provider API validates
  # invalidation_flow as a UUID and 400s on anything else. The stage binding
  # below already uses .uuid; this reference did not, and only a live apply
  # surfaced it.
  invalidation_flow = authentik_flow.litellm_invalidation.uuid

  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id

  # openid + email + profile (LiteLLM's default GENERIC_SCOPE) plus the custom
  # litellm_role scope below. The Kubernetes side must request the same set via
  # GENERIC_SCOPE, or Authentik never emits the role claim and first login lands
  # as INTERNAL_USER_VIEW_ONLY.
  property_mappings = concat(
    data.authentik_property_mapping_provider_scope.oauth2_default.ids,
    [authentik_property_mapping_provider_scope.litellm_role.id],
  )

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

# Custom scope claim so SSO users land as proxy_admin without a manual first
# login + PROXY_ADMIN_ID step. Value must be exactly "proxy_admin"
# (LitellmUserRoles.PROXY_ADMIN in the running v1.98.0 image). Mirrored from the
# reference repo's litellm_role mapping shape. LiteLLM reads it via
# GENERIC_USER_ROLE_ATTRIBUTE over the token/userinfo payload.
resource "authentik_property_mapping_provider_scope" "litellm_role" {
  name       = "LiteLLM Role"
  scope_name = "litellm_role"
  expression = <<-EOT
    return {"litellm_role": "proxy_admin"}
  EOT
}

# LiteLLM-only invalidation flow. The shared default-provider-invalidation-flow
# has zero stage bindings, so RP-initiated logout only ends the application
# session and leaves the Authentik browser session alive. With
# AUTO_REDIRECT_UI_LOGIN_TO_SSO that makes logout look like a no-op. Binding a
# User Logout stage onto the shared default would change logout for every
# OIDC/proxy app (coder, pg-admin, echo ExtAuth); this flow is
# scoped to authentik_provider_oauth2.litellm only.
resource "authentik_flow" "litellm_invalidation" {
  name        = "LiteLLM Invalidation"
  title       = "LiteLLM Invalidation"
  slug        = "litellm-invalidation-flow"
  designation = "invalidation"
}

resource "authentik_stage_user_logout" "litellm_logout" {
  name = "LiteLLM Logout"
}

resource "authentik_flow_stage_binding" "litellm_logout" {
  target = authentik_flow.litellm_invalidation.uuid
  stage  = authentik_stage_user_logout.litellm_logout.id
  order  = 0
}
