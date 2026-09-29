# SurrealDB

## Single writer, no rollback

`replicaCount: 1`. RocksDB allows one process to hold the data-dir lock.
Ceph RBD is the replication. Do not raise the replica count, and do not
read a hostname anti-affinity or topology spread on this Deployment as a
sign it should have a second writer. Strategy is `Recreate` because the
claim is RWO.

`upgrade.remediation.strategy: uninstall`. The last successful release
before RocksDB pointed `SURREAL_PATH` at TiKV. A rollback would restore
`tikv://` against a cluster that no longer has TiKV and then wedge.
`uninstall.keepHistory: false`.

Path is `rocksdb:///data/surrealdb`. Image runs as UID 65532.
`fsGroup: 65532` so the Ceph mount is writable. `unauthenticated: false`.

## Do not set storageClassName

`persistence.storageClassName` is omitted. SurrealDB chart 0.4.0 renders a
duplicate `storageClassName` key when the value is set, and Helm template
rendering breaks. The cluster default StorageClass is `ceph-block`. Size
20Gi, mount `/data`.

## CPU request and Gatus

Measure CPU at `[14d:30s]` before trimming the request: `[14d:1h]` and
`[14d:5m]` read the same peak several times low.

The Gatus check targets `/health`, not `/`. Bare `/` 307-redirects to a
surrealdb.com page that Gatus follows, so the check would depend on a host this
repo does not control.

## Alerts

`SurrealDBReplicasUnavailable` is
`kube_deployment_status_replicas_available{deployment="surrealdb"} < 1`.
A threshold of `< 2` fires forever on a healthy single replica.

`SurrealDBHighCPU` divides by `kube_pod_container_resource_requests`,
because this container declares a CPU request and no CPU limit. A
limit-based rule is an empty series. There is no `container_spec_*` metric.
Skill `observability`.

## Dead route block

The chart `route:` key does not produce a working HTTPRoute. Leave it unset.
A real route is its own manifest. Do not paste the old homepage / `sdb`
block back in.
