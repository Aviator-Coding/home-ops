# cloudnative-pg

CNPG operator + the canonical `postgres-17` cluster, plus pgAdmin and the upstream Grafana dashboards.

## What lives here

| Subdir | Purpose |
| ------ | ------- |
| `operator/` | CNPG operator HelmRelease (chart `0.28.0`, app `1.29.0`). 2 replicas, PodMonitor enabled. Secret `cloudnative-pg-secret` (postgres superuser + MinIO S3 keys for Barman). |
| `cluster-17/` | The `postgres-17` Cluster CR (Postgres 17 + pgvecto.rs), its `ScheduledBackup` (`@daily`), `LoadBalancer` Service, Gatus probe, PrometheusRule (7 alerts), and Barman config (serverName `postgres17-v5`, MinIO bucket `s3://home-ops-postgres-cluster/`). |
| `dashboard/` | OCI Helm chart `ghcr.io/cloudnative-pg/grafana-dashboards/cluster:0.0.5`. Sidecar-loaded into Grafana under the "Storage" folder. |
| `pgadmin/` | pgAdmin 4 web UI behind Authentik OAuth at `pgadmin.${SECRET_DOMAIN}` and `pg.${SECRET_DOMAIN}`. Triple-redundant volsync backup (Ceph 4h / MinIO 6h / R2 daily). |

## Cluster overview

- `postgres-17` runs 3 instances across talos-1/2/3 with required pod-anti-affinity on hostname.
- Storage: 100 GiB ceph-block per instance.
- Image: `ghcr.io/tensorchord/cloudnative-pgvecto.rs:17.5-v0.4.0` (PG 17 + pgvecto.rs vector extension).
- Apps consume the cluster via `postgres-17-rw.database.svc.cluster.local:5432`. Each app's ExternalSecret hardcodes that host explicitly (not via OnePassword templating) — see the comment in any per-app `externalsecret.yaml`.

## Common operations

### Manual backup

There is a name collision: both CNPG and TiDB-BR define a `Backup` kind. Always qualify the API group when scripting.

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

The cluster's `bootstrap.recovery.source` block plus `externalClusters[]` is how we did the v3 → v4 → v5 chain. Increment the serverName so archives stay distinct, then commit a new sibling Cluster manifest. See `cluster-17.yaml` for the working pattern (`bootstrap.recovery.source: postgres17-v4` + `externalClusters[postgres17-v4]`).

Full procedure documented in:

- `/Users/aviator/.claude/plans/soft-tickling-storm.md` — plan that drove the rebuild.
- `/Users/aviator/AI/Home-Ops/reports/cnpg-audit-20260426.md` — audit + cleanup history.

## Maintenance

- **Daily Barman backup** to MinIO via `cluster-17/scheduledbackup.yaml`. Retention 30 days.
- **pgAdmin volsync** triple-target backup at `0 */4 * * *` (Ceph), `0 */6 * * *` (MinIO), `0 1 * * *` (R2).
- **`reading-glasses` cache prune** runs daily at 03:00 (cluster's k8tz default tz). Manifest lives with the application: `apps/downloads/reading-glasses/app/cronjob.yaml`. Keeps `rreading-glasses.public.cache` from growing unbounded; uses the application's own role for least privilege.

## Operational notes

- All CronJobs in this cluster have their `timeZone` overwritten by the `k8tz` admission webhook to `America/New_York`. Don't bother setting `timeZone` explicitly.
- `cluster-17/prometheusrule.yaml` defines the 7 alerts that operate on `cnpg_*` metrics; rules are cluster-wide so they cover any future CNPG cluster too.
- Health gate for the Flux Kustomization is `status.readyInstances >= 1 && ContinuousArchiving == True`, NOT the Ready condition. CNPG can latch the Ready condition False indefinitely while still serving traffic — see the comment in `ks.yaml`.
