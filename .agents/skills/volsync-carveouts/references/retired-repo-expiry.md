# Retired VolSync repository expiry (decision record)

> ## THE RETENTION DESCRIBED HERE IS NOT IN FORCE
>
> **Nothing in this repository applies these rules, and merging does not apply them.** No object
> store has been configured. No object has been deleted, and none is scheduled to be.
>
> The declarative path that would normally carry a change like this **silently ignores it**:
> Rook's OBC controller has no update path, so a `bucketLifecycle` on the existing `volsync` OBC
> is accepted into Git, renders clean, passes review, and never reaches the bucket. A reader
> concluding that retired backups are expiring would be wrong in the most dangerous direction.
>
> The rules become real only when an operator runs
> [retired-expiry-apply-plan.md](retired-expiry-apply-plan.md) against each of the three object
> stores, on the owner's explicit go-ahead. Until then this is a decision on paper.
>
> When applied, the earliest deletion is **2027-03-31**; the sole-copy tier is **2027-09-30**.

**Owner's decision, 2026-09-12: an expiry date.** Not deletion now, not keeping it
indefinitely. A recovery window is preserved and unbounded growth stops for every future app
retirement. Scope: **all 48 retired repositories across all three destinations** (ceph, minio,
r2), not the 30 on ceph alone.

`scripts/volsync-retired-expiry/ledger.yaml` is the machine-readable policy (change a date
there). `render_lifecycle.py` turns it into S3 lifecycle rules.
`scripts/ci/volsync-retired-expiry-test.py` is the gate: it pins the NOT-IN-FORCE banner in the
first 24 lines of this file, the exact-segment match, the absence of an `Expiration.Days` key
and the apply-plan flags.

## The decision

| Tier | What | Prefixes | Size across 3 destinations | Earliest delete |
|---|---|---|---|---|
| `redundant` | Retired from VolSync in the kopiur Stage 5 migration. The claim still exists and kopiur backs it up on both ceph and r2 | 26 | 96.04 GiB | 2027-03-31 |
| `sole_copy` | App and volume are gone. This restic repository is the only remaining copy | 22 | 30.00 GiB | 2027-09-30 |

48 repositories, 126.03 GiB. Nothing is reclaimed before those dates even after the apply. The
window is about 6.5 months for data that already has two current backups and about 12.5 months
for data that has none. Two dates, not 48.

`ledger.yaml` marks some prefixes `protected`; those still have a live `ReplicationSource`.
Delete that source first or the render refuses them. Dates in the ledger are absolute. Do not
replace them with a relative keep count. The ledger is the count the apply plan uses; comments
that say four repositories were retired are stale (Stage 5 retired 27 claims, autobrr left
with its app).

## Why a retention policy cannot empty a frozen repo

Deleting a `ReplicationSource` never touches the restic repository. `restic forget --keep-*` is
relative to the repository's newest snapshot, so a frozen repository keeps at least one
snapshot forever. `Expiration.Days` counts from each object's creation time and would delete the
frozen repositories immediately; the gate refuses a `Days` key. Expiry is a whole-prefix
deletion on an absolute date.

## Exact segment, never a prefix

Repositories are `<bucket>/<name>/`. `volsync/syncthing` (retired) is a string prefix of
`volsync/syncthing-data` (live). A `startswith`, glob, unanchored regex or bare `Filter.Prefix`
of `syncthing` selects the live objects too.

The ledger stores bare names (`syncthing`, never `syncthing/`). `render_lifecycle.py` appends the
slash in one place and rejects an entry that already contains one. The emitted match
`syncthing/` does not match `syncthing-data/` because the strings differ at the character after
the shared stem. Protected prefixes, which must never receive a rule: `paperless-ngx`,
`paperless-ngx-media`, `syncthing-data`. The renderer raises `UnsafeLedger` rather than emit a
rule that can reach one.
