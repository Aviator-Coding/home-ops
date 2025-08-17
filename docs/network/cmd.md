🌐 Networking

interfaces → All network interfaces (physical + bonds + tunnels).

routes → Kernel routing table.

endpoints → Cluster API endpoints.

links / linkstatus → Link state info (you used this earlier).



get link status `talosctl -n 10.10.3.11 get links`

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
