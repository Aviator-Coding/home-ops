# Adoption of the existing live objects.
#
# This stack manages configuration that already exists and is in use. Every
# resource it declares is therefore paired with an `import` block, so the first
# `tofu plan` reconciles against the real object instead of proposing to create a
# second one. Without these, the first plan would show 9 creates - and applying
# that would mint new client secrets and a second forward-auth provider, breaking
# every SSO login on the cluster.
#
# The IDs below were read out of the live instance (Authentik's Postgres) during
# the inventory; the format each resource expects comes from its passthrough
# importer:
#
#   authentik_application                    -> slug
#   authentik_provider_oauth2                -> numeric provider pk
#   authentik_provider_proxy                 -> numeric provider pk
#   authentik_outpost_provider_attachment    -> "<outpost uuid>:<provider pk>"
#
# These blocks are safe to leave in place permanently: OpenTofu ignores an import
# block whose target is already in state. Keeping them makes the stack
# reproducible if the state file is ever lost - which, given the state lives in a
# single RGW bucket, is a scenario worth staying one command away from.

import {
  to = authentik_provider_oauth2.oauth2["coder"]
  id = "4"
}


import {
  to = authentik_provider_oauth2.oauth2["pg-admin"]
  id = "2"
}

import {
  to = authentik_application.oauth2["coder"]
  id = "coder"
}


import {
  to = authentik_application.oauth2["pg-admin"]
  id = "pg-admin"
}

import {
  to = authentik_provider_proxy.forward_auth
  id = "37"
}

import {
  to = authentik_application.echo
  id = "echo"
}

import {
  to = authentik_outpost_provider_attachment.forward_auth
  id = "a827266f-21ed-4a8b-a080-7b59a75a042e:37"
}
