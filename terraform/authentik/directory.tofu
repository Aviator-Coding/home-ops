# Directory (groups, users) is READ-ONLY here.
#
# Blueprint groups referenced below: `authentik Admins` (superuser, from
# `system/bootstrap.yaml`) and `authentik Read-only` (from
# `default/rbac-role-read-only.yaml`). A third, hand-made `tofu Writers` role
# exists out-of-band for approved applies (docs/authentik/terraform.md §5) and is
# deliberately NOT declared here - OpenTofu must not own the credential that can
# rewrite this stack. There are still no policy bindings on any application -
# access stays open to any authenticated user (`policy_engine_mode = "any"`,
# zero `authentik_policies_policybinding` rows targeting an application).
#
# That is a deliberate statement of the current state, not a recommendation.
# Introducing group-scoped access is a real authorization change that locks people
# out if it lands wrong, so it belongs in its own reviewed change, not smuggled in
# with the adoption of existing config.

data "authentik_group" "admins" {
  name = "authentik Admins"
}
