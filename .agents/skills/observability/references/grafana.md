# Grafana persistence and the removed operator

`kubernetes/apps/base/monitoring/grafana` sets `persistence.enabled: false`.
The database is `emptyDir` SQLite. Anything created in the UI after boot is
wiped on the next pod restart:

- Admin user and password changes. `GF_SECURITY_ADMIN_USER` /
  `GF_SECURITY_ADMIN_PASSWORD` from `grafana-admin-secret` apply only when
  Grafana bootstraps a fresh database. Clearing live admin drift is a pod
  restart.
- Service accounts and tokens created by hand.

`kubernetes/apps/base/monitoring/grafana-sa-provisioner` recreates the Viewer
service account and token that `ai/grafana-mcp` uses, and pushes it to
1Password item `grafana-mcp` / `GRAFANA_SERVICE_ACCOUNT_TOKEN`. It does not
repair admin-credential drift. Details: that app's `README.md`.

Dashboard JSON that contains a Grafana variable `${...}` is a Flux
substitution collision. Escape it or disable substitution on that
Kustomization (skill `flux-substitution`). Neither `flate` nor
`task flux:test:all` catches an undefined token.

Do not reintroduce `kind: GrafanaDashboard` or a grafana-operator HelmRelease.
Dashboards that Grafana actually loads are sidecar ConfigMaps (label
`grafana_dashboard: "1"`) or `url:` entries on the Grafana HelmRelease.

## CRDs still installed

The operator workload is gone. The `grafana.integreatly.org` CRDs are still
in the cluster (alertrulegroups, contactpoints, dashboards, datasources,
folders, librarypanels, mutetimings, notificationpolicies,
notificationpolicyroutes, notificationtemplates). They were applied by
helmfile, not Flux, so deleting the bootstrap pin did not remove them.
Bootstrap is not re-run against a healthy cluster.

Cleanup is a separate, explicitly approved pass. Do not run it as part of a
docs change:

```bash
kubectl get grafanadashboards -A
kubectl get crd -o name | grep grafana.integreatly.org
```

Delete the CRDs only after no CRs of those kinds remain.
`kubernetes/apps/base/ai/toolhive` still lists `grafana.integreatly.org` in a
ClusterRole. Drop that entry in the same approved pass, not before the CRDs
are gone. That RBAC file is the AI stack's, not this skill's edit.
