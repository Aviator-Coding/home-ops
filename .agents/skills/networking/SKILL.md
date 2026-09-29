---
name: networking
description: "Read before adding or editing an HTTPRoute, Gateway, HTTPRouteFilter, SecurityPolicy, external-dns annotation, LoadBalancer IP/BGP setting, NetworkPolicy or CiliumNetworkPolicy, or when a hostname is unreachable or DNS resolves wrong. Covers the DNS target coming from the parent Gateway, hand-made UniFi records, DNS-reply port:53 rules that never match, and supplementing chart-owned policies."
---

# Networking: Gateway API, DNS, BGP and NetworkPolicy

Cilium is the CNI: BGP LoadBalancer, kube-proxy replacement, no L2
announcements. Gateways are Envoy Gateway (`gatewayClassName: envoy`) for
`envoy-internal` / `envoy-external`, and agentgateway for its own listeners.
Host firewall policy is skill `cilium-host-policy`. ExtAuth ownership is skill
`authentik-terraform`.

## Tripwires

1. **The DNS target comes from the parent Gateway.** For the
   `gateway-httproute` source, `external-dns.alpha.kubernetes.io/target` on the
   HTTPRoute does not decide the record. A route on both envoy gateways yields
   both targets, and the UniFi webhook keeps the first. Route-level targets
   that match their Gateway are documentation.
2. **A record with no TXT twin is hand-made.** Every record
   `network/unifi-dns` manages has a `k8s.main.<type>-<name>.${SECRET_DOMAIN}`
   TXT twin. external-dns will not update or delete a record that lacks one.
   The LAN wildcard `*.${SECRET_DOMAIN}` -> `10.50.0.27` is hand-made, which
   is why unclaimed names hit the AI gateway. [dns-ownership.md](references/dns-ownership.md)
3. **A NetworkPolicy `ports:` entry matches the destination port on the
   protected pod.** A DNS reply arrives on the querier's ephemeral port, never
   53, so `port: 53` cannot admit it. Drop the `ports:` restriction on the
   from-kube-dns rule. Source-port filtering is not expressible in
   `networking.k8s.io/v1`.
4. **Chart-owned policies get a supplemental NetworkPolicy**, same
   `podSelector`. NetworkPolicies on the same pod OR-combine. Do not disable
   the chart policy. Shared dragonfly policy:
   `kubernetes/components/dragonfly/networkpolicy.yaml`.
5. **Envoy controller resources live under `deployment.envoyGateway`.**
   A `resources:` block under `config.envoyGateway` is the config-file schema
   and is silently ignored. [envoy-gateway.md](references/envoy-gateway.md)
6. **Restart Envoy after a GatewayNamespaceMode outage before calling it
   healthy.** Stale proxy SA tokens storm xDS auth. The controller's exit-137
   was that storm, not an OOM.
7. **`nslookup` needs a trailing dot** on a name you do not want search
   appended. Alpine busybox `nslookup` does not apply `resolv.conf` search to
   short names (NXDOMAIN). musl and glibc `getent hosts` do. Pods use
   nameserver `10.43.0.10` and `ndots:5`.
8. **LoadBalancer IPs are pinned with `lbipam.cilium.io/ips`.** The pool is
   `10.50.0.0/24`. `internal-noauth` stays ClusterIP. Do not put it back on
   the pool. [bgp-unifi.md](references/bgp-unifi.md)
9. **Every browser terminal is behind Authentik SSO.** `hermes-code` and
   `hass-code` run code-server with `--auth none`, so the SecurityPolicy
   (`hermes-code-auth`, `hass-code-auth`) is the only gate on the route. A new
   code-server route needs one in the same change. The Service port stays open
   to in-cluster callers, so this is an outer layer only.
10. **Host-endpoint Cilium policy** (default-deny, selector labels, msgr ports)
   is skill `cilium-host-policy`. The manifests live next to the CNI. Do not
   edit that skill from a networking change.

## Where things live

| What | Path |
|---|---|
| Cilium chart and BGP CRs | `kubernetes/apps/base/kube-system/cilium/` |
| Envoy Gateway | `kubernetes/apps/base/network/envoy-gateway/` |
| Public / internal DNS | `kubernetes/apps/base/network/{cloudflare-dns,unifi-dns}/` |
| Shared dragonfly NetworkPolicy | `kubernetes/components/dragonfly/networkpolicy.yaml` |
| Diagnostics | `task --list` under `network:` (`.taskfiles/network/`) |
| UniFi FRR snippet | [bgp-unifi.md](references/bgp-unifi.md) (the only git copy) |

## Procedures

- Gateway, filter, chart path: [envoy-gateway.md](references/envoy-gateway.md).
- DNS ownership and the reply-port rule: [dns-ownership.md](references/dns-ownership.md).
- BGP, FRR, pinned LB IPs: [bgp-unifi.md](references/bgp-unifi.md).
- Plex ingress CiliumNetworkPolicy: [plex-ingress-policy.md](references/plex-ingress-policy.md).
- `task network:*`: [diagnostics.md](references/diagnostics.md).

## Verify

- DNS ownership: port-forward `deploy/unifi-dns` in `network` to 8888 and
  `curl -H 'Accept: application/external.dns.webhook+json;version=1' localhost:8888/records`.
- A new NetworkPolicy: `cilium monitor --type drop` shows the destination
  port of a dropped DNS reply (ephemeral, not 53).
- BGP: UniFi `vtysh` `show bgp summary` (FRR syntax, not Cisco `show ip bgp`).
