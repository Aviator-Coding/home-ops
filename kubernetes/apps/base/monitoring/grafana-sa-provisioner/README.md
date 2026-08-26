# Grafana service account provisioner

Self-healing reconciler for the Grafana Viewer-scoped service account +
token that `ai/grafana-mcp` (ToolHive `MCPServer`) authenticates with.
Captain option (c), 2026-08-26.

## Why this exists

`monitoring/grafana` runs `persistence.enabled: false` (`emptyDir` SQLite) -
every pod restart wipes the database, taking any admin-created service
account and token with it. Nothing recreates them: the previous
`grafana-mcp` deployment referenced a 1Password token (item `grafana-mcp`,
field `GRAFANA_SERVICE_ACCOUNT_TOKEN`) that had gone dead the same way, with
no reconciliation path back to a live one.

A CronJob (`app/helmrelease.yaml`, `*/5 * * * *`) closes that loop:

1. **Probe** the last token it minted (stored in its own Secret
   `grafana-sa-provisioner-token`) with one `GET /api/org`. Valid -> exit,
   no writes. This is the steady-state path and is deliberately as cheap as
   a health check.
2. **Invalid or absent** -> authenticate with the env-provisioned admin
   credentials (`grafana-admin-secret`, mounted directly as
   `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD` - no new secret, no K8s API
   read needed for these), find-or-create a Viewer-scoped service account
   (captain decision **A6**: Viewer only, never Editor/Admin), revoke any
   tokens it already holds, and mint one fresh token.
3. **Push** the new token into its own Secret, which a `PushSecret`
   (`app/pushsecret.yaml`) syncs to the existing 1Password item/field
   `grafana-mcp` / `GRAFANA_SERVICE_ACCOUNT_TOKEN` - the exact one
   `ai/toolhive/mcp-servers/grafana-mcp`'s `ExternalSecret` already reads.
   Pattern mirrored from `ai/litellm`'s `provision_keys.py` +
   `pushsecret.yaml` (own-Secret -> `PushSecret` -> existing 1Password item).

`grafana-mcp`'s `ExternalSecret` was widened to `refreshInterval: 5m` (was
defaulting to 1h) so a freshly pushed token actually reaches the consumer
within one reconcile cadence instead of waiting up to an hour behind it.

## The chicken-and-egg case: admin credential drift

`GF_SECURITY_ADMIN_USER`/`PASSWORD` are only applied by Grafana when it
bootstraps a **fresh, empty** SQLite database at pod startup - confirmed
live against the running pod's boot log (`GF_SECURITY_ADMIN_PASSWORD`
overridden from the environment at startup, matching `grafana-admin-secret`
at that moment). Grafana never re-reads those env vars afterward. So if the
live admin password is ever changed through the API/UI after boot (as
happened here, verified live: with zero pod restarts the running admin
password no longer matches `grafana-admin-secret`, both 401), the two stay
out of sync until the **next pod restart** wipes the database and
re-bootstraps from whatever `grafana-admin-secret` currently holds.

This reconciler cannot fix that on its own - it authenticates *as* admin, it
doesn't reset admin. If admin auth 401s, the Job fails loudly (see
`resources/reconcile.py`) and keeps retrying every 5 minutes; the
`GrafanaSAProvisionerFailing` alert (`app/prometheusrule.yaml`) pages after
an hour of that. **The fix is a Grafana pod restart** (not a config change
here) - after that, first-run provisioning is automatic.

## RBAC

The reconciler's ServiceAccount can `create` any Secret in `monitoring` (K8s
RBAC can't scope `create` by resource name) but `get`/`patch` only the one
Secret it owns (`grafana-sa-provisioner-token`). It never touches
`grafana-admin-secret` via the K8s API - that's mounted directly as
container env, so no RBAC is needed to read it.
