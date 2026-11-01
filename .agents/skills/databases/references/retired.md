# Retired database apps: FalkorDB and DuckLake

Both apps were removed from Git on 2026-11-01 (owner decision, issue #2201). No consumer
existed in the repo. **The data was kept on purpose**; deleting it is a separate owner
step. `git revert` of the retirement PR restores manifests only, never data once a
deletion step below has run. Same shape as skill `ai-stack`
`references/retirement.md`.

## What was kept

| State | Where it is now | Why it survived the prune | Delete it with (destroys data) |
|---|---|---|---|
| FalkorDB data PVC `database/falkordb` (20Gi `ceph-block`) | kept, unmounted, no longer backed up | `retain: true` rendered `helm.sh/resource-policy: keep`; the claim is in no Flux inventory | `kubectl -n database delete pvc falkordb` (the PV `reclaimPolicy: Delete` destroys the RBD image) |
| FalkorDB kopia snapshots, `ceph` and `r2` repositories | kept, frozen (no GFS pruning once the policy is gone) | kopiur `onPolicyDelete` / `onScheduleDelete: Retain` | expire per snapshot with kopia. Keep r2 indefinitely: it is the only copy once the PVC is gone |
| FalkorDB `Snapshot` CRs | garbage-collected by the cascade; a count of 0 is the success path | none needed | never delete one by hand while its policy exists (skill `kopiur-backups` `retirement-and-removal.md`) |
| DuckLake bucket `ducklake` on `ceph-objectstore` (Parquet files, 200G quota), OBC Secret and ConfigMap | kept | OBC annotation `kustomize.toolkit.fluxcd.io/prune: disabled` | `kubectl -n database delete obc ducklake` (`ceph-bucket` is `reclaimPolicy: Delete`: bucket and files go) |
| DuckLake catalog: database and role `ducklake` on `postgres-17` | kept | created by the db-init Job, not a CNPG `Database` CR or managed role | `DROP DATABASE ducklake; DROP ROLE ducklake;` via `kubectl cnpg psql postgres-17 -n database`; barman backups age out by retention |
| 1Password `ducklake`, `ducklake-ceph-bucket` (vault `Automation`) | kept | the PushSecrets were `deletionPolicy: None` | delete after the bucket and role are gone |
| 1Password `falkordb` (hand-made, the ExternalSecret source) | kept | ESO never deletes a source item | delete in 1Password |
| In-cluster Secrets `falkordb-secret`, `ducklake-secret`, `ducklake-db-password` | deleted (ESO `Owner`) | values live in 1Password | none |
| DNS `falkordb`, `falkordb-browser` (UniFi) | removed by unifi-dns `policy: sync` | none | check for hand-made leftovers with no TXT twin |
| LB IP `10.50.0.24` | released, free in the BGP pool | none | none |
| `cluster-17` `serverAltDNSNames: postgres-17.${SECRET_DOMAIN}` | unchanged | general LAN name; editing the list reissues the shared cluster's certificate | none |
| Authentik ReferenceGrant `database` entry | unchanged, no consumer now | harmless; the file is under `security/` | optional separate issue |

The expected alert after the prune is `KopiurPolicyVanished` (warning) for
`database/falkordb-ceph` and `database/falkordb-r2`. It clears about 24 hours later.

## Reviving FalkorDB

Restore into a new PVC from the retained snapshot (or reattach the kept `falkordb` claim)
**before** the app writes an empty volume. Skill `kopiur-backups` for restore cache sizing
(about 6.2 GiB restore plateau, so a 10Gi cache). The previous manifests are at commit
`ef86b67c`, including `scripts/ci/falkordb-durability-test.py`.

- `maxmemory-policy noeviction`. The graph is one Redis key, so any eviction policy deletes
  it at the ceiling instead of refusing writes.
- `--maxmemory` strictly below `limits.memory` (it was `12gb` against a 16Gi limit, 0.75).
  At or above the limit the kernel OOM-kills first and every restart replays the AOF.
  Move the limit, `--maxmemory` and the alert ratios (0.6 warning, 0.7 critical) together.
  `requests.memory` is the resting set (7Gi), not the ceiling.
- AOF on with `--appendfsync everysec`. kopiur snapshots with no application hook; one
  mid-write snapshot restored 2174 of 2199 nodes with AOF and 200 of 2199 on the image's
  stock RDB-only settings, and both started clean.
- The data claim keeps `retain: true` (a Helm uninstall must not delete the volume) and
  `forceRename: falkordb` (a second persistence key would rename it to `falkordb-data`
  and orphan the SnapshotPolicy). Chart-owned claim, not `components/kopiur/pvc`.
- Escape every shell `$VAR` as `$${VAR}`: Flux postBuild runs strict. Skill
  `flux-substitution`. Run `exec redis-server` as PID 1 and quote `--requirepass`.
- Readiness is `redis-cli -e GRAPH.LIST` with `REDISCLI_AUTH`. Without `-e`, redis-cli exits
  0 on NOAUTH.
- Strategy `Recreate` (RWO claim; two servers must not open one AOF). Pod options go under
  `defaultPodOptions`, a sibling of `controllers`; any other spelling is discarded.
  Identity 1000:1000, `fsGroup` 1000. The module aborts without a writable `/tmp`.
- The browser sidecar takes only `AUTH_SECRET` and `ENCRYPTION_KEY` (sha256 of the
  password with two salts, 64 hex) by `secretKeyRef`. It needs two emptyDirs (`/app/.data`
  and `<workingDir>/.data`), `AUTH_URL`, `ALLOWED_ORIGINS`, `AUTH_TRUST_HOST=true`,
  `HOSTNAME=0.0.0.0` and `BROWSER=0`. Route hostname `falkordb-browser` behind a
  SecurityPolicy to the Authentik outpost; the LoadBalancer `falkordb-lb` owned
  `falkordb.${SECRET_DOMAIN}` at `10.50.0.24`, port 6379 only. `database` must be on the
  Authentik ReferenceGrant.
- Add the kopiur component only, never `components/volsync`. Re-add the claim to
  `EXPECTED_IDENTITY` and `NEVER_VOLSYNC` in `scripts/ci/kopiur-stage3-test.py` (1000:1000;
  `test_coverage_is_exact` forces it). Measure the mover identity first.
- Do not front Redis with a Gateway. The browser hostname differs from `falkordb.${SECRET_DOMAIN}`
  because unifi-dns `policy: sync` flaps one name with two targets. Run the browser image
  entrypoint with `exec`, not `node server.js`.
- Keep the `--save` dumps as a second copy, and do not raise `auto-aof-rewrite-percentage`
  (the rewrite fork is the trigger; a rarer rewrite lengthens replay). The ceiling must stay above
  `used_memory` of the loaded graph (GraphBLAS matrices included), or `GRAPH.QUERY` (`denyoom`)
  refuses every write while the pod looks healthy.
- The browser `workingDir` is `/var/lib/falkordb/browser` and `PORT` is 3000 (token storage path
  resolves relative to it). A root `fix-permissions` init chowns the emptyDirs the browser
  chmods, so kopiur `SecurityContextCompatible` is absent (not a failure).

## Reviving DuckLake

A DuckLake is a Postgres catalog plus Parquet files on S3. Nothing ran in the cluster for
queries: external DuckDB clients on the LAN talked straight to `postgres-17` and RGW. The
Parquet files were reproducible test data with no offsite copy.

- One DuckDB version for every client. A 2.0 client migrates the catalog one way and 1.5
  clients cannot read it afterwards.
- Credentials: 1Password `ducklake` (`POSTGRES_DB_USER_PASSWORD`, role `ducklake`) and
  `ducklake-ceph-bucket` (`ACCESS_KEY_ID`, `SECRET_ACCESS_KEY`), vault `Automation`.
- Keep `kustomize.toolkit.fluxcd.io/prune: disabled` on the bucket claim. It guards a Flux
  prune only, not `kubectl delete obc` or a namespace delete.
- The role password is an ESO `Password` generator with `refreshPolicy: CreatedOnce` and
  `symbols: 0`. Without `CreatedOnce` ESO re-mints it hourly, the PushSecret overwrites the
  1Password item and the role keeps the old password. The db-init Job (carrying
  `kustomize.toolkit.fluxcd.io/force: enabled`, a Job template is immutable) resets the
  role to the stored password, so a revival mints a new one and clients must re-read it.
- The overlay depended on `postgres-cluster-17`, `rook-ceph-cluster`, `onepassword-store` and
  waited (`wait: true`) on the db-init Job. The bucket PushSecret reads the OBC Secret `ducklake`.
  The password has `symbols: 0` because it travels in libpq env and ATTACH strings.
- The `ducklake-maintenance` CronJob ships `suspend: true` (`CHECKPOINT` deletes data files
  for good; retention was `expire_older_than` 30 days and `delete_older_than` 3 days; run
  `maintenance.sql` by hand with the catalog's DuckDB version; and the 1.5.6 image does not match a 2.0 catalog). Keep it suspended until the
  image is 2.0.0 and the lake was recreated on 2.0.0.
- Client:

  ```sql
  INSTALL ducklake; INSTALL postgres; INSTALL httpfs;
  CREATE SECRET ducklake_s3 (
    TYPE s3, KEY_ID '<ACCESS_KEY_ID>', SECRET '<SECRET_ACCESS_KEY>',
    ENDPOINT 's3.sklab.dev', URL_STYLE 'path', USE_SSL true, REGION 'us-east-1');
  ATTACH 'ducklake:postgres:dbname=ducklake host=postgres-17.sklab.dev user=ducklake password=<POSTGRES_DB_USER_PASSWORD> sslmode=require'
    AS lake (DATA_PATH 's3://ducklake/');
  ```

  `URL_STYLE 'path'` is required (RGW has no `rgw_dns_name`). Connect to Postgres directly,
  not through a pooler. `rgw_sigv4_insecure` must stay set until the Ceph bump that removes
  it (skill `rook-ceph`). TLS and the alt name: [cnpg.md](cnpg.md#tls).
