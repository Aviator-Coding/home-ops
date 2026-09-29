# Power-down and power-on

Whole-cluster electrical work. Do this while etcd and Ceph mon quorum still exist. After two nodes are down, `kubectl` and `ceph` are not reliable. `talosctl` talks to each node's own API and still works.

Node addresses: talos-1 `10.10.10.11`, talos-2 `10.10.10.12`, talos-3 `10.10.10.13`. Never target the VIP `10.10.10.10`.

`just talos shutdown-node` hard-codes `talosctl shutdown --force` and skips cordon and drain. Do not use it here.

## Before the first shutdown

```sh
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph -s          # HEALTH_OK, 6/6 OSDs, 3 mons
task rook:check-osd-device-paths
flux get ks -A ; flux get hr -A                                      # nothing mid-fail
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd set noout
```

`noout` alone is enough for an outage of minutes. Add `norecover`, `nobackfill`, `norebalance`, `nodown` and `pause`, and scale the Ceph deployments to zero, only when the cluster will stay down for hours.

Wait out anything already running:

```sh
kubectl get snapshot -A -o json | jq -r '.items[] | select(.status.phase=="Running") | "\(.metadata.namespace)/\(.metadata.name)"'
kubectl get jobs -A -l app.kubernetes.io/created-by=volsync -o json | jq -r '.items[] | select(.status.active>0) | "\(.metadata.namespace)/\(.metadata.name)"'
```

To stop a new run from starting, suspend and scale the controllers to zero. This was reasoned from the operator design and has not been executed as a drill:

```sh
flux suspend hr kopiur -n system  && kubectl -n system scale deploy/kopiur-controller --replicas=0
flux suspend hr volsync -n system && kubectl -n system scale deploy/volsync --replicas=0
```

If that does not hold, suspend each `SnapshotPolicy` (`spec.suspend: true`). See `kubernetes/components/kopiur/Readme.md`.

## Hibernate postgres-17 only for a long outage

Skip this for a short drain-and-power cycle. The first two shutdowns already give CNPG a clean switchover. Hibernation takes the database down for the whole window, including Authentik SSO and every other client of `postgres-17-rw`.

`cnpg.io/hibernation=on` is cluster-wide. It deletes the primary first, then the replicas, on every node. It cannot target "the last node". Set it before any drain, and only when `kubectl -n database get cluster postgres-17` already says healthy. The operator defers hibernation otherwise. PVCs stay Bound. Resume with `hibernation=off` brings the same primary back.

The chart tag is the OCIRepository in `kubernetes/apps/base/database/cloudnative-pg/operator/`. Re-read the running operator image before treating an upstream source line as current.

```sh
kubectl -n database annotate cluster postgres-17 cnpg.io/hibernation=on --overwrite
kubectl -n database get cluster postgres-17 -o jsonpath='{.status.conditions[?(@.type=="cnpg.io/hibernation")].reason}'
```

## Shutdown

talos-1 and talos-2 first, either order. Plain shutdown cordons and drains. CNPG watches the cordon and switchovers the primary before the PDB eviction. Each call waits until that node is off.

```sh
talosctl -n 10.10.10.11 shutdown
talosctl -n 10.10.10.12 shutdown
```

talos-3 last. There is nowhere left to switch the primary to, and the mon PDB is likewise unsatisfiable. Do not rely on Talos enforcing PDBs (siderolabs/talos#9882). The last instance takes an ordinary termination. That is an accepted residual risk: the other two replicas are already off, and the safety net is WAL archiving to the barman store, not a switchover that cannot happen. If you hibernated above, this risk is already closed.

```sh
talosctl -n 10.10.10.13 shutdown
```

Cut power only after all three calls have returned.

## Power-on

1. talos-3 dock PSU on. Wait 5-10 seconds. Fans and the card LED should be on. `pcie_port_pm=off` does not replace this. See [talos-3-b70-reboot.md](talos-3-b70-reboot.md).
2. Power talos-1 and talos-2. Two nodes restore etcd and mon quorum.
3. Power talos-3 after the dock wait.
4. `kubectl get nodes` shows three Ready.
5. `ceph -s` is `HEALTH_OK`, 6/6 OSDs, 3 mons. Then `ceph osd unset noout`.
6. `flux resume hr kopiur -n system` and `flux resume hr volsync -n system`. Resume restores the Git replica count.
7. If you set hibernation: `kubectl -n database annotate cluster postgres-17 cnpg.io/hibernation=off --overwrite`. Wait for healthy, 3/3.
8. `devic.es/b70` allocatable is 99 on talos-3 and 0 on the others.

```sh
kubectl get nodes -o custom-columns='NAME:.metadata.name,B70:.status.allocatable.devic\.es/b70'
```
