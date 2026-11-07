# CloudNativePG

Tripwires, the single barman destination, and the health gate live in skill
`databases` (`references/cnpg.md`). The operator chart tag is the
OCIRepository `ref.tag` in `operator/helmrelease.yaml`, not a version written
here. The `gatus.io/enabled` ConfigMap in `cluster-18/` is not a live Gatus
check (skill `observability`).

## Restore into a fresh cluster

postgres-18 is the only Cluster (postgres-17 was retired 2026-11-07). The
rebuild-in-place shape is "Restore shape" in `references/cnpg.md`.

1. Apply a manual `Backup` (`method: plugin`, `cluster.name: postgres-18`) and
   wait for `phase=completed`.
2. Copy `cluster-18/cluster-18.yaml`. New `metadata.name`. Set
   `bootstrap.recovery.source` and the plugin `externalClusters[]` to the
   previous `serverName`. Set the archiver `plugins[].parameters.serverName` to
   a new value so the archives stay distinct.
3. Add a sibling Flux Kustomization that `dependsOn` the existing cluster
   for the cutover window.
4. Move each app by editing the host hardcoded in its ExternalSecret
   (`postgres-18-rw.database.svc.cluster.local:5432`), then remove the old
   cluster after no client backends remain.

`barman-cloud-backup-list` needs the AWS keys from `cloudnative-pg-secret`
passed into the postgres pod. Do not print them. The off-site mirror ships
suspended; see `offsite-mirror/README.md`.
