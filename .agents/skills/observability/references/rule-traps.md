# PrometheusRule traps

`flate`, `promtool` and CI validate syntax. A rule whose metric is absent or
whose threshold can never be held still appears in the rule list. Before
merging, evaluate the expression against live Prometheus both ways: it is quiet
now, and inverting the comparison returns a series.

Global scrape interval is 1 minute.

## The traps

1. **`container_spec_*` is not in this Prometheus.** cAdvisor is scraped, that
   family is absent. Use `kube_pod_container_resource_limits` (or `_requests`
   when the container declares no limit) with an explicit
   `on (namespace, pod, container)` join, and only divide by a limit the
   container actually has.
2. **`rate(...[1m])` returns no series** at a 1m scrape. `[2m]` is two samples
   that one missed scrape empties. `[3m]` is the practical floor.
3. **`for:` longer than the event never fires.** Measure the contiguous run
   before choosing `for:`. A short burst that looks like it needs a long `for:`
   is discarded.
4. **A counter compared directly latches forever** after the threshold is
   crossed once (`ceph_trackedop_slow_ops_count > 5`). Wrap it in `increase()`.
5. **A crash loop resets every `for:` clock.** cAdvisor series go absent
   between containers, so a threshold cannot accumulate `for:` time across
   restarts. A crash-loop shape needs
   `kube_pod_container_status_last_terminated_reason` at `for: 0m`, AND-ed with
   `increase(...restarts_total[15m]) > 0` or it latches after one kill. A
   working-set threshold covers only the slow climb. Worked pair:
   `kubernetes/apps/base/database/falkordb/app/prometheusrule.yaml`.
6. **A threshold inside the noise floor is permanently true.** Where the metric
   sits elevated on purpose, alert on trajectory:
   `predict_linear(<metric>[6h], <horizon>)` joined to the same limit, with
   `for:` sized from a replay. `predict_linear` alone is not enough on a series
   that resets at pod restart: a post-boot ramp can project a crossing from a
   harmless base. AND the predicted crossing with an already-elevated current
   ratio, between the ramp peak and the steady-state floor. Replay both a
   sustained climb (must fire) and history plus a post-restart ramp (must not).
   Worked example: `kubernetes/apps/base/ai/vllm/app/prometheusrule.yaml`.
7. **Absence of a sparse series is the healthy state.** An inventory audit
   ("is the name in `__name__`?") flags these as dead. OpenTelemetry gauges
   that observe nothing while healthy, and prometheus_client counters that emit
   nothing until `.labels()` is first called, are sparse. Rewiring them onto an
   always-present neighbour changes what pages. Two tells that a series is
   real and merely sparse: the exporter publishes the rest of its registry, or
   a sibling from the same constructor has data over the same window. A live
   exporter that publishes nothing is genuinely dead. Chart-owned rules
   (kopiur-controller, authentik) cannot be edited per alert.

## Recency and orphans

A comparison against a kube-state-metrics gauge that cannot clear becomes a
latched page. Bound it with
`unless on (...) (time() - <start_time> >= N)`, written as `unless` so a
missing timestamp fails open. Worked examples:
`kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts/kubernetes-apps-recency.yaml`.

A live PrometheusRule or PodMonitor can outlive Git. `database/tikv-rules`
(`TiKVMemoryHighUsage`, dead `container_spec_memory_limit_bytes`) and the
`database/tikv-pd` / `database/tikv-tikv` PodMonitors (label
`kustomize.toolkit.fluxcd.io/name: tikv-cluster`, a Kustomization that is gone)
are unmanaged. Do not delete them as cleanup.

## Stock "app down" rules

`KubePodNotReady` (`for: 15m`, `severity: warning`), `KubePodCrashLooping` and
`OOMKilled` match every namespace. A probe-less app never generates the signal
they key on. Add probes before adding a duplicate rule. The probe path must be
able to return non-200. See [gatus.md](gatus.md).

## Rules that look dead and are not

`CoderdWorkspaceBuildFailures` stays on `coderd_workspace_builds_total`. That
series is a labeled counter from provisionerd `NewMetrics()`, registered on
the scraped registry unconditionally. It emits nothing until a build
completes. The unlabeled sibling `coderd_provisionerd_num_daemons` having
data is the proof the constructor ran. Do not rewire the rule because an
inventory query shows no series.

`KubeContainerWaiting` in `kubernetes-apps-recency.yaml` excludes terminal
Failed and Succeeded pods, because kube-state-metrics keeps their last
waiting reason. A container that is actually waiting on a live pod still
fires. A pile of terminal Failed pods is not this alert. It needs its own
rule if it should page.

`node-memory-pressure.yaml` has two tiers, both PSI, not `MemAvailable`.
Talos triggers while available memory is still high. The warning tier is
`for: 10m`. The acute tier is `node_pressure_memory_stalled` (PSI full) with
a `[3m]` rate, because `[1m]` returns no series. Do not lengthen either
`for` without remeasuring the contiguous burst: 15m on the warning tier and
5m on the acute tier each missed a real burst. The Talos trigger threshold
lives in `talos/machineconfig.yaml.j2`.

A third rule in that file fires on exit code 137 AND `reason="Error"`
together. That pair is the Talos OOMController. 137 alone is also a cgroup
OOMKilled, which the stock rule already owns. `reason="Error"` alone is an
ordinary non-zero exit. `last_terminated_exitcode` is the exit code itself,
so do not wrap it in `min_over_time`. The reason series is a 0/1 flag and
may use `min_over_time`. AND a restart increase so the gauge clears. Talos
`OOMActions` is not a Prometheus metric.

`node-network.yaml` is the packet-drop alert. The rook chart's drop and
error rules use `rate(...[1m])` and cannot fire, and that chart has no
per-rule disable. Do not edit them in place. Transmit and receive errors
are already `NodeNetworkReceiveErrs` and `NodeNetworkTransmitErrs`. The
drop ratio needs an absolute `>= 10` packets/s or an idle interface divides
to +Inf. There is no `for:`.

Loki's recording rules and the alerts built on them use `[5m]`. A `[1m]`
window returns nothing at this scrape interval. Series from
`job="promtail-metrics"` are the Promtail client, not the Loki server.
`LokiRequestPanics` already uses `increase(...[10m])`. The server series
exist because `monitoring.serviceMonitor.enabled` is true on the Loki
HelmRelease.

`KubeAggregatedAPIErrors` in `apiserver-aggregation.yaml` is the chart rule
with the window widened to `[5m]`. The chart copy stays disabled in
`defaultRules.disabled`.

## Worked rule files

- `kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts/node-memory-pressure.yaml`
- `kubernetes/apps/base/database/surrealdb/app/prometheusrule.yaml`
- `kubernetes/apps/base/database/falkordb/app/prometheusrule.yaml`
- `kubernetes/apps/base/flux-system/flux-instance/app/prometheusrule.yaml`
  (`flux_resource_info` uses `exported_namespace`, not `namespace`)
