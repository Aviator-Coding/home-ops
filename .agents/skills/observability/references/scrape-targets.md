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
is the number. They are not interchangeable. `ai/toolhive` needed
`targetPort: 8080` because no port was named `8080`.

Alertmanager's job relabel is separate and load-bearing. See
[alertmanager.md](alertmanager.md).

Before trusting a new monitor, check the live object's labels and that the
scrape target is up. Git is not the label set Flux applies.
