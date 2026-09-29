# Gatus and probes

Gatus serves two sources:

- HTTPRoutes annotated `gatus.home-operations.com/endpoint` (auto-discovered).
- Hand-written endpoints in
  `kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml`.

`gatus.io/enabled` on a ConfigMap is dead. The sidecar RBAC watches Services,
Gateways and HTTPRoutes, not ConfigMaps, and a pod cannot mount another
namespace's ConfigMap. Two leftovers still carry the label and still exist in
the cluster: `database/emqx-gatus-ep` and `database/postgres-17-gatus-ep`
(from `database/emqx/cluster/gatus.yaml` and
`database/cloudnative-pg/cluster-17/gatus.yaml`). They do not appear in
`GET /api/v1/endpoints/statuses`. Do not copy that pattern. A non-HTTPRoute
check goes in `config.yaml` (see `Radarr Import Queue`).

## Checks that cannot fail

An ext_authz `SecurityPolicy` answers 200 or 302 before the backend. A
status-only condition on that HTTPRoute stays green through an outage. The
Home Assistant check goes to the in-cluster Service and matches a body field
from Home Assistant's own `manifest.json`. The code-server sidecar also serves
`/manifest.json` at 200, so status alone cannot tell them apart.

n8n `/healthz` is unconditional `{"status":"ok"}`. Readiness and the Gatus
check use `/healthz/readiness`. Liveness stays on `/healthz` so a database blip
or a CNPG switchover does not restart the process. The startup probe stays
disabled: a readiness failure does not restart the pod, so a long migration
just holds it NotReady. A startup probe on that path CrashLoops through the
migration. See
`kubernetes/apps/base/selfhosted/n8n/app/helmrelease.yaml`.

Before trusting a probe or a Gatus condition, confirm the path can return
non-200. A name ending `(in-cluster)` in `config.yaml` means the check hits a
Service DNS name and skips Envoy. Keep that suffix. Gatus keys endpoints by
lowercased group and name, and the sidecar already publishes `external` /
`echo` and `external` / `kromgo`. A static entry with those same names panics
at startup (`name and group combination must be unique`). The public route
checks measure Authentik, because those routes carry a SecurityPolicy. Echo
and Kromgo in-cluster checks match a health body a login page cannot.
`github-webhook` stays on its route check: GET `/hook/` expecting 404 is the
receiver.

`GatusServiceDown` pages every group except `external` and `connectivity`,
and excludes `Radarr Import Queue` and `Hermes Gateway` because those have
their own alerts. `connectivity` is the household WAN, not an app.
`GatusEndpointDown` is the `external` group at `for: 5m`. `GatusServiceDown`
is `for: 10m`. Do not drop those exclusions.

`HermesGatewayDown` is `for: 10m`. Hermes is a single-replica Recreate
deployment, so every restart has a real outage window longer than the
external checks' 5m. A restart counter does not cover it: the container can
stay up while the gateway is down and the dashboard stays healthy.

The MCP gateway check posts `initialize` to the in-cluster ToolHive URL, not
the auto-discovered `/health`. `/health` stays 200 while `initialize` hangs.
The client timeout is 25s, under Hermes' connect timeout, and the interval
is 5m because each probe embeds the catalog. The body must match vmcp's
`serverInfo` name. A fast embedding failure can still return 200 and then
drop the session: Gatus cannot send the follow-up that would carry
`Mcp-Session-Id`. A down embedding pod is `KubePodNotReady`. An embedding
pod that is up and returning errors is not this check.

`fsGroup: 65532` on the Gatus pod is load-bearing. The app image runs as root
and the sidecar is distroless nonroot 65532, and they share the config PVC.
Without that group the sidecar cannot write, auto-discovery stops, and Gatus
silently keeps only the static endpoints.

## Stock pod rules

Once an app has probes that can fail, `KubePodNotReady`, `KubePodCrashLooping`
and `OOMKilled` already cover "is it down" in every namespace. A second rule
for the same fact is the chatty-rule pattern. Home Assistant's probes are the
worked `probes:` block:
`kubernetes/apps/base/home-automation/home-assistant/app/helmrelease.yaml`.

## Empty tokens look healthy

ExternalSecret `SecretSynced` does not mean a field is non-empty (skill
`secrets-1password`). An exporter that reads an empty token returns 500 while
Gatus and the ExternalSecret stay green. Check the secret key's decoded
length, not the ExternalSecret status. Plex's token path is skill
`media-stack`.
