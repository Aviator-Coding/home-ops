# Grafana service account provisioner

`monitoring/grafana` uses `emptyDir` SQLite (`persistence.enabled: false`).
A pod restart wipes admin-created service accounts and tokens.
`GF_SECURITY_ADMIN_USER` / `PASSWORD` apply only when Grafana bootstraps a
fresh database. A UI password change stays drifted until that restart.
Skill `observability`.

This CronJob (`*/5 * * * *`) heals only the Viewer token `ai/grafana-mcp`
uses:

1. Probe the last token in Secret `grafana-sa-provisioner-token` with
   `GET /api/org`. Valid means exit.
2. Otherwise log in with the admin env from `grafana-admin-secret` (form
   login, not HTTP Basic: `GF_AUTH_BASIC_ENABLED` is false). Find-or-create
   a Viewer service account, revoke its old tokens, mint one.
3. Write that token to the Secret. The PushSecret copies it to 1Password
   item `grafana-mcp`, field `GRAFANA_SERVICE_ACCOUNT_TOKEN`.

The consumer ExternalSecret refreshes on 5 minutes. Reloader rolls
grafana-mcp when Secret `toolhive-grafana` changes. This job cannot repair
admin drift. If admin login 401s, the job fails and
`GrafanaSAProvisionerFailing` pages. The fix is a Grafana pod restart.

The ServiceAccount may `create` any Secret in `monitoring` and may
`get`/`patch` only `grafana-sa-provisioner-token`. It does not read
`grafana-admin-secret` through the API. That secret is mounted as env.
