## BGP Verification
### Unifi Side
Enable ssh in the unifi controller and connect to it. run `vtysh` i order to open the frr terminal.
Run `show bgp summary`
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

In order to the unicast you can use
`show bgp ipv4 unicast`

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
