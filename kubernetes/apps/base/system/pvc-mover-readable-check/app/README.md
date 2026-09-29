# pvc-mover-readable-check

Every 6 hours, at minute 47, this CronJob checks whether each backup mover
can read the claim it backs up. Identity comes from the live
`ReplicationSource` or `SnapshotPolicy`, never from the component default.

The Job fails (and the PrometheusRule fires) when a **kopiur** identity
cannot read, or when a walk is `INCONCLUSIVE`. VolSync unreadables are
printed and do not fail the Job: VolSync stages writable and `fsGroup`
repairs the clone before restic reads. kopiur stages read-only and fails
closed.

Skill `pvc-integrity-checks`, reference `references/mover-readable.md`.

## What a page means

Read the Job log. `UNMEASURED` is not a pass: no running pod, a subPath-only
mount (`ntfy`), or an RBAC exclusion. `database/pgadmin` is a permanent gap.
The walk binds `pods/exec` only in `ai`, `downloads`, `home-automation`,
`media`, and `selfhosted`.

`recyclarr-config` has no long-lived pod. The one-off measure script is
`scripts/ci/fixtures/recyclarr-measure.sh`. The CronJob reports that claim
unmeasured most of the day. That is not a failure.

Do not shell-interpolate the mount path. The four walk traps (no `find -uid`,
no `/tmp` redirect, never discard stderr, prefix-match `regular empty file`)
and the `lost+found` prune are in `walk.sh`. Reintroducing any of them
returns a silent false-clean zero.
