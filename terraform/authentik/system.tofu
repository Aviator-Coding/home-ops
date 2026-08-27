# System-level objects, all READ-ONLY.
#
# The signing certificate, the default brand, and the Kubernetes service
# connection are Authentik-created (`authentik Self-signed Certificate` is
# generated at first boot; `authentik-default` comes from the `default/default-brand.yaml`
# blueprint). Nothing here is hand-built, so nothing here is owned by this stack.

data "authentik_certificate_key_pair" "self_signed" {
  name = "authentik Self-signed Certificate"

  # Only the certificate's ID is ever used, to point providers at their signing
  # key. Both of these default to TRUE, which makes the data source additionally
  # call view_certificate/ and view_private_key/ and store the PEM and the
  # PRIVATE KEY in state. That is a real leak into an already secret-bearing
  # state file, and view_certificate/ is also denied to the read-only role the
  # plan runs as, so leaving them on breaks `tofu plan` outright.
  fetch_certificate = false
  fetch_key         = false
}

# Referenced so a reviewer can see which brand is live and which flows it points
# at without opening the UI. Branding is deliberately unmanaged: this instance
# runs Authentik's stock brand untouched.
data "authentik_brand" "default" {
  domain = "authentik-default"
}
