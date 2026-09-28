---
name: observability
description: "Read before adding or editing a PrometheusRule, ServiceMonitor or PodMonitor, an Alertmanager route or receiver, a Gatus endpoint, Grafana values or dashboards, or a probe, and before calling any alert dead. Covers rules that can never fire, sparse vs absent metrics, proving a check fires both ways, severity: info never paging, commonMetadata overwriting selector labels, and emptyDir Grafana."
---

# Observability: alerts, scrape targets, Alertmanager, Gatus, Grafana

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`kubernetes/apps/base/monitoring/alertmanager/app/config/alertmanager.yml`](../../../kubernetes/apps/base/monitoring/alertmanager/app/config/alertmanager.yml) - routing and severities
- [`kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml`](../../../kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml) - hand-written Gatus checks
- [`kubernetes/apps/base/monitoring/grafana-sa-provisioner/README.md`](../../../kubernetes/apps/base/monitoring/grafana-sa-provisioner/README.md) - Grafana service-account self-heal
- [`docs/monitoring/exporter-endpoint-repair-2026-09-20.md`](../../../docs/monitoring/exporter-endpoint-repair-2026-09-20.md) - exporter and probe repair
- [`docs/grafana-operator-removal.md`](../../../docs/grafana-operator-removal.md) - grafana-operator removal

`AGENTS.md` entries (search for the opening words):

- `gatus.io/enabled` ConfigMap-label checks are dead
- `monitoring/grafana` runs `persistence.enabled: false`
- A PrometheusRule can be structurally incapable of firing
- A live PrometheusRule (or PodMonitor) can outlive its git declaration
- A new alert's `severity` label now decides whether it reaches the captain
- Alertmanager is a standalone app-template app
- Before writing a new PrometheusRule for "is this app down"
- A hand-written ServiceMonitor/PodMonitor selecting `app.kubernetes.io/name`

## Related skills

- `app-workloads`
