# postgres-17 off-site backup destination - design and blocking findings

**Date:** 2026-09-12
**Status:** design only. Nothing is implemented; work is parked on a missing credential
(see "What is blocked").

**Goal.** The shared CloudNativePG cluster `postgres-17` keeps its only backup copy on a
single LAN target. Every one of the fleet's 30 file volumes already has both a Ceph copy
and an off-site copy; Postgres is the outlier. This document works out how to give it a
second copy that is genuinely off the LAN.

**Captain's decision this serves (2026-09-11).** Off-site copy only. The captain was
explicitly shown that no CNPG restore has ever been exercised here and chose to close the
single-point-of-failure first and carry that risk knowingly. **A restore drill is out of
scope** and is deliberately not designed, scripted or scheduled anywhere in this document.

## Summary

The obvious shape - "a second `ScheduledBackup` plus an `ObjectStore`/plugin resource" -
**cannot be built on this cluster in a form that produces a restorable off-site copy.**
CloudNativePG has exactly one live barman destination per `Cluster`, and the one mechanism
that looks like it lifts that limit is accepted by the schema, runs, reports success, and
writes to the wrong bucket.

What remains is to copy the barman archive itself off the LAN. That design, its two
consistency requirements, and the single credential it is blocked on are below.

## Background: how this archive is laid out

`barman-cloud` stores an archive under `<destinationPath>/<serverName>/` in two parts:

- `base/<backup-id>/` - the base backup tarballs, plus a `backup.info` metadata file
  written **last**, which is what makes the backup appear in barman's catalogue.
- `wals/<prefix>/<segment>` - the continuous WAL stream, written by a separate command.

Both are required to restore: `barman-cloud-restore` replays the base backup and
`barman-cloud-wal-restore` supplies the WAL needed to reach consistency and to perform
point-in-time recovery. This split is the reason several of the findings below matter.

## What is live today (read-only verified 2026-09-12)

| Fact | Value |
|---|---|
| CNPG operator | `ghcr.io/cloudnative-pg/cloudnative-pg:1.30.0` (`database` ns, 2 replicas) |
| barman-cloud plugin | **not installed** - no `objectstores.barmancloud.cnpg.io` CRD |
| `postgres-17` destinations | exactly one: `s3://home-ops-postgres-cluster/` at `https://nas.${SECRET_DOMAIN}:9000` (the LAN TrueNAS MinIO) |
| serverName | `postgres17-v5` |
| Retention | `30d`, WAL + `@daily` base backup, `target: prefer-standby` |
| Cluster health | `Ready=True`, `ContinuousArchiving=True`, `LastBackupSucceeded=True` |
| Backup history | unbroken daily `completed` run, method `barmanObjectStore` |
| `spec.plugins` | empty |

Manifests: `kubernetes/apps/base/database/cloudnative-pg/cluster-17/`.

## Finding 1 - in-tree barman is single-destination, structurally

`Cluster.spec.backup.barmanObjectStore` is a single object, not a list. There is no
in-tree way to express a second destination. `externalClusters[].barmanObjectStore`
entries exist (this cluster has one, `postgres17-v4`) but they are **read-only recovery
sources**, never write targets.

## Finding 2 - the plugin cannot be a second destination either

The barman-cloud CNPG-I plugin is the documented path forward, since in-tree barman is
deprecated from CNPG 1.26. It does not help here, for two independent reasons.

**2a. Only one object store is ever written.** Quoting the live CRD on this cluster
(`clusters.postgresql.cnpg.io`, `.spec.plugins[].isWALArchiver`):

> Marks the plugin as the WAL archiver. At most one plugin can be designated as a WAL
> archiver. This cannot be enabled if the `.spec.backup.barmanObjectStore` configuration
> is present.

Upstream concepts documentation agrees: one `ObjectStore` per `Cluster`, serving WAL
archiving and base backups, with separate stores only for *recovery source* and *replica
source*. No second live backup destination is described.

**2b. A per-backup `barmanObjectName` is accepted and then silently ignored.** This is the
trap, and it is the reason this document exists. The obvious design - keep the LAN store
in-tree, add an R2 `ObjectStore`, and point a second `ScheduledBackup` at it via
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

`config.NewFromCluster` reads `BarmanObjectName` from the *Cluster's* `spec.plugins` entry
via `NewPlugin(cluster, metadata.PluginName)`. `request.BackupDefinition` and
`request.Parameters` are never consulted. `NewPlugin` also keeps only the **last**
`spec.plugins` entry matching the plugin name, so two barman-cloud entries collapse to one
rather than yielding two destinations.

This is upstream issue [cloudnative-pg#7778](https://github.com/cloudnative-pg/cloudnative-pg/issues/7778),
"ScheduledBackup ignores `barmanObjectName`, sending base backups to the WAL object
store". It was **closed as not planned** after going stale, and was reconfirmed against
CNPG 1.26.1 by another user five months later. It is not fixed on `main` today.

**Read this as a false-green risk, not merely a missing feature.** The `Backup` resource
reaches `completed` against the wrong bucket, so neither the resource status nor a green
CI run distinguishes a working second destination from a broken one. Only listing objects
at the new target does.

## Finding 3 - a base backup without its WAL is not restorable

Even if 2b were fixed upstream, base-backups-only to a second store would not give a
usable copy. `barman-cloud-backup` uploads PGDATA to `base/` and nothing else; the WAL
range generated between `pg_backup_start()` and `pg_backup_stop()` is written separately
to `wals/` by `barman-cloud-wal-archive`. The plugin builds its argument list in
`barman-cloud/pkg/backup`'s `GetBarmanCloudBackupOptions` - `--user`, `--name`,
compression, encryption, `--immediate-checkpoint`, `--jobs`, tags, `--endpoint-url`,
destination, serverName. There is no WAL-bundling flag.

Since WAL archiving is single-destination (2a), a second store would hold base backups
whose WAL range lives only on the LAN, and PostgreSQL would refuse to reach consistency
with `WAL ends before end of online backup`.

**So the off-site copy must contain both `base/` and `wals/`, or it is decorative.**

## Finding 4 - neither existing R2 credential can host this

The fleet's off-site precedent is **Cloudflare R2**, and it is a good one: 30 of 30 file
volumes already have an off-site copy there, so the provider is already funded, wired and
proven. But both live R2 tokens are bucket-scoped.

| 1Password item | Consumer | Scope |
|---|---|---|
| `kopiur-r2` | `system/kopiur` `ClusterRepository` `r2` | bucket `kopiur` **only** - `volsync` returns 403 and `ListBuckets` is denied (verified 2026-08-30, `kubernetes/apps/base/system/kopiur/README.md`) |
| `volsync-template` (`R2_HOME_OPS_*`) | the 3 surviving VolSync `*-r2` sources | bucket `volsync`; broader scope **unverified** |

Neither reaches a Postgres bucket. R2 itself is perfectly capable of hosting a barman
archive - it is S3-compatible and barman addresses it with a plain `--endpoint-url` - so
this is a credential gap, not a provider limitation.

## Options

### Option 1 (recommended) - mirror the barman archive LAN -> R2

A scheduled in-cluster job copies the whole `postgres17-v5` prefix (`base/` **and**
`wals/`) from the LAN MinIO bucket to an R2 bucket.

- Produces a **complete, independently restorable, PITR-capable** off-site archive, which
  is the only one of these options that clears Finding 3.
- **Touches nothing on the live `Cluster`** - no plugin, no `spec.plugins`, no rolling
  restart of the three healthy Postgres instances. This is the main reason to prefer it.
- Restore path is the shape already present in `cluster-17.yaml`: an `externalClusters[]`
  entry whose `barmanObjectStore` points at R2 with the same `serverName`.
- Cost: one small CronJob, one `ExternalSecret`, one R2 bucket.
- Two hard requirements before it ships, worked through in "Mirror consistency" below:
  the sync must be **copy-only**, and the off-site recovery point must be read as **last
  contiguous WAL**.

**Retention.** The LAN target runs WAL + daily base at `30d`, set by CNPG's own
`spec.backup.retentionPolicy`. The recommendation is to **not** mirror deletions, and to
expire on R2 at **35d** via a bucket lifecycle rule. Longer than the LAN's 30d,
deliberately: the five-day margin means a LAN-side prune can never race the mirror into
dropping an object the off-site copy still needs, and an accidental or malicious deletion
on the LAN does not reach the off-site copy the same day. The effective off-site recovery
window remains the intended 30d.

**RPO caveat.** The off-site WAL stream lags by the sync interval rather than being
continuous. At an hourly sync the off-site copy is up to ~1h behind. The LAN copy remains
the primary, continuous one; this is a second copy, not a second primary.

### Option 2 - make R2 authoritative via the plugin, mirror back to the LAN

Install the plugin, move the archive to an R2 `ObjectStore` with `isWALArchiver: true`,
drop `spec.backup.barmanObjectStore`, and mirror R2 -> LAN. Off-site becomes continuous
rather than lagging. Costs a plugin install, a cert-manager dependency, a `Cluster` spec
change, a rolling restart of all three instances, and a migration of the live archive.
Same mirroring machinery as Option 1 with materially more risk to a healthy database, for
a benefit Option 1 approximates at an hourly RPO.

### Option 3 - a second CNPG `Cluster` as a replica cluster archiving to R2

Genuinely CNPG-native and fully independent. Costs another ~100Gi `ceph-block` claim and a
standing Postgres instance, and introduces a distributed topology to maintain. Heaviest
option by a wide margin for the stated goal of "a second copy".

### Rejected - second `ScheduledBackup` + R2 `ObjectStore`

The shape this task originally suggested. Rejected on Findings 2b and 3: it writes to the
LAN store while reporting success, and even once fixed upstream it would produce a
WAL-less, unrestorable archive.

## Mirror consistency

Two questions have to be settled before any mirror ships, because both change the design
rather than merely tune it.

### Can the mirror copy a half-written WAL object?

**No - and the reason is S3 object semantics, not timing luck.**

`barman-cloud-wal-archive` writes each WAL segment as a single object under
`<destinationPath>/<serverName>/wals/`. In S3 an object becomes visible to
`ListObjectsV2` and `GetObject` only once the write completes: a `PutObject` in flight is
neither listable nor gettable, and a multipart upload materialises as an object only at
`CompleteMultipartUpload` - until then its parts are reachable only through
`ListMultipartUploads`, which a mirror does not read. MinIO implements those semantics. So
a sync either sees the finished object or does not see it at all.

This is precisely the property that makes mirroring an S3 archive safe where mirroring a
POSIX directory would not be: there is no torn read available to copy.

The real failure mode is therefore not a corrupt object but an **incomplete set**, in two
shapes, neither of which damages what is already off-site:

- **A base backup caught mid-upload.** Objects under `base/<id>/` appear as they are
  written, while `backup.info` is written last. A sync landing in that window copies a
  directory barman does not yet consider a backup at all, because the catalogue keys off
  `backup.info`. It is inert rather than corrupt, and the next run completes it.
- **A WAL gap from an interrupted run.** If a run dies partway, the off-site copy can hold
  a non-contiguous stretch of segments. PITR needs an unbroken WAL chain, so the honest
  statement of the off-site recovery point is **the last contiguous segment**, not the
  newest object present.

Carry that second point forward: the off-site copy's usable recovery horizon must be
judged by WAL contiguity, never by "the newest object is recent". Proving an actual
recovery from it is restore-drill work, deliberately deferred by the captain on
2026-09-11 and not attempted here.

### Must the sync be copy-only?

**Yes. This is a correctness requirement, not a preference.**

CNPG enforces `retentionPolicy: 30d` by running `barman-cloud-backup-delete` against the
LAN store, which removes aged base backups and the WAL segments no remaining backup needs.
If the sync propagated deletions - `rclone sync`, `mc mirror --remove`, or
`aws s3 sync --delete` - two things would follow:

1. **The 35d off-site window would be fiction.** Objects would be deleted by the sync at
   30d, before the R2 lifecycle rule could ever apply, so R2 retention would silently
   equal LAN retention and the stated margin would not exist.
2. **Every LAN-side destructive event would propagate.** An operator error, a mistaken
   retention change, a bucket wipe or ransomware on the LAN target would reach the
   off-site copy on the next run. A copy that faithfully reproduces the loss it exists to
   survive is a replica, not a backup - and removing exactly that single point of failure
   is the whole point of this task.

So the sync must be additive: `rclone copy`, `mc mirror` without `--remove`, or
`aws s3 sync` without `--delete`. Expiry on R2 is then enforced independently by an R2
bucket lifecycle rule. That independence is what makes the two retentions genuinely
separate rather than one being a shadow of the other.

**Consequence to expect, not a defect.** Copy-only means the R2 catalogue deliberately
diverges from the LAN one, retaining base backups CNPG has already pruned. That is
intended. The ages stay coherent because a base backup and the WAL it depends on are
created at roughly the same time and so expire together under a single lifecycle rule; the
limiting factor on any off-site restore is the age of the base backup, which is what a 35d
window is meant to express. The lifecycle rule should also abort incomplete multipart
uploads, so orphaned parts do not accrue storage.

## What is blocked

One thing, and it is the only thing: **the R2 bucket and its credential.**

No bucket exists for the Postgres archive, and neither live R2 token can reach one
(Finding 4). The credential must arrive through the existing 1Password + External Secrets
path - no hand-made Secret, no credential value in git - so the 1Password item has to be
minted before any manifest can resolve. Until it exists there is nothing useful to build:
a mirror job pointed at a bucket that does not exist, reading an `ExternalSecret` whose
properties are absent, would fail loudly at reconcile and would prove nothing.

What is needed, concretely:

1. An R2 bucket to hold the Postgres archive.
2. An R2 API token that can read and write it, following the per-purpose item convention
   the fleet already uses (`kopiur-r2` is the model: one purpose-built 1Password item per
   destination rather than a shared grab-bag).
3. A 1Password item holding that token's endpoint, access key and secret key, in a vault
   1Password Connect can see - **`Homelab`, `Automation` or `Services`, never `Home-Lab`**,
   which Connect cannot read at all (`AGENTS.md`, 1Password vaults).

Live confirmation that the new destination actually receives data is performed post-merge,
once Flux has reconciled the change, and is not a blocker on designing or implementing it.

## Related

- The restore path for `postgres-17` remains **unproven**. No CNPG restore has ever been
  exercised here; all 16 restore documents in `docs/backups/` are VolSync or kopiur. The
  drill was deliberately deferred by the captain on 2026-09-11 and is out of scope for
  this work.
- File-volume off-site precedent and the R2 credential conventions:
  `kubernetes/apps/base/system/kopiur/README.md`.
- Cluster manifests: `kubernetes/apps/base/database/cloudnative-pg/cluster-17/`.
