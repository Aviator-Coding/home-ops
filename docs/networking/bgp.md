# BGP (Cilium + UniFi)

Short index of **this** cluster. The Cilium objects live in
[`kubernetes/apps/base/kube-system/cilium/app/networking.yaml`](../../kubernetes/apps/base/kube-system/cilium/app/networking.yaml).
Operator notes and the last git copy of the UniFi snippet:
[`kubernetes/apps/base/kube-system/cilium/Readme.md`](../../kubernetes/apps/base/kube-system/cilium/Readme.md).

| Fact | Value |
|------|-------|
| k8s ASN | `64514` |
| UniFi ASN | `64513` |
| Cilium peer / UniFi BGP router-id | `10.0.0.1` |
| Node neighbors | `10.10.10.11`, `10.10.10.12`, `10.10.10.13` |
| LB pool | `10.50.0.0/24` |
| L2 announcements | disabled (`l2announcements.enabled: false`) |
| Cluster config | `CiliumBGPClusterConfig/l3-bgp-cluster-config` |
| Peer config | `CiliumBGPPeerConfig/l3-bgp-peer-config` (`ebgpMultihop: 4`) |
| Advertisement | `CiliumBGPAdvertisement/l3-bgp-advertisement` (Service `LoadBalancerIP`) |

`10.0.0.1` (BGP peer / LAN resolver) and `10.10.10.1` (node nameserver / VLAN
gateway) are both UniFi addresses. Docs should not collapse them.

UniFi BGP is not Flux-reconciled. Git cannot prove the UDM is running the last
checked-in snippet. A live `vtysh` `show bgp summary` closes that (C1 in the
Cilium readme). Do not treat 2025-08 dumps of `10.10.3.11-13` / `10.10.3.0/24`
as current.

Split DNS for internal hostnames is UniFi + external-dns
(`kubernetes/apps/base/network/unifi-dns`). There is no `k8s-gateway` app.

Node underlay snapshot vs current Talos: [`docs/network/cmd.md`](../network/cmd.md)
(labeled as a 2025-08 capture).

## Further reading

- https://sneekes.app/posts/advanced-kubernetes-networking-bgp-with-cilium-and-udm-pro/
