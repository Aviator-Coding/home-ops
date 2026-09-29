# ServiceMonitor and PodMonitor selectors

`commonMetadata.labels` on a Flux Kustomization is applied at reconcile, not by
`kustomize build`. It overwrites `app.kubernetes.io/name` on resources built
from that Kustomization's `path`. It does not rewrite a HelmRelease's chart
output.

A hand-written ServiceMonitor that selects `app.kubernetes.io/name` equal to
anything other than the enclosing Kustomization's `*app` anchor matches zero
targets after reconcile, while the manifest looks right. `media/plex` selected
`plex-exporter` and the overlay set `app.kubernetes.io/name: plex`. Select a
port or endpoint name that the Service actually has (`port: metrics`), or
select the anchor name.

On a PodMonitor endpoint, `port:` is a container port **name**. `targetPort:`
is the number. They are not interchangeable. `ai/toolhive` once used
`targetPort: 8080` for a port nothing listened on; it now uses `port: http`
(4483).

Alertmanager's job relabel is separate and load-bearing. See
[alertmanager.md](alertmanager.md).

Before trusting a new monitor, check the live object's labels and that the
scrape target is up. Git is not the label set Flux applies.

kube-state-metrics' own health series (`KubeStateMetricsListErrors` and
siblings) come from a second telemetry port, 8081, not the 8080 `http` port.
It needs the container port, a Service port and its own ServiceMonitor
endpoint or those rules can never fire. Loki likewise needs its chart
`monitoring.serviceMonitor.enabled`.
