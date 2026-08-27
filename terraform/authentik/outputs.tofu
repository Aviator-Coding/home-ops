# The generated LiteLLM credentials, for the one hop OpenTofu cannot make itself:
# getting them into 1Password so the cluster's ExternalSecret can read them.
#
# Read them with `tofu output -raw <name>` and put them in the source Secret the
# PushSecret syncs; the exact commands are in
# ../../docs/authentik/terraform.md section 8. Do not paste them anywhere else -
# `tofu output` without -raw redacts the secret, and that is the desired default.

output "litellm_client_id" {
  description = "OAuth2 client_id generated for the LiteLLM provider"
  value       = random_string.litellm_client_id.result
}

output "litellm_client_secret" {
  description = "OAuth2 client_secret generated for the LiteLLM provider"
  value       = random_password.litellm_client_secret.result
  sensitive   = true
}

output "litellm_oidc_endpoints" {
  description = "Authentik OIDC endpoints LiteLLM's generic SSO needs"
  value = {
    authorization = "https://auth.${var.cluster_domain}/application/o/authorize/"
    token         = "https://auth.${var.cluster_domain}/application/o/token/"
    userinfo      = "https://auth.${var.cluster_domain}/application/o/userinfo/"
  }
}
