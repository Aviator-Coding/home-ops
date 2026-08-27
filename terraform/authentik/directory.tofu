# Directory (groups, users) is READ-ONLY here.
#
# This instance has exactly two groups, both Authentik's own: `authentik Admins`
# (superuser, created by `system/bootstrap.yaml`) and `authentik Read-only`
# (created by `default/rbac-role-read-only.yaml`). There are no hand-made groups
# and no policy bindings on any of the four applications - access to every app is
# currently open to any authenticated user (`policy_engine_mode = "any"`, zero
# `authentik_policies_policybinding` rows targeting an application).
#
# That is a deliberate statement of the current state, not a recommendation.
# Introducing group-scoped access is a real authorization change that locks people
# out if it lands wrong, so it belongs in its own reviewed change, not smuggled in
# with the adoption of existing config.

data "authentik_group" "admins" {
  name = "authentik Admins"
}
