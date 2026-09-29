# Schedules and timezone

## Bare `H` only

kopiur rejects Jenkins range syntax (`H(0-19)`). The webhook error is
`CronPattern contains illegal character 'H'`. Separation from VolSync is the
**hour** field, not a bounded minute. Never hand-assign minutes. `H` is hashed
from the schedule identity, and `jitter: 5m` decorrelates the fire time.

## Hours

`KOPIUR_SCHEDULE_CEPH` stays at the component default `H 1-23/4 * * *`: odd
hours 01, 05, 09, 13, 17, 21. VolSync ceph uses the even hours.

`KOPIUR_SCHEDULE_R2` must be set per namespace. The component default
`H 4 * * *` would put every r2 policy in the 04:00 hour. One free hour per
namespace, pinned by `EXPECTED_R2_HOUR` in
`scripts/ci/kopiur-stage3-test.py`:

| Namespace | r2 hour |
|---|---|
| database | 07 |
| home-automation | 10 |
| downloads | 11 |
| selfhosted | 14 |
| media | 15 |
| ai | 19 |

## Timezone pin

k8tz injects `TZ=America/New_York` into both engines. VolSync's scheduler
honours that process TZ. kopiur ignores it and evaluates cron as UTC unless
`spec.schedule.timezone` is set.

Both `SnapshotSchedule` templates pin
`timezone: ${KOPIUR_SCHEDULE_TIMEZONE:-America/New_York}`. That pin is what
keeps the one-hour stagger constant across DST. Without it, the odd/even
offset holds only while the UTC offset is a multiple of the 4-hour period
(EDT, UTC-4) and collides when the offset becomes UTC-5. Do not remove the
pin, and do not move k8tz into `system` (skill `system-namespaces`): VolSync
would lose the injected TZ and shift every remaining `ReplicationSource`.

A comment in `snapshotschedule.yaml` that says the unset-timezone stagger
"accidentally still" works is the reason the pin exists. The pin is set. Leave
it.

## GFS and on-demand snapshots

Creating a `Snapshot` pushes an older one past retention. Scheduled snapshots
carry `deletionPolicy: Delete` and the `snapshot-cleanup` finalizer.

- ceph `keepHourly: 6` absorbs one extra snapshot by retiring an hourly slot.
- r2 is `keepDaily` / `keepWeekly` / `keepMonthly` with **no** `keepHourly`.
  An on-demand r2 snapshot evicts that day's previously newest snapshot.

Prefer restoring from an existing scheduled snapshot. Create a verification
snapshot only when destination identity needs a matched pair, and expect it to
cost the oldest snapshot in the tier it lands in. A "`Snapshot` census
unchanged" check is not a safety invariant: schedules and retention move the
count continuously. The invariant is that every snapshot that disappeared is
attributed.

`concurrencyPolicy: Forbid`: a `Snapshot` left `Running` blocks every later
scheduled backup for that claim. Do not leave one behind.
