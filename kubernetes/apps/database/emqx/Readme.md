# emqx

EMQX 5.x MQTT broker for the home-ops cluster, deployed via the EMQX operator. Two core nodes, persistent data, exposed at `10.50.0.30` for MQTT and at `mqtt.${SECRET_DOMAIN}` for the dashboard.

## What lives here

| Subdir | Purpose |
| ------ | ------- |
| `operator/` | EMQX operator HelmRelease (chart `emqx-operator` v2.2.29 from the `emqx-charts` HelmRepository). Single replica. Manages the `apps.emqx.io/v2beta1 EMQX` CRD. **Do not bump to operator 2.3.0** — it drops `v2beta1` and only supports EMQX 5.9 / 6.x. Stay on 2.2.29 until you can plan an EMQX major. |
| `cluster/` | The `emqx` EMQX CR plus its supporting resources: `ExternalSecret` (init users + ACL + exporter API key bootstrap), `HTTPRoute` (dashboard at `mqtt.${SECRET_DOMAIN}` → port 18083), `PodMonitor` (native `/api/v5/prometheus/stats`), `NetworkPolicy` (lock dashboard:18083 to monitoring + exporter), `Gatus` probe. |
| `exporter/` | `emqx-exporter` v0.2.11 raw manifests (no upstream Helm chart): `Deployment`, `Service`, `ServiceMonitor`, `ExternalSecret` rendering its config from the same OnePassword item that bootstraps the API key on the cluster side. Required to populate the 7 EMQX Grafana dashboards. |

## Cluster overview

- Image: `public.ecr.aws/emqx/emqx:5.8.9` — current OSS 5.x. Renovate-tracked.
- 2 core nodes (no replicants — fine for ≤7 nodes per EMQX docs; replicants only matter for read-heavy MQTT subscriber scaling).
- Resources: `requests` cpu=250m mem=1Gi, `limits` cpu=1000m mem=1Gi (memory locked at limit since Erlang VM is GC-heavy and OOMKill is catastrophic for Mria gossip).
- Persistence: 5 GiB ceph-block PVC per core node mounted at `/opt/emqx/data` so retained messages, runtime ACLs, dashboard-created rules and built_in_database additions survive pod restarts.
- PDB: `coreTemplate.spec.minAvailable: 1` (operator-managed).
- Authentication: built_in_database with bcrypt; users bootstrapped from `/opt/init-user.json` (default admin + MQTT service account, both in 1Password).
- Authorization: built_in_database + ACL file (`/opt/init-acl`); `no_match: deny` (default-deny).
- API keys: bootstrapped from `/opt/init-api-keys` so the exporter can authenticate without manual setup.

## MQTT clients

- LoadBalancer IP: `10.50.0.30` (Cilium LB-IPAM, requested via `lbipam.cilium.io/ips` annotation).
- Plain MQTT: `tcp://10.50.0.30:1883`
- MQTT over TLS: `tls://10.50.0.30:8883`
- WebSocket: `ws://10.50.0.30:8083`
- WebSocket over TLS: `wss://10.50.0.30:8084`
- Dashboard (UI): `https://mqtt.${SECRET_DOMAIN}` (HTTPRoute on internal gateway only).

Use the `EMQX_MQTT_USERNAME` / `EMQX_MQTT_PASSWORD` from the `EMQX` 1Password item for client connections; the bootstrap ACL grants this user `all` permissions on `#`.

## Common operations

### Log into the dashboard

Use `EMQX_DEFAULT_USERNAME` / `EMQX_DEFAULT_PASSWORD` from 1Password at `https://mqtt.${SECRET_DOMAIN}`.

### List currently connected clients

```sh
kubectl exec -n database emqx-core-0 -- emqx ctl clients list | head -40
```

### Tail core logs

```sh
kubectl logs -n database -l apps.emqx.io/instance=emqx -c emqx --tail=100 -f
```

### Subscribe / publish from a debug pod

```sh
# Subscribe
kubectl run mosq -it --rm --image=eclipse-mosquitto:2 -- \
  mosquitto_sub -h emqx-listeners.database.svc.cluster.local -p 1883 \
  -u "$EMQX_MQTT_USERNAME" -P "$EMQX_MQTT_PASSWORD" -t 'test/#' -v

# Publish (separate terminal)
kubectl run mosq-pub -it --rm --image=eclipse-mosquitto:2 -- \
  mosquitto_pub -h emqx-listeners.database.svc.cluster.local -p 1883 \
  -u "$EMQX_MQTT_USERNAME" -P "$EMQX_MQTT_PASSWORD" -t 'test/hello' -m 'world'
```

### Verify Prometheus targets are healthy

```sh
PROM=http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090
kubectl exec -n monitoring deploy/grafana -c grafana -- sh -c \
  "wget -qO- '$PROM/api/v1/query?query=up%7Bjob%3D~%22.%2Aemqx.%2A%22%7D'" \
  | jq '.data.result[] | {job: .metric.job, value: .value[1]}'
```

Expect three targets all `value=1`: two from the `PodMonitor` (one per core pod, native metrics) and one from the exporter `ServiceMonitor`.

## Operational notes

- **Do not remove `volumeClaimTemplates`** from `cluster/cluster.yaml`. Without it, retained MQTT messages and any runtime user/ACL/rule changes are lost on pod restart. The bootstrap files re-apply the initial users/ACL/api-key but nothing added later survives.
- **Exporter API key bootstrap caveat:** `init-api-keys` is idempotent (EMQX skips the bootstrap entry if a key with the same id already exists), but rotating the key/secret in 1Password will NOT update an existing key — you must delete the API key in the EMQX dashboard first, then let the bootstrap re-create it. For non-rotation use, leave the values stable.
- **`EMQX_API_KEY__BOOTSTRAP_FILE` env override:** The operator injects a default `EMQX_API_KEY__BOOTSTRAP_FILE=/opt/emqx/data/bootstrap_api_key` env var, and EMQX env wins over HOCON. The CR's `coreTemplate.spec.env` overrides this back to `/opt/init-api-keys` so EMQX reads our Secret-mounted bootstrap file. Don't remove that env entry — the HOCON `api_key { bootstrap_file = ... }` block is silently ignored when the env var is present.
- **Exporter probe disabled.** The exporter's `/probe` endpoint is meant to publish/subscribe against MQTT to test connectivity, but it doesn't support `username`/`password` in its config and our `no_match: deny` ACL rejects anonymous connect. Probe is omitted from the exporter config; the `emqx-client-events` Grafana panel that depends on probe metrics will read empty. All other dashboards populate from `/metrics`.
- **Operator chart pin:** `emqx-operator` 2.2.29 is intentional. Operator 2.3.0 drops the `apps.emqx.io/v2beta1` CRD and requires EMQX 5.9 or 6.x; Renovate may flag a bump but it cannot be applied until EMQX itself is moved to 5.9+ in a separate session.
- **QoS lands as Burstable, not Guaranteed,** despite identical requests/limits on the EMQX container. The cluster-wide k8tz mutating webhook injects an init container with empty resources that drags every pod down to Burstable. Memory eviction protection still applies (request==limit) but the formal Guaranteed tier is unreachable until k8tz injects resources on its init container.
- **Dashboards live under the Database Grafana folder** (alongside CNPG and Dragonfly). The 7 EMQX dashboards (`emqx-overview`, `-authentication`, `-authorization`, `-client-events`, `-messages`, `-rule-engine-count`, `-rule-engine-rate`) are loaded directly from raw URLs in `apps/monitoring/grafana/app/helmrelease.yaml` and require the exporter to populate. Without the exporter (or with an exporter that can't authenticate), they all read empty.
- **Renovate coverage** is automatic for three things: the EMQX image (`# renovate:` annotation in `cluster/cluster.yaml`), the operator chart (auto-detected via the `emqx-charts` HelmRepository), and the exporter image (`# renovate:` annotation in `exporter/deployment.yaml`). All three appear in Dependency Dashboard issue #489. Pin upgrades should land via Renovate PRs, not manual bumps.
- **`no_match: deny` on authorization.** Correct secure default. Side effect: only localhost can read `$SYS/#` system topics. Since metrics are scraped via Prometheus rather than `$SYS`, no exposure is needed.
- **Ephemeral MQTT message ordering:** there's no replicant tier, so MQTT subscribers are served by either core node. With 2 cores there's no quorum for split-brain — fine for a homelab broker but a real production cluster would want 3+ cores.
