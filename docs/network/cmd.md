# Node networking snapshot (2025-08-16)

**This file is a historical capture, not current node networking.**

Captured 2025-08-16 (`648600cb`) during the `10.10.3.0/24` → `10.10.10.0/24`
cutover. The node was dual-homed (`bond0/10.10.10.11` **and**
`enp89s0/10.10.3.11`). Interface names are pre-alias X710 ports
(`enp2s0f0np0` / `enp2s0f1np1`) plus onboard `enp87s0` / `enp89s0`. There are
no `bond0.3` / `bond0.90` links in this dump.

Do **not** use `talosctl -n 10.10.3.11` as the default node in runbooks.
Current node IPs are `10.10.10.11/12/13`. Current Talos API / Kubernetes node
IP is the `bond0` address (`nodeIP.validSubnets: 10.10.10.0/24`).

The `talosctl get links` / `get ethtool` / `get addresses` **verbs** are still
valid (`docs/talosctl.md` ResourceDefinitions: `link links linkstatus`,
`ethtool ethernetstatus`, `address addresses`). The target node and expected
output below are not.

Even as a snapshot the two 2025-08 captures disagree: `get ethtool` shows
`enp89s0 LINK=false` while `get addresses` shows it addressed.

---

## Current (from Talos git, 2026-08)

Live `talosctl` was **not** captured in this worktree (empty `talosconfig`).
Expected layout from `talos/nodes/talos-1.yaml.j2` and
`talos/machineconfig.yaml.j2`:

| Link | Role |
|------|------|
| `net0` / `net1` | i40e ports aliased so the bond survives PCIe bus renumber (`pci=assign-busses`) |
| `bond0` | 802.3ad LACP, MTU 9000, DHCPv4, node IP `10.10.10.11/24`, VIP `10.10.10.10` |
| `bond0.3` | VLAN 3, no Address/DHCP on the node (Multus IoT macvlan master) |
| `bond0.90` | VLAN 90, no Address/DHCP on the node (Multus VPN macvlan master) |

NIC-name cutover: `a67806b5` (2026-06-10). Aliases `get links` and
`get linkstatus` both work; there is no `get link status` verb.

```
talosctl -n 10.10.10.11 get links
talosctl -n 10.10.10.11 get ethtool
talosctl -n 10.10.10.11 get addresses
```

Expect `bond0` addressed on `10.10.10.0/24`, plus `bond0.3` and `bond0.90`
present **without** node IPs. `enp89s0/10.10.3.11` should be gone. Confirm
with the commands above when a `talosconfig` is available.

---

## Historical capture (talos-1 via `10.10.3.11`, 2025-08-16)

interfaces → All network interfaces (physical + bonds + tunnels).

routes → Kernel routing table.

endpoints → Cluster API endpoints.

links / linkstatus → Link state info.

`talosctl -n 10.10.3.11 get links`

```
vscode ➜ /workspaces/home-ops (main) $ talosctl -n 10.10.3.11 get links
NODE         NAMESPACE   TYPE         ID            VERSION   TYPE       KIND     HW ADDR                                           OPER STATE   LINK STATE
10.10.3.11   network     LinkStatus   bond0         5         ether      bond     58:47:ca:78:c8:9a                                 up           true
10.10.3.11   network     LinkStatus   dummy0        1         ether      dummy    56:ab:fe:0f:0f:ff                                 down         false
10.10.3.11   network     LinkStatus   enp2s0f0np0   5         ether               58:47:ca:78:c8:9a                                 up           true
10.10.3.11   network     LinkStatus   enp2s0f1np1   4         ether               58:47:ca:78:c8:9a                                 up           true
10.10.3.11   network     LinkStatus   enp87s0       2         ether               58:47:ca:78:c8:9c                                 down         false
10.10.3.11   network     LinkStatus   enp89s0       3         ether               58:47:ca:78:c8:9d                                 up           true
10.10.3.11   network     LinkStatus   ip6tnl0       1         tunnel6    ip6tnl   00:00:00:00:00:00:00:00:00:00:00:00:00:00:00:00   down         false
10.10.3.11   network     LinkStatus   lo            2         loopback            00:00:00:00:00:00                                 unknown      true
10.10.3.11   network     LinkStatus   sit0          1         sit        sit      00:00:00:00                                       down         false
10.10.3.11   network     LinkStatus   teql0         1         void                                                                  down         false
10.10.3.11   network     LinkStatus   tunl0         1         ipip       ipip     00:00:00:00                                       down         false
```

```
vscode ➜ /workspaces/home-ops (main) $ talosctl -n 10.10.3.11 get ethtool
NODE         NAMESPACE   TYPE             ID            VERSION   LINK    SPEED
10.10.3.11   network     EthernetStatus   bond0         2         true
10.10.3.11   network     EthernetStatus   enp2s0f0np0   2         true
10.10.3.11   network     EthernetStatus   enp2s0f1np1   4         true
10.10.3.11   network     EthernetStatus   enp87s0       1         false
10.10.3.11   network     EthernetStatus   enp89s0       1         false
```

```
vscode ➜ /workspaces/home-ops (main) $ talosctl -n 10.10.3.11 get addresses

NODE         NAMESPACE   TYPE            ID                                     VERSION   ADDRESS                        LINK
10.10.3.11   network     AddressStatus   bond0/10.10.10.11/24                   1         10.10.10.11/24                 bond0
10.10.3.11   network     AddressStatus   bond0/fe80::5a47:caff:fe78:c306/64     2         fe80::5a47:caff:fe78:c306/64   bond0
10.10.3.11   network     AddressStatus   enp89s0/10.10.3.11/24                  1         10.10.3.11/24                  enp89s0
10.10.3.11   network     AddressStatus   enp89s0/fe80::5a47:caff:fe78:c309/64   2         fe80::5a47:caff:fe78:c309/64   enp89s0
10.10.3.11   network     AddressStatus   lo/127.0.0.1/8                         1         127.0.0.1/8                    lo
10.10.3.11   network     AddressStatus   lo/169.254.116.108/32                  1         169.254.116.108/32             lo
10.10.3.11   network     AddressStatus   lo/::1/128                             1         ::1/128                        lo
```
