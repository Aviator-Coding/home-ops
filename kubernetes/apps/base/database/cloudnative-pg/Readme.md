# CloudNativePG

Tripwires, the single barman destination, and the health gate live in skill
`databases` (`references/cnpg.md`). The operator chart tag is the
OCIRepository `ref.tag` in `operator/helmrelease.yaml`, not a version written
here. The `gatus.io/enabled` ConfigMap in `cluster-17/` is not a live Gatus
check (skill `observability`).

## Restore into a fresh cluster

1. Apply a manual `Backup` (`method: barmanObjectStore`, `cluster.name:
   postgres-17`) and wait for `phase=completed`.
2. Copy `cluster-17/cluster-17.yaml`. New `metadata.name`. Set
   `bootstrap.recovery.source` and `externalClusters[]` to the previous
   `serverName`. Set `backup.barmanObjectStore.serverName` to a new value so
   the archives stay distinct.
3. Add a sibling Flux Kustomization that `dependsOn` the existing cluster
   for the cutover window.
4. Move each app by editing the host hardcoded in its ExternalSecret
   (`postgres-17-rw.database.svc.cluster.local:5432`), then remove the old
   cluster after no client backends remain.

`barman-cloud-backup-list` needs the AWS keys from `cloudnative-pg-secret`
passed into the postgres pod. Do not print them. The off-site mirror ships
suspended; see `offsite-mirror/README.md`.
