# postgres-17 off-site backup destination - design and blocking findings

**Date:** 2026-09-12
**Task:** add a second, off-site barman destination to the shared CNPG cluster `postgres-17`.
**Captain's decision this implements:** off-site copy only; the restore drill is
deliberately deferred and is explicitly NOT part of this work.

## Summary

The task as framed - "a second `ScheduledBackup` plus an `ObjectStore`/plugin resource" -
**cannot be built on this cluster in a form that produces a restorable off-site copy.**
CloudNativePG has exactly one live barman destination per `Cluster`, and the one mechanism
that looks like it lifts that limit is silently broken upstream.

This document records the measurement so the next agent does not re-derive it, and states
the three options that remain.

## What is live today (read-only verified 2026-09-12)

| Fact | Value |
|---|---|
| CNPG operator | `ghcr.io/cloudnative-pg/cloudnative-pg:1.30.0` (`database` ns, 2 replicas) |
| barman-cloud plugin | **not installed** - no `objectstores.barmancloud.cnpg.io` CRD |
| `postgres-17` destinations | exactly one: `s3://home-ops-postgres-cluster/`, `https://nas.${SECRET_DOMAIN}:9000` (LAN TrueNAS MinIO) |
| serverName | `postgres17-v5` |
| Retention | `30d`, WAL + `@daily` base backup, `target: prefer-standby` |
| Cluster health | `Ready=True`, `ContinuousArchiving=True`, `LastBackupSucceeded=True` |
| Backup history | unbroken daily `completed` run, method `barmanObjectStore` |
| `spec.plugins` | empty |

## Finding 1 - in-tree barman is single-destination, structurally

`Cluster.spec.backup.barmanObjectStore` is a single object, not a list. There is no
in-tree way to express a second destination. `externalClusters[].barmanObjectStore`
entries exist (this cluster has one, `postgres17-v4`) but they are **read-only recovery
sources**, never write targets.

## Finding 2 - the plugin cannot be a second destination either

The barman-cloud CNPG-I plugin is the documented path forward (in-tree barman is
deprecated from 1.26). It does not help here, for two independent reasons.

**2a. Only one object store is ever written.** Quoting the live CRD on this cluster
(`clusters.postgresql.cnpg.io`, `.spec.plugins[].isWALArchiver`):

> Marks the plugin as the WAL archiver. At most one plugin can be designated as a WAL
> archiver. This cannot be enabled if the `.spec.backup.barmanObjectStore` configuration
> is present.

Upstream concepts documentation agrees: one `ObjectStore` per `Cluster`, serving WAL
archiving and base backups, plus separate stores only for *recovery source* and *replica
source*. No second live backup destination is described.

**2b. A per-backup `barmanObjectName` is accepted and then silently ignored.** This is
the trap. The obvious design - keep the LAN store in-tree, add an R2 `ObjectStore`, and
point a second `ScheduledBackup` at it via
`pluginConfiguration.parameters.barmanObjectName` - **passes schema validation, runs, and
reports `completed`, while writing to the LAN store.**

The CNPG-I protocol does transmit the per-backup configuration
(`cnpg-i/proto/backup.proto`, `message BackupRequest`):

```proto
// This field is REQUIRED. Value of this field is the JSON
// serialization of the Backup that is being taken
bytes backup_definition = 2;

// This field is OPTIONAL. Value of this field is the configuration
// of this backup as set in the Backup or in the ScheduledBackup object
map<string, string> parameters = 3;
```

The reference plugin ignores both. `internal/cnpgi/instance/backup.go` at `v0.15.0`
(latest, released 2026-09-03) and on `main` resolves the destination only from the
Cluster:

```go
configuration, err := config.NewFromClusterJSON(request.ClusterDefinition)
...
if err := b.Client.Get(ctx, configuration.GetBarmanObjectKey(), &objectStore); err != nil {
```

and `config.NewFromCluster` reads `BarmanObjectName` from the *Cluster's* `spec.plugins`
entry via `NewPlugin(cluster, metadata.PluginName)`. `request.BackupDefinition` and
`request.Parameters` are never consulted. `NewPlugin` also keeps only the **last**
`spec.plugins` entry matching the plugin name, so two barman-cloud entries collapse to
one rather than giving two destinations.

This is upstream issue [cloudnative-pg#7778](https://github.com/cloudnative-pg/cloudnative-pg/issues/7778),
"ScheduledBackup ignores `barmanObjectName`, sending base backups to the WAL object
store". It was **closed as not planned** after going stale, and was reconfirmed against
CNPG 1.26.1 by another user five months later. It is not fixed on `main` today.

**Read this as a false-green risk, not merely a missing feature.** The `Backup` resource
reaches `completed` against the wrong bucket. Acceptance criterion 3 of this task - prove
the new destination actually receives data - is precisely the check that catches it.

## Finding 3 - a base backup without its WAL is not restorable

Even if 2b were fixed, base-backups-only to a second store would not give a usable copy.
`barman-cloud-backup` uploads PGDATA to `base/` and nothing else; the WAL range generated
between `pg_backup_start()` and `pg_backup_stop()` is written separately to `wals/` by
`barman-cloud-wal-archive`. The plugin builds its argument list in
`barman-cloud/pkg/backup`'s `GetBarmanCloudBackupOptions` - `--user`, `--name`,
compression, encryption, `--immediate-checkpoint`, `--jobs`, tags, `--endpoint-url`,
destination, serverName. There is no WAL-bundling flag.

A restore therefore needs `barman-cloud-restore` *and* `barman-cloud-wal-restore` against
the same store. Since WAL archiving is single-destination (2a), a second store would hold
base backups whose WAL range lives only on the LAN - PostgreSQL would refuse to reach
consistency with `WAL ends before end of online backup`.

So the off-site copy must contain **both** `base/` and `wals/` or it is decorative.

## Finding 4 - neither existing R2 credential can host this

The fleet's off-site precedent is **Cloudflare R2**, and it is a good one: 30 of 30 file
volumes already have an off-site copy there. But both live R2 tokens are bucket-scoped.

| 1Password item | Consumer | Scope |
|---|---|---|
| `kopiur-r2` | `system/kopiur` `ClusterRepository` `r2` | bucket `kopiur` **only** - `volsync` returns 403 and `ListBuckets` is denied (verified 2026-08-30, `kubernetes/apps/base/system/kopiur/README.md`) |
| `volsync-template` (`R2_HOME_OPS_*`) | the 3 surviving VolSync `*-r2` sources | bucket `volsync`; broader scope **unverified** |

Neither reaches a Postgres bucket. Creating the bucket and minting or widening a token is
a Cloudflare-account action, and under this task's read-only contract it is the captain's
to make - see "What is blocked" below.

## Options

### Option 1 (recommended) - mirror the barman archive LAN -> R2

A scheduled in-cluster job copies the whole `postgres17-v5` prefix (`base/` + `wals/`)
from the LAN MinIO bucket to an R2 bucket.

- Produces a **complete, independently restorable, PITR-capable** off-site archive.
- **Touches nothing on the live `Cluster`** - no plugin, no `spec.plugins`, no rolling
  restart of the three Postgres instances. This is the whole reason to prefer it.
- Restore path is the shape already in `cluster-17.yaml`: an `externalClusters[]` entry
  whose `barmanObjectStore` points at R2 with the same `serverName`.
- Cost: one small CronJob, one `ExternalSecret`, one R2 bucket.
- Weakness, stated plainly: a mirror is not air-gapped. If the sync deletes what the
  source deleted, a LAN-side accident propagates. Mitigated by copying **additively**
  (never propagate deletes) and giving R2 its own lifecycle expiry.

**Retention.** The LAN target runs WAL + daily base at `30d`, set by CNPG's own
`spec.backup.retentionPolicy`. Recommendation is to **not** mirror deletions and instead
expire on R2 at **35d** via a bucket lifecycle rule. Slightly longer than the LAN's 30d,
deliberately: it keeps a five-day margin so a LAN-side prune can never race the mirror
into deleting an object the off-site copy still needs, and it means an accidental or
malicious deletion on the LAN does not reach the off-site copy within the same day. The
effective off-site recovery window stays the intended 30d.

**RPO caveat.** The off-site WAL stream lags by the sync interval rather than being
continuous. At an hourly sync the off-site copy is up to ~1h behind the LAN copy. The LAN
copy remains the primary, continuous one; this is a second copy, not a second primary.

### Option 2 - make R2 authoritative via the plugin, mirror back to the LAN

Install the plugin, move the archive to an R2 `ObjectStore` with `isWALArchiver: true`,
drop `spec.backup.barmanObjectStore`, mirror R2 -> LAN. Off-site becomes continuous
rather than lagging. Costs a plugin install, a cert-manager dependency, a `Cluster` spec
change and a rolling restart of all three instances, plus a migration of the live archive.
Same mirroring machinery as Option 1 with materially more risk to a healthy database, for
a benefit (continuous off-site WAL) that Option 1 approximates at an hourly RPO.

### Option 3 - a second CNPG `Cluster` as a replica cluster archiving to R2

Genuinely CNPG-native and fully independent. Costs another ~100Gi `ceph-block` claim and
a standing Postgres instance, and introduces a distributed topology to maintain. Heaviest
option by a wide margin for the stated goal of "a second copy".

### Rejected - second `ScheduledBackup` + R2 `ObjectStore`

The literal shape the task suggested. Rejected on Findings 2b and 3: it writes to the LAN
store while reporting success, and even once fixed upstream it would produce a WAL-less,
unrestorable archive.

## What is blocked

Two things are not the implementing agent's to do.

1. **R2 bucket and credential.** A bucket for the Postgres archive does not exist, and
   neither live R2 token can reach one (Finding 4). Acceptance criterion 2 requires the
   credential to arrive through the existing 1Password + External Secrets path with no
   hand-made secret, so the item must be minted by the captain before any manifest can
   resolve.
2. **Live proof that the new destination receives data** (acceptance criterion 3). Under
   this task's read-only cluster contract, the change can only take effect by merging and
   letting Flux reconcile it. It cannot be demonstrated from a branch, and a green CI run
   is explicitly not evidence for this criterion.

## Related

- Restore path for `postgres-17` remains **unproven** - no CNPG restore has ever been
  exercised here; all 16 restore documents in `docs/backups/` are VolSync or kopiur. The
  drill was deliberately deferred by the captain on 2026-09-11 and is out of scope here.
- File-volume off-site precedent: `kubernetes/apps/base/system/kopiur/README.md`.
