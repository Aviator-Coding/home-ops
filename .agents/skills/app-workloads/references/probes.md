# Probes that can fail

kube-prometheus-stack already alerts on `KubePodNotReady` (`for: 15m`),
`KubePodCrashLooping`, and `OOMKilled` in every namespace. A probe-less
app never generates those signals. Adding a liveness or readiness probe
is usually the whole fix. A second "app is down" PrometheusRule duplicates
them. Skill `observability`.

## The path must be able to fail

n8n's `/healthz` answers `{"status":"ok"}` with no dependency check.
Upstream treats it as not caring about the database. Pointing readiness
and Gatus at it kept the pod `Ready` and Gatus green while every real
route returned `Database is not ready!`. The database-aware endpoint is
`/healthz/readiness`.

Before trusting a probe or a Gatus check, confirm the path returns
non-200 when the thing it represents is down. A curl against a healthy
pod only proves the happy path.

Worked example: `kubernetes/apps/base/selfhosted/n8n/app/helmrelease.yaml`.
Home Assistant uses probes instead of a bespoke down-alert:
`kubernetes/apps/base/home-automation/home-assistant/app/helmrelease.yaml`.

## Gatus

Gatus discovers `gatus.home-operations.com/endpoint` on HTTPRoutes.
`gatus.io/enabled` on a ConfigMap is dead: the sidecar never watches
ConfigMaps. Non-HTTP checks are hand-written in
`kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml`.
Skill `observability`.

## Startup versus liveness

A slow first boot belongs on `startupProbe`, not a tight `livenessProbe`.
A liveness failure restarts the pod; a readiness failure only pulls it
out of Service. Do not point both at a path that cannot fail.
