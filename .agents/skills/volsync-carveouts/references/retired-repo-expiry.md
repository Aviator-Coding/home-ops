# Retired VolSync repository expiry

> **NOT IN FORCE.** No bucket lifecycle is applied. No object has been deleted.
> Merging Git does not change that.

Rook's ObjectBucketClaim controller has no update path. A `bucketLifecycle`
field on the existing `volsync` OBC is accepted into Git and never reaches the
bucket. The only way the decision becomes real is an operator running
`docs/backups/volsync-retired-expiry-apply-plan.md` against each destination,
with an explicit go-ahead. r2 additionally needs a Cloudflare token in the
`Workers R2 Storage Write` group. The in-cluster token is object-scoped and
gets `AccessDenied`.

Until that run, this is a decision on paper. The human banner that CI checks
is the top of `docs/backups/volsync-retired-repository-expiry.md`. The apply
plan stays as written. It has not been executed.

## Why a retention policy cannot empty a frozen repo

Deleting a `ReplicationSource` does not touch restic. restic `--keep-*` tiers
are all relative to the repository's **newest** snapshot, so on a frozen
repository every policy keeps at least one snapshot forever.

`Expiration.Days` is also wrong: it counts from each object's creation time
and would delete the whole prefix immediately.

Expiry is a whole-prefix deletion on a date.

## Exact segment, never a prefix

Repositories are `<bucket>/<APP>`. `volsync/syncthing` (retired config claim)
is a strict string prefix of `volsync/syncthing-data` (live). A `startswith`,
a glob, an unanchored regex or a bare S3 `Filter.Prefix` of `syncthing`
selects the live objects too. Match `<name>/`, which cannot reach a longer
segment.

`scripts/volsync-retired-expiry/ledger.yaml` is the machine-readable policy.
`render_lifecycle.py` turns it into lifecycle rules.
`scripts/ci/volsync-retired-expiry-test.py` pins the NOT-IN-FORCE banner, the
exact-segment match, and the apply-plan flags.

## The decision, when someone applies it

| Tier | What | Prefixes | Size across 3 destinations | Earliest delete |
|---|---|---|---|---|
| `redundant` | VolSync retired, claim still exists, kopiur backs it up on ceph and r2 | 26 | 96.04 GiB | 2027-03-31 |
| `sole_copy` | App and volume are gone. This repository is the only remaining copy | 22 | 30.00 GiB | 2027-09-30 |

48 repositories, 126.03 GiB, three destinations (ceph, minio, r2). Not "30 on
ceph". Nothing is reclaimed before those dates even after the apply.

`ledger.yaml` marks some prefixes `protected`. Those still have a live
`ReplicationSource`. Delete that source first or the render refuses them.
Dates in the ledger are absolute. Do not replace them with a relative keep
count.

## What "four retired" used to mean

Comments that say four repositories were retired are stale. Stage 5 retired
VolSync from 27 claims; autobrr then left with its app. The ledger is the
count that the apply plan uses. Do not recompute it from an old sentence.
