# LiteLLM UI single sign-on. CREATED by this stack (no import block), so unlike the adopted
# providers both credential halves are generated here and surfaced via outputs.tofu, never Git.
# Kubernetes side: skill litellm-proxy (references/platform.md).

resource "random_string" "litellm_client_id" {
  length  = 40
  special = false
  # Same shape as Authentik's own client ids.
}

resource "random_password" "litellm_client_secret" {
  length  = 128
  special = false
  # Alphanumeric: it reaches LiteLLM through an environment variable.
}

resource "authentik_provider_oauth2" "litellm" {
  name          = "LiteLLM"
  client_id     = random_string.litellm_client_id.result
  client_secret = random_password.litellm_client_secret.result
  client_type   = "confidential"
  sub_mode      = "hashed_user_id"
  issuer_mode   = "per_provider"
  logout_method = "backchannel"

  # Stock implicit-consent flow; LiteLLM-only invalidation flow (see below).
  authorization_flow = data.authentik_flow.provider_authorization_implicit_consent.id
  # .uuid, NOT .id: authentik_flow's terraform id is the slug, and the API 400s on it.
  invalidation_flow = authentik_flow.litellm_invalidation.uuid

  # MUST be declared on a CREATED provider: omitted, it is empty and Authentik rejects every
  # authorize ("Invalid grant_type for provider"). Imported providers adopt the live set.
  grant_types = ["authorization_code"]

  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id

  # Must match GENERIC_SCOPE on litellmproxy.yaml, or no role claim is emitted and first login
  # lands as INTERNAL_USER_VIEW_ONLY.
  property_mappings = concat(
    data.authentik_property_mapping_provider_scope.oauth2_default.ids,
    [authentik_property_mapping_provider_scope.litellm_role.id],
  )

  access_code_validity    = "minutes=1"
  access_token_validity   = "minutes=5"
  refresh_token_validity  = "days=30"
  refresh_token_threshold = "seconds=0"

  # <PROXY_BASE_URL>/sso/callback as LiteLLM builds it; change both together.
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

# SSO users land as proxy_admin (value must be exactly "proxy_admin"); LiteLLM reads the claim
# via GENERIC_USER_ROLE_ATTRIBUTE.
resource "authentik_property_mapping_provider_scope" "litellm_role" {
  name       = "LiteLLM Role"
  scope_name = "litellm_role"
  expression = <<-EOT
    return {"litellm_role": "proxy_admin"}
  EOT
}

# LiteLLM-only invalidation flow with a User Logout stage, so logout ends the Authentik session.
# Never bind that stage to the shared default flow: it would change logout for every other app.
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
