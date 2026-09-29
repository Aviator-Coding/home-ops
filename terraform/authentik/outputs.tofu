# Generated LiteLLM credentials. Read with tofu output -raw and push with kubectl create, never client-side kubectl apply. Skill `authentik-terraform` references/apply-runbook.md.

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
