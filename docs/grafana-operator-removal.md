# Grafana operator

No grafana-operator workload is deployed. Grafana is the standalone HelmRelease
at `kubernetes/apps/base/monitoring/grafana` (`grafana.enabled: false` on
kube-prometheus-stack). Do not reintroduce `kind: GrafanaDashboard`.

The `grafana.integreatly.org` CRDs are still installed. They were applied by
helmfile, not Flux. Bootstrap is not re-run against a healthy cluster.
Cleanup is a separate, explicitly approved pass. The commands and the
toolhive ClusterRole follow-up are in skill `observability`,
`references/grafana.md`.

Prose mentions in `bootstrap/AGENTS.md` and `docs/reference.md` are intentional.
