variable "authentik_url" {
  type        = string
  description = "Base URL of the Authentik instance this stack manages"
  default     = "https://auth.sklab.dev"
}

variable "authentik_token" {
  type        = string
  description = "Authentik API token used by the provider. Supplied as TF_VAR_authentik_token from 1Password via vals; never set in a committed file."
  sensitive   = true
}

variable "cluster_domain" {
  type        = string
  description = "Public DNS domain the cluster's SSO-gated services are published under"
  default     = "sklab.dev"
}

# The two client_id variables below are deliberately NOT marked `sensitive`.
# An OAuth2 client_id is a public identifier - it travels in browser redirect
# URLs - so there is nothing to protect. Marking it sensitive actively hurt:
# Terraform renders a sensitivity-marked attribute as `~ (sensitive value)` in an
# import plan even when the value is identical, which put a phantom "change" on
# the one attribute whose real change would break every login for that app. They
# still come from 1Password, for consistency and to keep them out of Git.
variable "coder_client_id" {
  type        = string
  description = "OAuth2 client_id of the existing Coder provider. Adopted, not generated - changing it breaks Coder logins."
}


variable "pgadmin_client_id" {
  type        = string
  description = "OAuth2 client_id of the existing pgAdmin provider. Adopted, not generated - changing it breaks pgAdmin logins."
}
