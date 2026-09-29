# Adopted providers omit client_secret and grant_types. Declaring client_secret rotates the live secret. Created providers must declare grant_types. Skill `authentik-terraform`.

locals {
  oauth2_applications = {
    coder = {
      application_name   = "Coder"
      provider_name      = "Coder"
      client_id          = var.coder_client_id
      authorization_flow = data.authentik_flow.provider_authorization_explicit_consent.id
      launch_url         = ""
      logout_uri         = null
      redirect_uris = [
        "https://coder.${var.cluster_domain}/api/v2/users/oidc/callback",
      ]
    }


    pg-admin = {
      application_name   = "pgAdmin"
      provider_name      = "pgAdmin"
      client_id          = var.pgadmin_client_id
      authorization_flow = data.authentik_flow.provider_authorization_implicit_consent.id
      launch_url         = ""
      logout_uri         = null
      redirect_uris = [
        "https://pgadmin.${var.cluster_domain}/oauth2/authorize",
        "https://pg.${var.cluster_domain}/oauth2/authorize",
      ]
    }
  }
}

resource "authentik_provider_oauth2" "oauth2" {
  for_each = local.oauth2_applications

  name          = each.value.provider_name
  client_id     = each.value.client_id
  client_type   = "confidential"
  sub_mode      = "hashed_user_id"
  issuer_mode   = "per_provider"
  logout_method = "backchannel"
  logout_uri    = each.value.logout_uri

  authorization_flow = each.value.authorization_flow
  invalidation_flow  = data.authentik_flow.provider_invalidation.id

  include_claims_in_id_token = true
  signing_key                = data.authentik_certificate_key_pair.self_signed.id
  property_mappings          = data.authentik_property_mapping_provider_scope.oauth2_default.ids

  access_code_validity    = "minutes=1"
  access_token_validity   = "minutes=5"
  refresh_token_validity  = "days=30"
  refresh_token_threshold = "seconds=0"

  # `redirect_uri_type` must be declared even though the database rows for these
  # two adopted providers predate the field: the API defaults it to "authorization"
  # and returns it, so omitting it makes every plan propose removing it.
  allowed_redirect_uris = [
    for uri in each.value.redirect_uris : {
      matching_mode     = "strict"
      redirect_uri_type = "authorization"
      url               = uri
    }
  ]
}

resource "authentik_application" "oauth2" {
  for_each = local.oauth2_applications

  name               = each.value.application_name
  slug               = each.key
  protocol_provider  = authentik_provider_oauth2.oauth2[each.key].id
  meta_launch_url    = each.value.launch_url
  group              = ""
  open_in_new_tab    = false
  policy_engine_mode = "any"
}
