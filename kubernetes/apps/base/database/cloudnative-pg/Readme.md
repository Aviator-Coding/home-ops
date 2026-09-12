# cloudnative-pg

CNPG operator + the canonical `postgres-17` cluster, plus pgAdmin and the upstream Grafana dashboards.

## What lives here

| Subdir | Purpose |
| ------ | ------- |
| `operator/` | CNPG operator HelmRelease (chart `0.29.0`, app `1.30.0`). 2 replicas, PodMonitor enabled. Secret `cloudnative-pg-secret` (postgres superuser + MinIO S3 keys for Barman). |
| `cluster-17/` | The `postgres-17` Cluster CR (Postgres 17 + pgvecto.rs), its `ScheduledBackup` (`@daily`), `LoadBalancer` Service, Gatus probe, PrometheusRule (7 alerts), and Barman config (serverName `postgres17-v5`, MinIO bucket `s3://home-ops-postgres-cluster/`). |
| `dashboard/` | OCI Helm chart `ghcr.io/cloudnative-pg/grafana-dashboards/cluster:0.0.5`. Sidecar-loaded into Grafana under the "Database" folder (alongside the Dragonfly operator dashboard). |
| `pgadmin/` | pgAdmin 4 web UI behind Authentik OAuth at `pgadmin.${SECRET_DOMAIN}` and `pg.${SECRET_DOMAIN}`. Kopiur-only backup (ceph 4-hourly / r2 daily at hour 07). VolSync retired 2026-09-04. |
| `offsite-mirror/` | CronJob that copies the `postgres-17` Barman archive (LAN MinIO -> Cloudflare R2), giving it a second, off-site backup copy. CNPG can only express one live barman destination, so this mirrors the archive rather than adding a second `ScheduledBackup`. **Ships `suspend: true` - installed but NOT running**; it cannot run until the `cloudnative-pg-r2` 1Password item exists, and un-suspending is a deliberate one-line follow-up. Details, credential and known follow-ups: `offsite-mirror/README.md`. |

## Cluster overview

- `postgres-17` runs 3 instances across talos-1/2/3 with required pod-anti-affinity on hostname.
- Storage: 100 GiB ceph-block per instance.
- Image: `ghcr.io/tensorchord/cloudnative-pgvecto.rs:17.5-v0.4.0` (PG 17 + pgvecto.rs vector extension).
- Apps consume the cluster via `postgres-17-rw.database.svc.cluster.local:5432`. Each app's ExternalSecret hardcodes that host explicitly (not via OnePassword templating) — see the comment in any per-app `externalsecret.yaml`.

## Common operations

### Manual backup

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  name: manual-backup-postgres-17-YYYYMMDD
  namespace: database
spec:
  cluster:
    name: postgres-17
  method: barmanObjectStore
```

```sh
kubectl apply -f manual-backup.yaml
kubectl wait --for=condition=Completed backup.postgresql.cnpg.io/manual-backup-postgres-17-YYYYMMDD -n database --timeout=30m
kubectl get backup.postgresql.cnpg.io -n database
```

### List Barman backups in MinIO

The `cloudnative-pg-secret` doesn't expose AWS env vars to the postgres pod, so credentials must be passed in for ad-hoc `barman-cloud-*` calls:

```sh
AWS_KEY=$(kubectl get secret cloudnative-pg-secret -n database -o jsonpath='{.data.aws-access-key-id}' | base64 -d)
AWS_SEC=$(kubectl get secret cloudnative-pg-secret -n database -o jsonpath='{.data.aws-secret-access-key}' | base64 -d)
kubectl exec -n database postgres-17-1 -c postgres -- bash -c \
  "AWS_ACCESS_KEY_ID='$AWS_KEY' AWS_SECRET_ACCESS_KEY='$AWS_SEC' \
   barman-cloud-backup-list --cloud-provider aws-s3 \
   --endpoint-url https://nas.${SECRET_DOMAIN}:9000 \
   s3://home-ops-postgres-cluster postgres17-v5"
```

### Recover from backup into a fresh cluster

Use `bootstrap.recovery.source` plus `externalClusters[]` to bootstrap a new
cluster from a Barman archive in MinIO. Increment the `serverName` so the
new cluster's archive stays distinct from the old one's, then commit a
sibling Cluster manifest. The current `cluster-17.yaml` is the working
template (`bootstrap.recovery.source: postgres17-v4` +
`externalClusters[postgres17-v4]` → new `serverName: postgres17-v5`).

Steps:

1. Take a manual `Backup` against the running cluster (the procedure above).
   Wait for `phase=completed`.
2. Author a new Cluster manifest — copy `cluster-17.yaml`, change the cluster
   `metadata.name`, set `bootstrap.recovery.source` to the previous server
   name, define `externalClusters[]` with the previous serverName, and bump
   `backup.barmanObjectStore.serverName` to a new value.
3. Add a sibling Flux `Kustomization` in `kubernetes/apps/main/database/cloudnative-pg.yaml` with `dependsOn` pointing
   at the existing cluster (so both run in parallel during cutover).
4. Commit + push. Flux reconciles, CNPG runs `barman-cloud-restore` into the
   new cluster's data dir, then `pg_basebackup` joins additional replicas.
5. Migrate consumers app-by-app (each app's ExternalSecret hardcodes the
   postgres host string; update those, ESO refreshes, roll the deployments).
6. Decommission the old cluster once no client backends remain.

## Maintenance

- **Daily Barman backup** to MinIO via `cluster-17/scheduledbackup.yaml`. Retention 30 days.
- **Off-site mirror** of that archive to R2 is **suspended and does not run yet** - it is installed but switched off until the `cloudnative-pg-r2` credential exists. Once un-suspended it runs hourly, independently of the above; see `offsite-mirror/README.md`.
- **pgAdmin kopiur** is the only backup engine (VolSync retired 2026-09-04): ceph `H 1-23/4 * * *` (component default), r2 `H 7 * * *`. Identity 5050:5050.
- **`reading-glasses` cache prune** runs daily at 03:00 (cluster's k8tz default tz). Manifest lives with the application: `apps/base/downloads/reading-glasses/app/cronjob.yaml`. Keeps `rreading-glasses.public.cache` from growing unbounded; uses the application's own role for least privilege.

## Operational notes

- CronJob `spec.timeZone` is stamped by the `k8tz` admission webhook (`America/New_York`) on CREATE only — do not set it in git, and do not hand-patch it live. Objects that predate the webhook (or later lose the unowned field) keep running in UTC with every GitOps signal green; class, detection, and fix: `docs/admission-webhook-create-only-drift.md`.
- `cluster-17/prometheusrule.yaml` defines the 7 alerts that operate on `cnpg_*` metrics; rules are cluster-wide so they cover any future CNPG cluster too.
- Health gate for the Flux Kustomization is `status.readyInstances >= 1 && ContinuousArchiving == True`, NOT the Ready condition. CNPG can latch the Ready condition False indefinitely while still serving traffic — see the comment in `kubernetes/apps/main/database/cloudnative-pg.yaml`.
- `cluster-17.yaml` keeps both the original `cluster` and renamed `cnpg_cluster` labels in `monitoring.podMonitorMetricRelabelings`. **Do not re-add `{ regex: cluster, action: labeldrop }`** — the bundled CNPG Grafana dashboard's `Cluster` dropdown extracts the legacy `cluster` label via regex and goes empty (every panel "No data") if it's dropped.
- The grafana helm values used to also include a `databases.cloudnative-pg` (gnetId 20417) entry that duplicated this dashboard's UID `cloudnative-pg`. The duplicate locked Grafana's sidecarProvider out of all writes; it has been removed in `apps/base/monitoring/grafana/app/helmrelease.yaml` — don't add it back.
