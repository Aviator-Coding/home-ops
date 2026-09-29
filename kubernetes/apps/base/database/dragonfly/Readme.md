# Dragonfly

No central cluster. The operator chart tag is in `app/helmrelease.yaml`.
Each consumer includes `kubernetes/components/dragonfly/`. Image pin, args,
and the cache-only policy are in that component and in skill `databases`
(`references/dragonfly.md`).

Five consumers (authentik, litellm, paperless-ngx, rsshub, searxng), two
replicas each. `up{job=~".*dragonfly.*"}` should return 10 series. Prometheus
needs the additive `*-allow-prometheus` NetworkPolicy from the component.

`--cache_mode=true` means a restart drops the data on purpose. Do not add the
gnet dashboards 15944, 15945, 21053, or 21054. Those are a different project.
