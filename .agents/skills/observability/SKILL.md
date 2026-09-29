---
name: observability
description: "Read before adding or editing a PrometheusRule, ServiceMonitor or PodMonitor, an Alertmanager route or receiver, a Gatus endpoint, Grafana values or dashboards, or a probe, and before calling any alert dead. Covers rules that can never fire, sparse vs absent metrics, proving a check fires both ways, severity: info never paging, commonMetadata overwriting selector labels, and emptyDir Grafana."
---

# Observability: alerts, scrape targets, Alertmanager, Gatus, Grafana

Rules, probes and Gatus checks can look correct and never fire. `severity: info`
does not page. Prove a check both ways before trusting it.

## Tripwires

1. **Syntax gates do not prove a rule can fire.** `flate`, `promtool` and CI
   check expression syntax, not that the metric exists or the threshold is
   reachable. Evaluate against live Prometheus both ways: quiet now, and a
   series when the comparison is inverted. [rule-traps.md](references/rule-traps.md)
2. **A missing series is not a dead rule.** Sparse gauges and counters exist
   only while the condition is happening. Read the exporter HELP or source
   before calling a rule dead. [rule-traps.md](references/rule-traps.md)
3. **`severity: info` does not page.** `critical` is Pushover priority 2,
   `warning` is 0, `info`/`notify` is -2, and `InfoInhibitor` suppresses info
   when the namespace has no warning or critical. An alert with no severity
   label falls through to warning on purpose. A latched critical needs a
   recency bound. [alertmanager.md](references/alertmanager.md)
4. **Chart-shipped rules are all-or-nothing.** A vendor PrometheusRule cannot
   be patched per alert from Git. Document an inert rule at the HelmRelease.
5. **`commonMetadata.labels` overwrites `app.kubernetes.io/name` on raw
   manifests** at Flux reconcile. A ServiceMonitor selecting any other name
   matches zero targets, and `kustomize build` / `flate` cannot see it.
   `port:` on a PodMonitor is a container-port name, not `targetPort`.
   [scrape-targets.md](references/scrape-targets.md)
6. **Gatus is annotation-only plus hand-written endpoints.**
   `gatus.io/enabled` ConfigMaps are not loaded. A check that hits an
   ext_authz HTTPRoute can stay green while the app is down.
   [gatus.md](references/gatus.md)
7. **Grafana persistence is `emptyDir`.** Admin password changes and
   hand-made tokens die on pod restart. The SA provisioner heals only the
   grafana-mcp Viewer token. [grafana.md](references/grafana.md)
8. **A probe only helps if the path can return non-200.** n8n `/healthz` is
   unconditional `ok`. Prefer the stock `KubePodNotReady` /
   `KubePodCrashLooping` / `OOMKilled` rules over a bespoke "app down" rule
   once real probes exist. [gatus.md](references/gatus.md)
9. **Do not delete an orphan PrometheusRule or PodMonitor** that outlived its
   Git declaration. `database/tikv-rules`, `database/tikv-pd` and
   `database/tikv-tikv` are that class. Deletion is a destructive action.

## Where things live

| What | Path |
|---|---|
| Alertmanager config and scrape | `kubernetes/apps/base/monitoring/alertmanager/` |
| Gatus hand-written checks | `kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml` |
| kube-prometheus-stack rules | `kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts/` |
| Grafana chart (`persistence.enabled: false`) | `kubernetes/apps/base/monitoring/grafana/app/helmrelease.yaml` |
| Grafana Viewer token self-heal | `kubernetes/apps/base/monitoring/grafana-sa-provisioner/` |
| Shared-downloads capacity page | `kubernetes/apps/base/downloads/maintenance/app/prometheusrule.yaml` |
| Coder alert runbooks (GitHub anchors, ConfigMap) | `kubernetes/apps/base/coder/app/runbooks/` |
| Human SABnzbd disk runbook | `.agents/skills/media-stack/references/sabnzbd-disk-space.md` |

## Procedures

- Write or audit a PrometheusRule: [rule-traps.md](references/rule-traps.md).
- Change a route, receiver, or the Watchdog ping: [alertmanager.md](references/alertmanager.md).
- Add a Gatus check or a probe: [gatus.md](references/gatus.md).
- Add a ServiceMonitor or PodMonitor: [scrape-targets.md](references/scrape-targets.md).
- Grafana persistence, tokens, leftover operator CRDs: [grafana.md](references/grafana.md).

## Verify

- `python3 scripts/ci/docs-budget-test.py` and `python3 scripts/ci/doc-links-test.py`.
- Before merging a rule, run the expression and its inverse against Prometheus
  (`monitoring/kube-prometheus-stack-prometheus:9090`). A stale `Ready=False`
  on a controller is backoff, not proof the rule is wrong (skill `flux-gitops`).
- Live checks are read-only. Do not restart Alertmanager or Grafana to "see
  if it works".
