# Cilium

Chart pin, BGP CRs and LoadBalancer pool:
`app/ocirepository.yaml`, `app/networking.yaml`.

UniFi is not reconciled from here. The FRR snippet and peer table are in
skill `networking`
([bgp-unifi.md](../../../../../.agents/skills/networking/references/bgp-unifi.md)).
Human page: `.agents/skills/networking/references/bgp-unifi.md`.

Host firewall policy: skill `cilium-host-policy`. Manifest:
`app/hostpolicy-ceph.yaml`.
