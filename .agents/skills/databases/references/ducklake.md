# DuckLake

A DuckLake is a Postgres catalog plus Parquet files on S3. Nothing runs in the
cluster for queries: external DuckDB clients on the LAN talk straight to
`postgres-17` and to RGW. Manifests: `kubernetes/apps/base/database/ducklake/app/`.
LAN only. A public path through cloudflared is not built.

## Where the credentials live

Both items are in the 1Password vault `Automation`, written by PushSecrets on
`onepassword-automation` (`deletionPolicy: None`). Nothing is created by hand.

| Item | Fields | Used for |
|---|---|---|
| `ducklake` | `POSTGRES_DB_USER_PASSWORD` | role `ducklake` on database `ducklake` |
| `ducklake-ceph-bucket` | `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY` | bucket `ducklake` on `ceph-objectstore` |

One read-write identity. DuckLake has no permission layer of its own.

## Client

DuckDB 2.0 alpha or later, one version for every client: a 2.0 client migrates
the catalog one way and 1.5 clients cannot read it afterwards.

```sql
INSTALL ducklake; INSTALL postgres; INSTALL httpfs;
CREATE SECRET ducklake_s3 (
  TYPE s3, KEY_ID '<ACCESS_KEY_ID>', SECRET '<SECRET_ACCESS_KEY>',
  ENDPOINT 's3.sklab.dev', URL_STYLE 'path', USE_SSL true, REGION 'us-east-1');
ATTACH 'ducklake:postgres:dbname=ducklake host=postgres-17.sklab.dev user=ducklake password=<POSTGRES_DB_USER_PASSWORD> sslmode=require'
  AS lake (DATA_PATH 's3://ducklake/');
```

`URL_STYLE 'path'` is required: RGW has no `rgw_dns_name`, so `bucket.s3.sklab.dev`
does not route. The endpoint is a bare host, no scheme. Connect to Postgres
directly (`postgres-17.sklab.dev`, the LAN LoadBalancer), not through a pooler.

## TLS

`sslmode=require` encrypts without authenticating the server. The server
certificate is the CNPG-issued one, with `postgres-17.sklab.dev` added through
`certificates.serverAltDNSNames` in `cluster-17.yaml`, so `verify-full` works
with the CNPG CA (`kubectl -n database get secret postgres-17-ca -o jsonpath='{.data.ca\.crt}'`).
That CA is regenerated about every 90 days, so a copied CA goes stale.

The sklab.dev Let's Encrypt certificate was not used as the CNPG server
certificate. CNPG instances and the operator verify the server certificate
against `serverCASecret` for replication and the status endpoint, so the
cluster's internal trust would depend on the public chain. That chain is
already `YR2` under `Root YR` cross-signed by `ISRG Root X1`; the next root
change breaks replication reconnects on all databases. cert-manager also
leaves `ca.crt` empty for ACME certificates. Revisit with a CNPG `Pooler` that
terminates TLS with the sklab.dev certificate if `verify-full` without a
copied CA is needed.

## Cluster side

- Role and database come from the `ducklake-db-init` Job (idempotent; delete the
  Job to re-run it after a password change). The password is generated once by
  an ESO `Password` generator (`refreshPolicy: CreatedOnce`) and pushed to 1Password.
- The bucket claim carries `kustomize.toolkit.fluxcd.io/prune: disabled`. The
  `ceph-bucket` class has `reclaimPolicy: Delete`, so deleting the claim deletes
  every Parquet file. The annotation guards a Flux prune (manifest removed from
  Git, Kustomization deleted). It does not guard `kubectl delete obc` or a
  namespace delete. Quota: 200G, 100000 objects.
- No offsite copy of the Parquet files: the data is test data and reproducible.
  The catalog is covered by the shared cluster's barman backup (offsite mirror
  suspended).
- `ducklake-maintenance` CronJob (weekly `CHECKPOINT`, `expire_older_than`
  30 days, `delete_older_than` 3 days) ships `suspend: true` on
  `duckdb/duckdb:1.5.6`. No 2.0 image is published yet. Do not unsuspend it until
  the image is 2.0.0 and the lake has been recreated on 2.0.0.
- Manual run: run `maintenance.sql` from the app directory with the same DuckDB
  version as the catalog and the client environment (`PG*`, `AWS_*`, and
  `S3_ENDPOINT`). Once the image matches:
  `kubectl -n database create job --from=cronjob/ducklake-maintenance ducklake-maintenance-manual`.
- `rgw_sigv4_insecure` is set on RGW. Multipart Parquet PUTs from DuckDB 2.0
  alpha45672 were proven against it on 2026-10-09. Re-test after a Ceph bump
  that removes the flag (skill `rook-ceph`).
