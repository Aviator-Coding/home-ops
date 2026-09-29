# Network diagnostics

Recipes live in `.taskfiles/network/Taskfile.yaml` (`task network:<name>`).
They talk to Talos nodes `10.10.10.11/12/13`. Do not target the VIP
`10.10.10.10`, and do not target `10.10.3.11` (retired underlay).

| Task | What it does |
|---|---|
| `network:check-network-config` | interface config on each node |
| `network:check-bond-status` | `bond0` health |
| `network:check-routes` | routing tables |
| `network:ping-test` | node-to-node connectivity |
| `network:speed-test-all` | iperf3 across the six directed pairs |
| `network:speed-test-<a>-to-<b>` | one directed pair (`1-to-2` … `3-to-2`) |
| `network:ceph-network-test` | Ceph network path |
| `network:bandwidth-test` | bandwidth sample |
| `network:network-summary` | rollup |
| `network:full-test` | the suite |

`deploy-iperf3-server` and `cleanup-iperf3` bracket a speed test. Read the
Taskfile before running `full-test`. Sample output in old docs was captured
on the `10.10.3.0/24` layout and is not a baseline.

Link state, when a `talosconfig` is available:

```bash
talosctl -n 10.10.10.11 get links
talosctl -n 10.10.10.11 get ethtool
talosctl -n 10.10.10.11 get addresses
```

Expect `bond0` on `10.10.10.0/24`, plus `bond0.3` and `bond0.90` with no node
IP. There is no `get link status` verb. `get linkstatus` works.
