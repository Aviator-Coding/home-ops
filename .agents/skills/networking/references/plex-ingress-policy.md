# Plex ingress CiliumNetworkPolicy

Manifest: `kubernetes/apps/base/media/plex/app/ciliumnetworkpolicy.yaml`.

## Why it exists

Plex's `ALLOWED_NETWORKS` (media/plex `helmrelease.yaml`) is
`10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`. The cluster pod CIDR `10.42.0.0/16`
sits inside `10.0.0.0/8`, so any pod could read `/library/sections`,
`/status/sessions`, `/:/prefs` and similar with no Plex credential. Plex cannot
subtract a CIDR from `ALLOWED_NETWORKS`, so the exclusion is done at L3: the
policy default-denies ingress on the plex pod and allows back only what needs
it. This closes the unauthenticated pod-network path and changes no Plex auth.

## What is allowed back

- **`world`, `remote-node`, `host` entities.** The Cilium LoadBalancer runs in
  DSR mode (`loadBalancer.mode: dsr` in kube-system/cilium), so LAN and other
  non-cluster clients keep their real source IP and never look like pod-network
  traffic. Every `ALLOWED_NETWORKS` range keeps working.
- **envoy-external proxy pods** (`network` namespace, label
  `gateway.envoyproxy.io/owning-gateway-name: envoy-external`). It is the only
  Gateway the plex HTTPRoute attaches to, and it proxies as a new connection
  from its own pod IP.
- **`plex-exporter`** (same namespace). It authenticates with its own
  `PLEX_TOKEN`, so it does not rely on the bypass this policy closes.
- **CoreDNS replies** (`kube-system`, `k8s-app: kube-dns`), with no `ports:`
  restriction on purpose: see tripwire 3 in `SKILL.md`. Cilium reply-conntrack
  also fails to match a DNS reply to its request when the client pod's node
  hosts a CoreDNS replica.

A new in-cluster consumer of Plex needs its own `fromEndpoints` entry and its
own Plex token.
