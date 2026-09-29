# EMQX

## Operator pin

Chart `emqx-operator` stays on the 2.2.x line (the tag is in
`operator/helmrelease.yaml`). 2.3.x drops `apps.emqx.io/v2beta1` and calls
the Enterprise-only `load_rebalance` API. Against this OSS broker that call
404s and the readiness gate never sets (emqx/emqx-operator#1202). Do not
take a Renovate bump to 2.3.x until the broker image is on a release that
operator supports, in the same change.

The broker image pin is `cluster/cluster.yaml` (`public.ecr.aws/emqx/emqx`,
Renovate-tracked). Two core nodes, no replicants. `coreTemplate` allows one
unavailable. Memory request equals the memory limit. Persistence is a
ceph-block PVC per core at `/opt/emqx/data`. Do not remove
`volumeClaimTemplates`: retained messages and runtime ACL or rule changes
die on restart without it. Bootstrap files reapply only the initial users.

## Clients and auth

LoadBalancer `10.50.0.30` (Cilium LB-IPAM). Plain MQTT 1883, TLS 8883,
WebSocket 8083, WSS 8084. Dashboard HTTPRoute is internal only,
`mqtt.${SECRET_DOMAIN}` port 18083.

Authentication is `built_in_database` with bcrypt, users from the init
file, credentials in the `EMQX` 1Password item. Authorization is
`no_match: deny`. Only localhost reads `$SYS/#`. Metrics come from
Prometheus, not `$SYS`.

The exporter (`exporter/deployment.yaml`, image `emqx/emqx-exporter`) cannot
be bootstrapped from the CR. The operator owns
`EMQX_API_KEY__BOOTSTRAP_FILE` and the Secret mounted there. Create an
Administrator API key named `exporter` in the dashboard, store
`EMQX_EXPORTER_API_KEY` and `EMQX_EXPORTER_API_SECRET` in 1Password, force-sync
the exporter ExternalSecret, and roll the exporter Deployment. The exporter
`/probe` publish path is omitted: it has no username field, and
`no_match: deny` rejects anonymous MQTT. The client-events dashboard panel
that needs probe metrics stays empty. The other dashboards need `/metrics`.

Expect three Prometheus `up` series matching `emqx`: two core pods from the
PodMonitor and one from the exporter ServiceMonitor.

The `gatus.io/enabled` ConfigMap is not a live Gatus endpoint. Skill
`observability`.

Two cores have no split-brain quorum. That is accepted for this broker. Do
not add a replicant tier as a drive-by.
