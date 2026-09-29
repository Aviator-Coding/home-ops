# CloudNativePG

The operator chart tag is the OCIRepository `ref.tag` in
`kubernetes/apps/base/database/cloudnative-pg/operator/helmrelease.yaml`.
The cluster image, instance count, and `serverName` are in
`cluster-17/cluster-17.yaml`. The Readme's chart version is not authoritative.

## One barman destination

A `Cluster` has one live barman store. A second `ScheduledBackup` that names
another `barmanObjectName` is accepted, runs, reports completed, and writes
to the original store (cloudnative-pg#7778, closed as not planned). The
off-site copy is a mirror of the archive, not a second destination:
`offsite-mirror/README.md`. That CronJob ships `suspend: true` until the
`cloudnative-pg-r2` 1Password item exists. Un-suspending it is a deliberate
follow-up, not a side effect of editing the cluster.

Current archive: `destinationPath: s3://home-ops-postgres-cluster/`,
`serverName: postgres17-v5`, endpoint the NAS MinIO URL, retention `30d`.
Bootstrap recovery source is the previous server name (`postgres17-v4`).
Increment `serverName` on a restore so the new archive does not collide
(archives so far: v3, v4, v5).

Apps use `postgres-17-rw.database.svc.cluster.local:5432`, hardcoded in each
app's ExternalSecret. A cutover updates those strings. It is not a
1Password template.

Three instances, required pod anti-affinity on hostname, 100Gi `ceph-block`
each. One instance must tolerate `home-operations.com/dedicated`
`PreferNoSchedule` or the spread cannot place the third pod.

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

The `gatus.io/enabled` ConfigMap under `cluster-17/` is not loaded by Gatus.
Skill `observability`. Do not treat that file as a live check.

Backup-age rules in `cluster-17/prometheusrule.yaml`: standbys export
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
