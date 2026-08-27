# System-level objects, all READ-ONLY.
#
# The signing certificate, the default brand, and the Kubernetes service
# connection are Authentik-created (`authentik Self-signed Certificate` is
# generated at first boot; `authentik-default` comes from the `default/default-brand.yaml`
# blueprint). Nothing here is hand-built, so nothing here is owned by this stack.

data "authentik_certificate_key_pair" "self_signed" {
  name = "authentik Self-signed Certificate"
}

# Referenced so a reviewer can see which brand is live and which flows it points
# at without opening the UI. Branding is deliberately unmanaged: this instance
# runs Authentik's stock brand untouched.
data "authentik_brand" "default" {
  domain = "authentik-default"
}
