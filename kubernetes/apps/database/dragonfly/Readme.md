# dragonfly

Dragonfly DB operator and per-app cache instances. There is **no centrally-deployed Dragonfly here** — only the operator. Each consuming app provisions its own dedicated `<app>-dragonfly` cluster via the shared component at `kubernetes/components/dragonfly/`.

## What lives here

| Subdir | Purpose |
| ------ | ------- |
| `operator/` | Dragonfly operator HelmRelease (chart `v1.5.0`, OCI). Manages the `Dragonfly` CRD, master/replica failover, per-cluster `PodDisruptionBudget` (`maxUnavailable: 1`), and per-cluster `NetworkPolicy` (peer-pods-only on `:9999`). Bundles the operator Grafana dashboard into the "Database" folder. |

The `cluster/` subdir was deleted as dead code (was commented out in `ks.yaml` for months and duplicated the real per-app component). All actual cluster config now lives in `kubernetes/components/dragonfly/`.

## Per-app instances

Each consuming app references the component from its `ks.yaml` (`components: - ../../../../components/dragonfly`), which provisions in the app's own namespace:

- `<app>-dragonfly` — the `Dragonfly` CR (2 replicas, `--cluster_mode=emulated`, cache-only).
- `<app>-dragonfly` — the `PodMonitor` (scrapes admin port `:9999`).
- `<app>-dragonfly-allow-prometheus` — additive `NetworkPolicy` so Prometheus can reach `:9999` (the operator's default NP blocks it).
- `<app>-dragonfly-gatus-ep` — `ConfigMap` that registers a Gatus TCP probe on `:6379`.

Current consumers and their endpoints:

| App | Namespace | Endpoint |
| --- | --------- | -------- |
| `authentik` | security | `authentik-dragonfly.security.svc.cluster.local:6379` |
| `immich` | media | `immich-dragonfly.media.svc.cluster.local:6379` |
| `litellm` | ai | `litellm-dragonfly.ai.svc.cluster.local:6379` |
| `open-webui` | ai | `open-webui-dragonfly.ai.svc.cluster.local:6379` |
| `paperless-ngx` | selfhosted | `paperless-ngx-dragonfly.selfhosted.svc.cluster.local:6379` |
| `rsshub` | selfhosted | `rsshub-dragonfly.selfhosted.svc.cluster.local:6379` |
| `searxng` | ai | `searxng-dragonfly.ai.svc.cluster.local:6379` |

## Cluster overview (per instance, set in the component)

- Image: `ghcr.io/dragonflydb/dragonfly:v1.38.0` — pinned in `components/dragonfly/cluster.yaml`, tracked by Renovate (`# renovate: datasource=docker depName=ghcr.io/dragonflydb/dragonfly`).
- 2 replicas with `topologySpreadConstraints` enforcing `maxSkew=1` across `kubernetes.io/hostname` (each cluster spans 2 of talos-1/2/3).
- Resources: `requests` and `limits` both at 250m CPU + 640Mi memory. The CR-level config qualifies for Guaranteed QoS, but the cluster-wide `k8tz` mutating webhook injects an empty-resources init container that drags every pod down to **Burstable** (see Operational notes).
- Args: `--maxmemory=512Mi`, `--proactor_threads=2`, `--cluster_mode=emulated`, `--cache_mode=true`, `--default_lua_flags=allow-undeclared-keys`.
- Service routes `:6379` to the pod labeled `role=master`. The operator handles failover automatically; consumers see a stable hostname.

## Common operations

### Connect from a debug pod

```sh
kubectl run redis-cli --rm -it --image=redis:7 -- \
  redis-cli -h <app>-dragonfly.<ns>.svc.cluster.local -p 6379
```

This only works from a pod inside `<ns>` — the operator's NetworkPolicy blocks cross-namespace ingress on `:6379`. There is no password set; isolation comes from the NetworkPolicy plus per-namespace placement.

### Force a rolling restart

The operator owns the StatefulSet, so don't run `kubectl rollout restart` directly. Either delete one pod at a time and let the operator re-create it (the PDB ensures the master keeps serving), or modify a field in the Dragonfly CR to trigger a reconcile.

### Verify Prometheus is scraping

```sh
PROM=http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090
kubectl exec -n monitoring deploy/grafana -c grafana -- sh -c \
  "wget -qO- '$PROM/api/v1/query?query=up%7Bjob%3D~%22.%2Adragonfly.%2A%22%7D'" \
  | jq '.data.result[] | {job: .metric.job, value: .value[1]}'
```

Expect 14 results (7 clusters × 2 replicas), all `value=1`. If any are `0`, check the additive `*-allow-prometheus` NetworkPolicy actually applied in that namespace.

### Check master/replica role

```sh
kubectl get pods -A -l app.kubernetes.io/part-of=dragonfly \
  -o custom-columns=NS:.metadata.namespace,POD:.metadata.name,ROLE:.metadata.labels.role,NODE:.spec.nodeName
```

Expect exactly one `master` per `<app>-dragonfly` cluster, the rest `replica`.

## Operational notes

- **Cache-only policy.** Every cluster runs with `--cache_mode=true`; LRU eviction is the consistency model. Snapshot/RDB backup is **intentionally** disabled — consumers must tolerate full data loss on pod restart. Authentik's session cache, Immich's job state, etc. all reset on rollout.
- **No password auth.** Mitigated by two NetworkPolicies in front of every pod: the operator-installed one (peer-pods + operator controller-manager only on `:9999`, and any pod in the same namespace on `:6379`) plus the additive `<app>-dragonfly-allow-prometheus` NP from the component.
- **QoS lands as Burstable, not Guaranteed.** The CR-level `requests` and `limits` match exactly, but the cluster-wide `k8tz` mutating webhook injects an init container with empty resources into nearly every pod (only 1 pod cluster-wide is currently Guaranteed). Fixing this is a `k8tz` config change, not a Dragonfly one.
- **Dragonfly v1.38.0 memory floor.** `--maxmemory` must be ≥ `256MiB × proactor_threads`. With our `--proactor_threads=2`, the floor is 512MiB. Don't drop `limits.memory` below 640Mi without first lowering `proactor_threads`, or pods crash-loop on startup with `"There are 2 threads, so 512.00MiB are required. Exiting..."`.
- **Operator's NetworkPolicy is restrictive on `:9999`.** It only allows the operator controller-manager and peer Dragonfly replicas. Prometheus needs the additive `<app>-dragonfly-allow-prometheus` NP from `components/dragonfly/networkpolicy.yaml` — without it, all 14 scrape targets land in `health=down` (`context deadline exceeded`) and the Grafana dashboard is empty.
- **Bundled operator Grafana dashboard is in the "Database" folder.** The grafana helm values used to also pull four `dragonfly-*` dashboards (gnetIds 15944/15945/21053/21054) — those are for **D7Y / Dragonfly P2P file distribution**, an unrelated CNCF project sharing the name. They have been removed from `apps/monitoring/grafana/app/helmrelease.yaml`; don't add them back.
- **Renovate tracks both the operator chart and the Dragonfly image** automatically (the chart via Flux's OCIRepository manager, the image via the renovate annotation comment in `components/dragonfly/cluster.yaml`). Bumps appear in the Dependency Dashboard issue.
