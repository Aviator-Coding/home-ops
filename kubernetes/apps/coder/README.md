# Coder

Workspace platform. Sign in with Authentik; do not create a local password user.

Password auth is disabled (`CODER_DISABLE_PASSWORD_AUTH=true`). Authentik group
`Admins` is mapped to Coder role `owner` via `CODER_OIDC_USER_ROLE_MAPPING`.
There is no first-boot admin-user form to fill, and you should not `UPDATE`
`users.rbac_roles` in Postgres to grant admin.

If a first-connect setup wizard still appears, it is leftover Coder chart UI.
Skip it and use **Sign in with Authentik**. Membership of Authentik `Admins` is
what grants `owner`.

OIDC issuer: `https://auth.${SECRET_DOMAIN}/application/o/coder/`
(see `app/helmrelease.yaml`).
