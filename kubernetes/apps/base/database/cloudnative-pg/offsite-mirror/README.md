# postgres-17 off-site archive mirror

Copies the `postgres-17` barman archive from the LAN TrueNAS MinIO to Cloudflare R2, so
the cluster's only Postgres backup copy stops being a single LAN target. Every one of the
fleet's file volumes already has both a Ceph copy and an off-site copy; Postgres was the
outlier.

Design, evidence and the rejected alternatives:
[`docs/backups/postgres-offsite-destination-design-2026-09-12.md`](../../../../../../docs/backups/postgres-offsite-destination-design-2026-09-12.md).

## Why a mirror and not a second CNPG destination

CloudNativePG has exactly **one** live barman destination per `Cluster`, and the mechanism
that looks like it lifts that limit is silently broken:

- `spec.backup.barmanObjectStore` is a single object, not a list.
- The live CRD states `isWALArchiver` "cannot be enabled if the
  `.spec.backup.barmanObjectStore` configuration is present", so WAL archiving is
  single-destination regardless of the plugin.
- `plugin-barman-cloud` (v0.15.0 and `main`) resolves the destination **only** from the
  Cluster's `spec.plugins`. A `ScheduledBackup` carrying its own `barmanObjectName` is
  accepted and ignored - it reports `completed` while writing to the LAN store
  ([cloudnative-pg#7778](https://github.com/cloudnative-pg/cloudnative-pg/issues/7778),
  closed as not planned).
- `barman-cloud-backup` bundles no WAL, so a base backup in a store that receives no WAL
  cannot reach consistency on restore even if the above were fixed.

Copying the archive itself moves `base/` **and** `wals/` together, which is what makes the
off-site copy independently restorable, and it changes nothing on the live `Cluster` - no
plugin, no `spec.plugins`, no rolling restart of the three Postgres instances.

## The two rules that are correctness, not tuning

**Copy-only.** `rclone copy` never deletes on the destination. It must never become
`rclone sync`, and no `--delete-*` flag may be added. CNPG enforces its 30d window by
running `barman-cloud-backup-delete` against the LAN store; propagating those deletions
would make the 35d off-site window fiction (objects would go at 30d, before the prune
could apply) and would carry any LAN-side wipe straight to the off-site copy. A copy that
faithfully reproduces the loss it exists to survive is a replica, not a backup.

**Expiry is independent.** The 35d prune is an age-based rule applied to the destination,
never a reflection of what the LAN deleted. It runs only after a successful copy (`set -eu`),
reads the source mtime rclone preserves in object metadata, and refuses to act when the
candidate count exceeds `PRUNE_CEILING`.

## Reading the off-site recovery point

A half-written WAL object **cannot** be copied - an in-flight `PutObject` is neither
listable nor gettable, and a multipart upload becomes an object only at
`CompleteMultipartUpload`. The residual risk is an incomplete *set*: a base backup whose
trailing `backup.info` has not landed (inert, since barman's catalogue keys off that file),
or a WAL gap from an interrupted run.

So the off-site recovery point is **the last contiguous WAL segment**, not the newest
object present. PITR needs an unbroken chain.

## Credential

| | |
|---|---|
| 1Password item | `cloudnative-pg-r2` |
| Vault | `Homelab` (any Connect-visible vault works - `Homelab`, `Automation`, `Services` - but **never** the hyphenated `Home-Lab`, which Connect cannot see at all) |
| Fields | `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` |
| R2 bucket | `home-ops-postgres-cluster` (same name as the LAN bucket, on purpose) |

The endpoint lives in the item rather than in git because it embeds the Cloudflare account
id, the same reason `system/kopiur` keeps it there. The token needs object read/write on
that bucket only - `--s3-no-check-bucket` means no `HeadBucket`/`CreateBucket` call is made.

Neither existing R2 token is reused: `kopiur-r2` is scoped to bucket `kopiur` alone
(`volsync` 403s, `ListBuckets` denied) and `volsync-template`'s `R2_HOME_OPS_*` is scoped
to `volsync`.

Until the item exists the `ExternalSecret` does not resolve and the job's pods stay
unschedulable. **That is the intended state, not a broken manifest** - it is how this repo
expresses a secret that must exist, and it fails closed rather than running without
credentials. Fields are fetched by explicit `data` + `property` so a missing one fails
loudly instead of materializing a blank credential.

## Known follow-ups

- **No `PrometheusRule` yet.** A failed or silently-stopped mirror currently does not
  alert. This is deliberate rather than an oversight: `AGENTS.md` records that a
  PrometheusRule can be structurally incapable of firing while nothing in CI catches it,
  so the rule is to be written and validated against live Prometheus **both ways** (quiet
  now, and returning a series when the comparison is inverted) once the job has actually
  run. That can only happen after the credential lands.
- **The restore path remains unproven.** No CNPG restore has ever been exercised on this
  cluster; all restore documents under `docs/backups/` are VolSync or kopiur. The drill was
  deliberately deferred by the captain on 2026-09-11 and is out of scope here.
