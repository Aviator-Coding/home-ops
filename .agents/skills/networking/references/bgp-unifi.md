# Cilium BGP and the UniFi FRR snippet

Source of truth for the CRs is
`kubernetes/apps/base/kube-system/cilium/app/networking.yaml`. Chart pin is
`app/ocirepository.yaml`. L2 announcements are off. Devices are `bond+`.

| Role | Value |
|---|---|
| k8s ASN | `64514` |
| UniFi ASN | `64513` |
| Cilium peer / UniFi router-id | `10.0.0.1` |
| Node neighbors | `10.10.10.11`, `.12`, `.13` on `bond0` |
| Control-plane VIP | `10.10.10.10` |
| VLAN gateway / node nameserver | `10.10.10.1` (a different address from `10.0.0.1`) |
| LB pool | `10.50.0.0/24` |
| Peer | `ebgpMultihop: 4`, graceful restart 15s |

`ebgpMultihop: 4` is required because `10.0.0.1` is off-subnet from
`10.10.10.0/24`. Same-VLAN peering on `10.10.3.0/24` is the old layout.
Private 16-bit ASNs are `64512-65534`.

UniFi BGP is not in Flux. This block is the only git copy. Confirm on the
UDM with `vtysh` before changing it. Do not put it back into a deleted
`cilium/unifi/bgp.conf`.

```
router bgp 64513
  bgp router-id 10.0.0.1
  no bgp ebgp-requires-policy

  neighbor k8s peer-group
  neighbor k8s remote-as 64514

  neighbor 10.10.10.11 peer-group k8s
  neighbor 10.10.10.12 peer-group k8s
  neighbor 10.10.10.13 peer-group k8s

  address-family ipv4 unicast
    redistribute connected
    neighbor k8s next-hop-self
    neighbor k8s soft-reconfiguration inbound
  exit-address-family
exit
```

Timers, `no bgp default ipv4-unicast`, `no bgp network import-check`, and a
global `neighbor k8s activate` were removed in `c3160f11`. Do not restore
them from older prose.

FRR commands, not Cisco: `show bgp summary`, `show bgp ipv4 unicast`,
`show bgp neighbors 10.10.10.11`. Expected prefixes are `/32`s inside
`10.50.0.0/24` with nexthops `.11` `.12` `.13`.

## Pinned addresses

`lbipam.cilium.io/ips` in git (re-grep before quoting this list in a change):

| IP | Service |
|---|---|
| `10.50.0.21` | envoy-external |
| `10.50.0.24` | falkordb |
| `10.50.0.26` | envoy-internal |
| `10.50.0.27` | agentgateway internal (also the hand-made wildcard target) |
| `10.50.0.29` | agentgateway public |
| `10.50.0.30` | emqx |
| `10.50.0.51` | syncthing |
| `10.50.0.52` | plex |
| `10.50.0.54` | tdarr |
| `10.50.0.55` | samba |
| `10.50.0.121` | kube-api |

`10.50.0.28` is not pinned. `internal-noauth` is ClusterIP. A LoadBalancer
on that Gateway exposes keyless paid-provider routes to the LAN.

Node underlay (Talos, not this skill's files): `bond0` 802.3ad MTU 9000,
DHCP on `10.10.10.0/24`, `bond0.3` and `bond0.90` present with no node
address. Skill `talos-nodes`.
