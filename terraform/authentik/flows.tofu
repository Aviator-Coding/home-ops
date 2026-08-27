# Flows are READ-ONLY here, on purpose.
#
# Every one of the 14 flows on this instance is created and reconciled by one of
# Authentik's own built-in blueprints (`authentik_blueprints_blueprintinstance`,
# e.g. `default/flow-default-provider-authorization-implicit-consent.yaml`). None
# was hand-built. Declaring them as `resource` blocks would put OpenTofu and
# Authentik's blueprint reconciler in a fight over the same objects, which is the
# same class of failure this repo already documents for Flux-vs-controller drift.
#
# So: reference them, never own them. If a genuinely custom flow is ever needed,
# add it as a new `resource` alongside these - do not convert one of these.

data "authentik_flow" "provider_authorization_implicit_consent" {
  slug = "default-provider-authorization-implicit-consent"
}

data "authentik_flow" "provider_authorization_explicit_consent" {
  slug = "default-provider-authorization-explicit-consent"
}

data "authentik_flow" "provider_invalidation" {
  slug = "default-provider-invalidation-flow"
}
