# EMQX

Stay on operator 2.2.x. The chart tag is in `operator/helmrelease.yaml`.
2.3.x drops `apps.emqx.io/v2beta1` and 404s the Enterprise `load_rebalance`
API against this OSS broker. Image pin, listeners, and ACL are in skill
`databases` (`references/emqx.md`).

Dashboard: `https://mqtt.${SECRET_DOMAIN}` (internal gateway). MQTT
LoadBalancer `10.50.0.30`. Do not remove `volumeClaimTemplates`.

The exporter API key is created in the dashboard (name `exporter`, role
Administrator) and stored as `EMQX_EXPORTER_API_KEY` /
`EMQX_EXPORTER_API_SECRET` on the EMQX 1Password item. Force-sync the
exporter ExternalSecret and roll `emqx-exporter`. The `/probe` path is
omitted because `no_match: deny` rejects anonymous MQTT.

The `gatus.io/enabled` ConfigMap is not a live Gatus endpoint.
