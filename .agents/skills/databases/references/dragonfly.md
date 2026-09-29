# Dragonfly

There is no central Dragonfly cluster. The operator HelmRelease is
`kubernetes/apps/base/database/dragonfly/app/helmrelease.yaml` (chart tag
on that file). Each consumer includes `kubernetes/components/dragonfly/`,
which creates `<app>-dragonfly` in the app's namespace.

## Consumers

| App | Namespace | Endpoint |
|---|---|---|
| authentik | security | `authentik-dragonfly.security.svc.cluster.local:6379` |
| litellm | ai | `litellm-dragonfly.ai.svc.cluster.local:6379` |
| paperless-ngx | selfhosted | `paperless-ngx-dragonfly.selfhosted.svc.cluster.local:6379` |
| rsshub | selfhosted | `rsshub-dragonfly.selfhosted.svc.cluster.local:6379` |
| searxng | ai | `searxng-dragonfly.ai.svc.cluster.local:6379` |

Five clusters, two replicas each. `up{job=~".*dragonfly.*"}` should return
10 series. A note that says 12 scrape targets is stale.

## Cache-only

Args in the component include `--cache_mode=true`, `--maxmemory=512Mi`,
`--proactor_threads=2`, `--cluster_mode=emulated`. LRU eviction is the
consistency model. Snapshot backup is off on purpose. Authentik sessions
and other consumers lose this data on restart. Do not add a volume to
"make it durable" without a product decision.

`--maxmemory` must be at least `256Mi` times `proactor_threads`. With 2
threads the floor is 512Mi. `limits.memory` is 640Mi. Dropping the limit
below that without lowering `proactor_threads` crash-loops startup.

Requests equal limits (250m CPU, 640Mi) for Guaranteed QoS. The image pin
is the `image:` field in `components/dragonfly/cluster.yaml`.

## Placement and scrape

`topologySpreadConstraints` on `app.kubernetes.io/part-of: dragonfly` does
not keep one instance's two pods apart: the selector counts every Dragonfly
pod in the namespace. Required `podAntiAffinity` on `app: ${APP}-dragonfly`
is what separates the pair. `nodeTaintsPolicy: Honor` is set so a future
hard taint is not an ignored spread domain. The live talos-3 taint is
`PreferNoSchedule`, which Honor does not filter.

The operator NetworkPolicy allows peers and the controller on `:9999`, and
same-namespace pods on `:6379`. Prometheus needs the additive
`<app>-dragonfly-allow-prometheus` policy from the component. No password.
Cross-namespace clients are blocked.

Do not add the gnet Dragonfly dashboards 15944, 15945, 21053, or 21054.
Those are the unrelated Dragonfly file-distribution project. The operator
dashboard in the Database folder is the one this CRD feeds.

The component file is shared. A change there rolls every consumer. Skill
`node-scheduling` before touching the resource block.
