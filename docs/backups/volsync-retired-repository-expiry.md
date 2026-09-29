# Expiring retired VolSync restic repositories

> ## THE RETENTION DESCRIBED HERE IS NOT IN FORCE
>
> **Nothing in this repository applies these rules, and merging does not apply them.** No object
> store has been configured. No object has been deleted, and none is scheduled to be.
>
> This is not a caveat - it is the single most important fact on the page. The declarative path
> that *would* normally carry a change like this **silently ignores it**: Rook's OBC controller
> has no update path, so a `bucketLifecycle` on the 341-day-old `volsync` OBC is accepted into
> Git, renders clean, passes review, and never reaches the bucket. A reader concluding from this
> document that retired backups are expiring would be wrong in the most dangerous direction.
>
> The rules become real only when an operator runs
> [`volsync-retired-expiry-apply-plan.md`](volsync-retired-expiry-apply-plan.md) against each of
> the three object stores. Until then this is a decision on paper.
>
> When it *is* applied, the earliest deletion is **2027-03-31** - about 6.6 months after the
> decision - and the sole-copy tier is **2027-09-30**, about 12.6 months.

**Captain's decision, 2026-09-12: an expiry date. Not deletion now, not keeping it
indefinitely.** A recovery window is preserved, and unbounded growth stops for every future
app retirement. Scope decided the same day, on the corrected measurement below: **all 48
retired repositories across all three destinations**, not the 30 on ceph alone.

This document is the decision record. The apply runbook is
[`volsync-retired-expiry-apply-plan.md`](volsync-retired-expiry-apply-plan.md). The
machine-readable policy is
[`scripts/volsync-retired-expiry/ledger.yaml`](../../scripts/volsync-retired-expiry/ledger.yaml);
[`render_lifecycle.py`](../../scripts/volsync-retired-expiry/render_lifecycle.py) turns it into
S3 lifecycle rules; `scripts/ci/volsync-retired-expiry-test.py` is the gate.

## The decision

| tier | what it is | prefixes | size (all 3 destinations) | expires |
|---|---|---|---|---|
| `redundant` | Retired from VolSync in the kopiur Stage 5 migration. The claim still exists and kopiur backs it up **today on both ceph and r2**, and the fleet is restore-proven on both. | 26 | 96.04 GiB | **2027-03-31** |
| `sole_copy` | App and volume are gone from the cluster entirely. This restic repository is the **only** remaining copy of that data anywhere. | 22 | 30.00 GiB | **2027-09-30** |

**Total reclaimed over time: 126.03 GiB across 48 repositories.** Nothing is reclaimed before
2027-03-31; the recovery window is **~6.5 months** for data that already has two current
backups, and **~12.5 months** for data that has none.


Two dates, not 48. The machine-readable copy is the ledger. Change a date there.

## Why a retention policy cannot do this

Deleting a `ReplicationSource` never touches the restic repository. `restic forget --keep-*`
is relative to the repository's newest snapshot, so a frozen repository keeps at least one
snapshot forever. Expiry is a whole-prefix deletion on an absolute date.
`Expiration.Days` counts from each object's creation time and would delete the frozen
repositories immediately. The gate refuses a `Days` key.

## Exact-segment matching

Repositories are `<bucket>/<name>/`. `volsync/syncthing` (retired) is a string prefix of
`volsync/syncthing-data` (live). A `startswith`, glob, unanchored regex, or bare
`Filter.Prefix` of `syncthing` selects the live objects too.

The ledger stores bare names (`syncthing`, never `syncthing/`). `render_lifecycle.py`
appends the slash in one place and rejects an entry that already contains one. The emitted
match `syncthing/` does not match `syncthing-data/` because the strings differ at the
character after the shared stem (`/` versus `-`).

Protected prefixes, which must never receive a rule: `paperless-ngx`, `paperless-ngx-media`,
`syncthing-data`. The renderer raises `UnsafeLedger` rather than emitting a rule that can
reach one.

Skill `volsync-carveouts`, reference `references/retired-repo-expiry.md`.
