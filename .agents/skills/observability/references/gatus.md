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
does not restart the process. See the probes block in
`kubernetes/apps/base/selfhosted/n8n/app/helmrelease.yaml`.

Before trusting a probe or a Gatus condition, confirm the path can return
non-200. A name ending `(in-cluster)` in `config.yaml` means the check hits a
Service DNS name and skips Envoy. Keep that suffix on those entries so the
next edit does not "fix" them onto the public route.

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
