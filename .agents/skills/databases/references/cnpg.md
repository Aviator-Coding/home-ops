# CloudNativePG

The operator chart tag is the OCIRepository `ref.tag` in
`kubernetes/apps/base/database/cloudnative-pg/operator/helmrelease.yaml`.
The cluster image, instance count, and `serverName` are in
`cluster-17/cluster-17.yaml`. The Readme's chart version is not authoritative.

The clusters back up through the in-tree barman (`barmanObjectStore`), which
CNPG 1.31.0 removes. Renovate never automerges the operator chart or image
(`.renovate/autoMerge.json5`), so an upgrade to 1.31.0 or later is a human
decision that needs a backup path migration first.

## One barman destination

A `Cluster` has one live barman store. A second `ScheduledBackup` that names
another `barmanObjectName` is accepted, runs, reports completed, and writes
to the original store (cloudnative-pg#7778, closed as not planned). The
off-site copy is a mirror of the archive, not a second destination:
`offsite-mirror/README.md`. That CronJob ships `suspend: true` until the
`cloudflare-r2` 1Password item exists. Un-suspending it is a deliberate
follow-up, not a side effect of editing the cluster.

Current archive: `destinationPath: s3://home-ops-postgres-cluster/`,
`serverName: postgres17-v5`, endpoint the NAS MinIO URL, retention `30d`.
Bootstrap recovery source is the previous server name (`postgres17-v4`).
Increment `serverName` on a restore so the new archive does not collide
(archives so far: v3, v4, v5).

Apps use `postgres-18-rw.database.svc.cluster.local:5432`, hardcoded in each
app's ExternalSecret. A cutover updates those strings. It is not a
1Password template.

Three instances, required pod anti-affinity on hostname, 100Gi `ceph-block`
each. One instance must tolerate `home-operations.com/dedicated`
`PreferNoSchedule` or the spread cannot place the third pod.

## TLS

Never give a Cluster `serverTLSSecret` or `serverCASecret`. CNPG instances and the operator
verify the server certificate against `serverCASecret` for replication and the status
endpoint, so the cluster's internal trust would depend on the public chain. The
sklab.dev Let's Encrypt chain is already `YR2` under `Root YR` cross-signed by `ISRG Root X1`;
the next root change breaks replication reconnects on every database of the shared
cluster. cert-manager also leaves `ca.crt` empty for ACME certificates. This was tried for
DuckLake clients and rejected.

`cluster-17` instead adds the LAN name of `postgres-17-lb` to the CNPG-issued certificate
through `certificates.serverAltDNSNames` (`postgres-17.${SECRET_DOMAIN}`), so a LAN client
can use `sslmode=verify-full` with the CNPG CA
(`kubectl -n database get secret postgres-17-ca -o jsonpath='{.data.ca\.crt}'`). That CA is
regenerated about every 90 days, so a copied CA goes stale. `sslmode=require` encrypts
without authenticating the server. Changing the alt-name list reissues the server
certificate on the shared cluster. If `verify-full` without a copied CA is ever needed,
revisit a CNPG `Pooler` that terminates TLS with the sklab.dev certificate.

## postgres-18 (all apps)

`cluster-18/` is the shared Cluster for every app. It runs 3 instances on `openebs-hostpath`
(local NVMe), so Postgres replication plus the WAL archive are the redundancy, not Ceph.
hostpath has no quota; the `NodeFilesystem*` alerts cover `/var`. An instance whose node is
drained stays Pending until that node returns. The image is `postgresql:18-standard-trixie`,
which ships pgvector (`vector`); there are no extension image volumes. Renovate holds it to
major 18 (`.renovate/overrides.json5`). Sync replication is `any 1`, `dataDurability:
preferred`, `failoverQuorum: true`, with `failoverDelay: 30`.

Backups go through the Barman Cloud plugin (`barman-cloud/`, ObjectStore `postgres` in
`cluster-18/objectstore.yaml`), `serverName: postgres18-v2`. v1 is the archive of the empty
cluster it replaced. The plugin renames the backup metrics to
`barman_cloud_cloudnative_pg_io_*`; the backup-age rules accept either name.

It was bootstrapped by a monolith `initdb.import` from postgres-17 with an explicit
database and role list. Retired apps' databases (readarr, rreading-glasses, ducklake, `app`)
and roles (immich, open-webui) were left behind on postgres-17. The `bootstrap` block only
acts on a new Cluster.

Roles and databases are `DatabaseRole`/`Database` CRs from `components/cnpg-database`,
applied by a sibling Flux Kustomization `<app>-db` (targetNamespace `database`) in each
app's overlay. The app's Kustomization `dependsOn` it. Both CRs are `retain`: deleting
them never drops data, so app retirement includes a manual `DROP DATABASE`/`DROP ROLE`.
The role password comes from the app's 1Password item (`POSTGRES_DB_USER_PASSWORD`, or
`CNPG_PASSWORD_KEY`); a change there is applied to the role by CNPG.

postgres-17 stays, untouched and unused, as the rollback target until it is retired.

## Health and metrics

The Flux health gate is `status.readyInstances >= 1` and
`ContinuousArchiving == True`. It is not the Cluster `Ready` condition,
which can stay False while the database serves. The gate is on
`kubernetes/apps/main/database/cloudnative-pg.yaml`.

`monitoring.podMonitorMetricRelabelings` keeps the original `cluster` label
and copies it to `cnpg_cluster`. Do not add
`{regex: cluster, action: labeldrop}`. The bundled Grafana dashboard's
Cluster variable reads `cluster`. Dropping it empties every panel.

Do not re-add the gnetId 20417 dashboard. It duplicated UID `cloudnative-pg`
and locked Grafana's sidecar out of writes.

The `gatus.io/enabled` ConfigMap under `cluster-18/` is not loaded by Gatus.
Skill `observability`. Do not treat that file as a live check.

Backup-age rules in `cluster-18/prometheusrule.yaml`: standbys export
`cnpg_collector_last_available_backup_timestamp` as 0, so require
`timestamp > 0` joined to `cnpg_pg_replication_in_recovery == 0`. A primary
that reports 0 or drops the series is the `*TimestampAbsent` pair
(`in_recovery == 0` unless a positive timestamp on the same pod). The
archiver series is primary-only.

## Restore shape

1. Manual `Backup` (`method: barmanObjectStore`) against the running cluster.
   Wait for `phase=completed`.
2. New Cluster manifest: new `metadata.name`, `bootstrap.recovery.source`
   set to the previous `serverName`, matching `externalClusters[]`, and a
   new `backup.barmanObjectStore.serverName`.
3. Sibling Flux Kustomization that `dependsOn` the existing cluster during
   cutover. That overlay file is not edited from an apps-only change unless
   the restore is the task.
4. Move consumers by editing each ExternalSecret host, then decommission
   the old cluster after no client backends remain.

`barman-cloud-backup-list` needs the AWS keys from `cloudnative-pg-secret`
passed into the postgres pod. The secret is not mounted as AWS env. Do not
print the keys.

pgAdmin is kopiur-only. Its identity and schedule live on the pgAdmin
overlay, not in this note.
