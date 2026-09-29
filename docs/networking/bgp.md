# UniFi BGP peer config

Cilium's BGP CRs are in
`kubernetes/apps/base/kube-system/cilium/app/networking.yaml`.
The UniFi side is not in Flux. The only git copy of the FRR snippet, the
ASN/peer table, and the pinned `10.50.0.0/24` addresses are in skill
`networking`:
[bgp-unifi.md](../../.agents/skills/networking/references/bgp-unifi.md).

Confirm on the UDM with `vtysh` (`show bgp summary`), not Cisco `show ip bgp`.
Peer `10.0.0.1` is not the VLAN gateway `10.10.10.1`.
