# Cilium

Chart pin, BGP CRs and LoadBalancer pool:
`app/ocirepository.yaml`, `app/networking.yaml`.

UniFi is not reconciled from here. The FRR snippet and peer table are in
skill `networking`
([bgp-unifi.md](../../../../../.agents/skills/networking/references/bgp-unifi.md)).
Human page: `.agents/skills/networking/references/bgp-unifi.md`.

Host firewall policy: skill `cilium-host-policy`. Manifest:
`app/hostpolicy-ceph.yaml`.

Hubble: agent server, relay and UI are on, with flow metrics (dns, drop,
tcp, flow, port-distribution, icmp, httpV2) scraped by ServiceMonitors and
dashboards in the Grafana `Cilium` folder. The UI is
`hubble.${SECRET_DOMAIN}` on `envoy-internal` behind Authentik ExtAuth
(`app/httproute.yaml`, `app/securitypolicy.yaml`). Certs use
`hubble.tls.auto.method: cronJob`: the chart's `helm` method reuses its leaf
certs on every upgrade and never renews them, while certgen renews every four
months and keeps the Secrets out of the release, so no `driftDetection`
ignore is needed.
