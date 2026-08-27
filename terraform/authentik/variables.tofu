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

variable "coder_client_id" {
  type        = string
  description = "OAuth2 client_id of the existing Coder provider. Adopted, not generated - changing it breaks Coder logins."
  sensitive   = true
}

variable "open_webui_client_id" {
  type        = string
  description = "OAuth2 client_id of the existing open-webui provider. Adopted, not generated - changing it breaks open-webui logins."
  sensitive   = true
}

variable "pgadmin_client_id" {
  type        = string
  description = "OAuth2 client_id of the existing pgAdmin provider. Adopted, not generated - changing it breaks pgAdmin logins."
  sensitive   = true
}
