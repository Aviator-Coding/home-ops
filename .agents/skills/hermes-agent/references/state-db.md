# Hermes state.db

`/opt/data/state.db` is the session store and it dominates the claim. The
values below were checked against `app/resources/config.yaml` and the overlay's
`KOPIUR_CAPACITY: 40Gi`. The historical measurements (a 9.31 GiB file, a 25Gi
claim at 76% full, ~175-235 MB/day, ~98% `cron` transcripts, FTS storing each
message three times) are why the keys exist. Git history holds the tables.

## The three keys

In `sessions:` of `resources/config.yaml`:

| Key | Required shape | Why |
|---|---|---|
| `auto_prune` | explicit `true` | Call sites read `.get("auto_prune", False)`. Upstream's default is the only thing that turns it on. An upstream flip with the key absent stops all pruning and nothing in this repo notices. |
| `vacuum_after_prune` | `false` | See below. Independent of how large the claim is. |
| `retention_days` | integer, `0 < n < 90` | 90 is upstream's default and was measured not to fit. 30 is the current value. 21 or 14 need no CI edit. Removing the key deep-merges back to 90. |

`retention_days` is the one dial. Upstream has no per-source retention (cron
kept shorter than human chat), but a session can be exempted permanently by
pinning it.

Gate: `scripts/ci/hermes-state-db-retention-test.py`. It asserts the shape,
not a frozen `retention_days` literal, and that `hermes-configmap` still
sources `resources/config.yaml`. The `copy-config` initContainer copies that
file onto the PVC on every start, so an in-pod `hermes config edit` does not
survive.

## Why vacuum stays off

VACUUM is the only operation that returns freed pages to the filesystem. It
rewrites every page through the WAL, so it needs free space on the order of
the database file itself.

`last_vacuum` is absent from `state_meta`. Hermes treats `since_vacuum` as
`None`, the `min_vacuum_interval_days` throttle never engages, and the
post-prune freelist ratio clears the auto-vacuum gate. At upstream's `true`
default a VACUUM is attempted on **every** prune pass that deletes rows,
writes until ENOSPC, and rolls back. Filling the volume stops Hermes
persisting anything.

Pruning still bounds growth: freed pages sit on the freelist and SQLite
reuses them, so the file plateaus instead of shrinking. That is the steady
state. Reclaiming space is an offline, operator-driven
`hermes sessions optimize-storage` (this database is on legacy FTS layout 0)
and it needs free space first. Do not flip the key to get that reclaim.

The prune sweep itself runs at startup only, at most once per 24h. The bound
is enforced at pod-restart cadence.

## Capacity

The git claim size is 40Gi (`KOPIUR_CAPACITY` on the overlay). Writing a new
number there does not resize the live PVC: `components/kopiur/pvc` is
`IfNotPresent`. Confirm the live size with
`kubectl -n ai get pvc hermes` before any arithmetic.

`hermes/app/prometheusrule.yaml` compares
`predict_linear` of `kubelet_volume_stats_used_bytes` to the **live**
`kubelet_volume_stats_capacity_bytes` for `persistentvolumeclaim="hermes"`.
It does not encode 25Gi or 40Gi. A comment in that file that still says 25Gi
is stale; the expression is not.

A full volume stops a single RWO writer. There is no second replica to fail
over to.
