# renovate-operator UI single sign-on. CREATED by this stack (no import block). Confidential client:
# client_id is a plain literal the HelmRelease repeats; the secret and the UI session key are
# generated here and copied once into 1Password Homelab/renovate-operator-oidc (never Git).
# Kubernetes side: kubernetes/apps/base/renovate/renovate-operator/app (skill `renovate`).

locals {
  # Public identifier. Must match auth.oidc.clientId in the renovate-operator HelmRelease.
  renovate_operator_client_id = "renovate-operator"
}

resource "random_password" "renovate_operator_client_secret" {
  length  = 64
  special = false
}

# Static AES key for the operator's encrypted session cookie, so logins survive pod restarts.
resource "random_password" "renovate_operator_session_secret" {
  length  = 64
  special = false
}

resource "authentik_provider_oauth2" "renovate_operator" {
  name          = "Renovate Operator"
  client_id     = local.renovate_operator_client_id
  client_secret = random_password.renovate_operator_client_secret.result
  client_type   = "confidential"
  sub_mode      = "hashed_user_id"
  # Issuer https://auth.<domain>/application/o/renovate-operator/ (the application slug).
  issuer_mode   = "per_provider"
  logout_method = "backchannel"

  authorization_flow = data.authentik_flow.provider_authorization_implicit_consent.id
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  # MUST be declared on a CREATED provider, see litellm.tofu.
  grant_types = ["authorization_code"]

  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id
  # openid, email, profile: the scopes the operator requests.
  property_mappings = data.authentik_property_mapping_provider_scope.oauth2_default.ids

  access_code_validity    = "minutes=1"
  access_token_validity   = "minutes=5"
  refresh_token_validity  = "days=30"
  refresh_token_threshold = "seconds=0"

  # Path fixed by the operator: <route host>/auth/callback.
  allowed_redirect_uris = [
    {
      matching_mode     = "strict"
      redirect_uri_type = "authorization"
      url               = "https://renovate.${var.cluster_domain}/auth/callback"
    }
  ]
}

resource "authentik_application" "renovate_operator" {
  name               = "Renovate"
  slug               = "renovate-operator"
  protocol_provider  = authentik_provider_oauth2.renovate_operator.id
  meta_launch_url    = "https://renovate.${var.cluster_domain}"
  group              = ""
  open_in_new_tab    = false
  policy_engine_mode = "any"
}
