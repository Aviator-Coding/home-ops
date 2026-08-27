# Provider wiring for the live Authentik instance.
#
# Credentials never live in this tree. `authentik_token` is supplied through the
# environment as TF_VAR_authentik_token, rendered from 1Password by `vals` - see
# secrets.vals.yaml and the runbook in ../../docs/authentik/terraform.md.

terraform {
  required_version = "~> 1.12"

  required_providers {
    authentik = {
      source = "goauthentik/authentik"
      # Track the running Authentik server's release line. The server is pinned
      # in kubernetes/apps/base/security/authentik/app/helmrelease.yaml; keep the
      # major.minor here aligned with it before raising this.
      version = "~> 2026.5.1"
    }
  }
}

provider "authentik" {
  url   = var.authentik_url
  token = var.authentik_token
}
