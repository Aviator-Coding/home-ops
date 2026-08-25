# Cilium + UniFi BGP

Current GitOps picture. Source of truth for Cilium is
[`app/networking.yaml`](./app/networking.yaml) plus the HelmRelease
(`bgpControlPlane.enabled`, `l2announcements.enabled: false`, `devices: bond+`).
Chart pin: `1.18.6` in [`app/ocirepository.yaml`](./app/ocirepository.yaml).

UniFi BGP is **not** reconciled by Flux. The snippet below is the last git copy of
`cilium/unifi/bgp.conf` (inlined here when that file was deleted on 2025-10-18).
A live `vtysh` `show bgp summary` on the UDM is the only way to confirm the
device is still running this config. See **C1** at the end.

## Current topology

| Role | Value |
|------|-------|
| k8s ASN | `64514` (`CiliumBGPClusterConfig/l3-bgp-cluster-config`) |
| UniFi ASN | `64513` |
| Cilium BGP peer / UniFi router-id | `10.0.0.1` |
| Node neighbors (UniFi side) | `10.10.10.11`, `.12`, `.13` (`bond0` on `10.10.10.0/24`) |
| Control-plane VIP | `10.10.10.10` on `bond0` |
| Node nameserver / VLAN gateway | `10.10.10.1` (do **not** collapse this with `10.0.0.1`) |
| LB pool | `10.50.0.0/24` (`CiliumLoadBalancerIPPool/pool`) |
| Advertisement | `CiliumBGPAdvertisement/l3-bgp-advertisement` → all `LoadBalancerIP`s |
| Peer config | `CiliumBGPPeerConfig/l3-bgp-peer-config` (`ebgpMultihop: 4`, graceful restart 15s) |
| L2 announcements | **off** (no `CiliumL2AnnouncementPolicy`) |
| Cilium devices | `bond+` (`bond0`, `bond0.3`, `bond0.90`) |

`ebgpMultihop: 4` is required because the UniFi peer `10.0.0.1` is off-subnet
from the node IPs on `10.10.10.0/24`. Same-VLAN peering on `10.10.3.0/24` is
the old layout, not current Talos.

Private 16-bit ASNs are `64512-65534` (RFC 6996; `65535` is reserved).

### Node underlay (Talos)

From `talos/nodes/talos-{1,2,3}.yaml.j2` and `talos/machineconfig.yaml.j2`:

- i40e ports aliased to `net0`/`net1`, bonded as `bond0` (802.3ad, MTU 9000)
- DHCPv4 on `bond0` only; kubelet `nodeIP.validSubnets: 10.10.10.0/24`
- `bond0.3` (VLAN 3) and `bond0.90` (VLAN 90) exist for Multus. Nodes do **not**
  take addresses on them. IoT macvlan gateway `10.40.0.1`; VPN macvlan gateway
  `192.168.90.1`.

Talos will not put `10.10.3.11` on a node unless some out-of-band DHCP/NIC
still exists.

## UniFi config (last git copy)

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
global `neighbor k8s activate` were stripped from this config in `c3160f11`
(2025-08-11). Do not re-add them from older prose.

```
┌─────────────────┐    eBGP     ┌─────────────────┐
│  UniFi (UDM)    │◄──────────► │ talos-1         │
│  AS 64513       │             │ AS 64514        │
│  10.0.0.1       │             │ 10.10.10.11     │
└─────────────────┘             └─────────────────┘
                                ┌─────────────────┐
                                │ talos-2 / .12   │
                                │ talos-3 / .13   │
                                └─────────────────┘
```

## Cilium CRs

All in [`app/networking.yaml`](./app/networking.yaml):

- `CiliumLoadBalancerIPPool/pool` - `10.50.0.0/24`
- `CiliumBGPAdvertisement/l3-bgp-advertisement` - advertise Service `LoadBalancerIP`
- `CiliumBGPPeerConfig/l3-bgp-peer-config` - peer to UniFi
- `CiliumBGPClusterConfig/l3-bgp-cluster-config` - local ASN 64514, peer `unifi` at `10.0.0.1` ASN 64513
- `Service/kube-api` - `lbipam.cilium.io/ips: 10.50.0.121`

### Expected advertised prefixes (from git `lbipam.cilium.io/ips`)

A current UniFi `show bgp ipv4 unicast` should list `/32`s in `10.50.0.0/24`
with nexthops `10.10.10.11-13`, not the old `10.10.3.0/24` pool. More than five
prefixes.

| IP | Service |
|----|---------|
| `10.50.0.21` | envoy-external |
| `10.50.0.26` | envoy-internal |
| `10.50.0.27` / `.28` / `.29` | agentgateway (internal / internal-noauth / public) |
| `10.50.0.30` | emqx |
| `10.50.0.50` / `.52` / `.54` | jellyfin / plex / tdarr |
| `10.50.0.51` | syncthing |
| `10.50.0.121` | kube-api |

## Verify on UniFi (FRR / vtysh)

Enable SSH on the UniFi controller, connect, then `vtysh`. Use FRR commands
(not Cisco `show ip bgp ...`):

```
show bgp summary
show bgp ipv4 unicast
show bgp neighbors 10.10.10.11
```

Expect three neighbors `10.10.10.11-13` in AS 64514, router identifier
`10.0.0.1`, local AS 64513, and `10.50.0.x/32` prefixes as above.

## C1 - live UniFi vs git (open)

Git cannot prove the UDM is running the snippet above. This worktree can ping
`10.0.0.1` and `10.10.10.11` but has no UniFi SSH credentials and no
`talosconfig`, so the live session was not captured.

- **Side A (git, likely current operator intent):** router-id `10.0.0.1`,
  neighbors `10.10.10.11-13`, AS 64513↔64514, pool `10.50.0.0/24`.
- **Side B (older captures in this file's history):** neighbors `10.10.3.11-13`,
  advertised `10.10.3.2-7/32`. Predates the 2025-08-16 subnet cutover
  (`ade7b639`). Do not assume Side B is live.

## Historical capture (~2025-08, pre-`10.50.0.0/24` pool)

Neighbors `10.10.3.11-13` and five `/32`s in `10.10.3.0/24`. Kept only as a
dated dump. Not current.

`show bgp summary` (UniFi, FRR):

```
IPv4 Unicast Summary:
BGP router identifier 10.0.0.1, local AS number 64513 VRF default vrf-id 0
BGP table version 25
RIB entries 9, using 1152 bytes of memory
Peers 3, using 71 KiB of memory
Peer groups 1, using 64 bytes of memory

Neighbor        V         AS   MsgRcvd   MsgSent   TblVer  InQ OutQ  Up/Down State/PfxRcd   PfxSnt Desc
10.10.3.11      4      64514      2250      2250       25    0    0 01:13:19            5        5 N/A
10.10.3.12      4      64514      2249      2249       25    0    0 01:13:19            5        5 N/A
10.10.3.13      4      64514      2250      2250       25    0    0 01:13:20            5        5 N/A

Total number of neighbors 3
```

`show bgp ipv4 unicast` from the same session:

```
BGP table version is 25, local router ID is 10.0.0.1, vrf id 0
Default local pref 100, local AS 64513
Status codes:  s suppressed, d damped, h history, u unsorted, * valid, > best, = multipath,
               i internal, r RIB-failure, S Stale, R Removed
Nexthop codes: @NNN nexthop's vrf id, < announce-nh-self
Origin codes:  i - IGP, e - EGP, ? - incomplete
RPKI validation codes: V valid, I invalid, N Not found

     Network          Next Hop            Metric LocPrf Weight Path
 *>  10.10.3.2/32     10.10.3.13                             0 64514 i
 *=                   10.10.3.11                             0 64514 i
 *=                   10.10.3.12                             0 64514 i
 *>  10.10.3.3/32     10.10.3.13                             0 64514 i
 *=                   10.10.3.11                             0 64514 i
 *=                   10.10.3.12                             0 64514 i
 *>  10.10.3.5/32     10.10.3.13                             0 64514 i
 *=                   10.10.3.11                             0 64514 i
 *=                   10.10.3.12                             0 64514 i
 *>  10.10.3.6/32     10.10.3.13                             0 64514 i
 *=                   10.10.3.11                             0 64514 i
 *=                   10.10.3.12                             0 64514 i
 *>  10.10.3.7/32     10.10.3.13                             0 64514 i
 *=                   10.10.3.11                             0 64514 i
 *=                   10.10.3.12                             0 64514 i

Displayed 5 routes and 15 total paths
```
