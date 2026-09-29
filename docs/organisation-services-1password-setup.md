# New 1Password item

Skill `secrets-1password`
([recipes.md](../.agents/skills/secrets-1password/references/recipes.md)).

- ESO reads `Homelab`, `Automation` and `Services`. `Home-Lab` is a different
  vault and Connect cannot see it.
- Postgres host is not a field. ExternalSecrets hardcode
  `postgres-17-rw.database.svc.cluster.local`. Do not add `POSTGRES_DB_HOST`.
- `POSTGRES_SUPER_PASS` stays on the shared `cloudnative-pg` item. The app
  item holds `POSTGRES_DB_NAME`, `POSTGRES_DB_USER_NAME`,
  `POSTGRES_DB_USER_PASSWORD`, and the app's own secrets.
- Generate values with `openssl rand`. Store them in 1Password. Let the
  ExternalSecret project them.
- Never commit a secret, and never seed one with client-side `kubectl apply`
  (the value is copied into `last-applied-configuration`).

The field list for an existing app is its `externalsecret.yaml`.
