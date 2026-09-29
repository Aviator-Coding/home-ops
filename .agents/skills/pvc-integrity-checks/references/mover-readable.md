# pvc-mover-readable-check

CronJob `system/pvc-mover-readable-check`, every 6 hours at minute 47 so it
does not overlap `pvc-writable-check`. It resolves each engine's mover uid
and gid from the **live** `ReplicationSource` or `SnapshotPolicy` (never the
component default of 1000), then execs `walk.sh` inside a container that
already mounts the claim.

Readable means owner-read, or group-read while in the group, or other-read.
Directories also need the matching execute bit. One untraversable directory
hides the subtree (`ai/hermes` root `0700` `10000:10000` hides the whole
volume from uid 1000).

Pass `-1` for an engine that does not cover the claim. Those counters are
`NA`, not a fake 0.

## Alert versus report

Exit code 1 (the Job fails, the PrometheusRule fires) when a **kopiur**
identity cannot read, or when a walk is `INCONCLUSIVE`.

VolSync unreadables are printed and do not fail the Job. VolSync's writable
staging plus `fsGroup` repairs the clone before restic reads, so the same
mismatch still produces a complete restic backup. kopiur's read-only staging
does not. Alerting both would page on a condition VolSync survives. Reporting
neither would hide the condition that fails kopiur.

Structurally unmeasured claims do not fail the Job either: no running pod,
subPath-only (`ntfy`), RBAC-excluded namespaces. They are logged. They are
not a pass. Do not add a rule that treats "no series" as healthy for this
Job; the Job's own exit code is the signal.

## RBAC

Same split as the writable check, narrower binding.

| Role | Verbs | Binding |
|---|---|---|
| `pvc-mover-readable-check-read` | `pods` get/list, plus get/list on `replicationsources` and `snapshotpolicies` | ClusterRoleBinding |
| `pvc-mover-readable-check-exec` | `pods/exec` create | RoleBinding in `ai`, `downloads`, `home-automation`, `media`, `selfhosted` only |

`database` is not bound. `database/pgadmin` is kopiur-only and is
`UNMEASURED` on every run. That gap is permanent unless a captain decision
widens exec into `database`. Do not widen it inside a backup change.

No `targetNamespace` on the overlay. RoleBindings and the CronJob exclusion
list stay in lockstep. The remote command is always `walk.sh` with the mount
path as its own argv element.

This is arbitrary command execution inside every pod in the five namespaces.
That cost was accepted so the check runs as the identity that actually
mounts the volume. Root on the host would always see "readable".

## The walk traps

All four returned a false-clean zero when the check was built. `walk.sh`
encodes the defences, and `scripts/ci/pvc-mover-readable-check-test.py`
pins them. Do not "simplify" the walker:

1. busybox `find` has no `-uid` or `-gid`. It prints usage and exits. A
   pipeline then yields 0. Ownership is `stat` into `awk`.
2. `/tmp` is read-only in hardened containers. A `2>"$ERRF"` redirect kills
   the walk. No temp file. Errors are tagged `WALKERR|` on the same stream
   via an fd swap.
3. stderr is never discarded. `WALK_ERRORS > 0` is `INCONCLUSIVE`, which
   fails the Job.
4. busybox `stat -c %F` prints `regular empty file` for a zero-byte file. An
   exact `regular file` match drops empty files from both totals. Match by
   prefix.

`lost+found` is `0700 root:root` on ext4. Descending into it is one
`Permission denied` per claim and would mark the fleet `INCONCLUSIVE`. It is
pruned from descent and statted on its own, so it stays counted and is not a
finding.

## Schedules and identity source

Component defaults are not the measurement. Per-claim movers in this fleet
include 0, 1000, 2000, 3002, 5050, 5984 and 10000. The CronJob reads the live
CR. A Git change to `KOPIUR_PUID` that has not been reconciled yet is
invisible to the next run, which is the correct question: can the mover that
will actually run read the volume?

`recyclarr-config` has no long-lived pod. The one-off measure script used
when that claim is up is `scripts/ci/fixtures/recyclarr-measure.sh`. The
CronJob will report it unmeasured most of the day. That is not a failure.
