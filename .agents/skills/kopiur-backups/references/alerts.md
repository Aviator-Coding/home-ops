# kopiur alerts

Rules live in `kubernetes/apps/base/system/kopiur/app/prometheusrule.yaml`.
The chart ships its own rules from the HelmRelease; those four vendor alerts
are correct and are not duplicated here. An inert chart rule cannot be edited
from Git (skill `observability`).

## `KopiurProjectedCredentialsLeaking`

The series is a leader-only census, published on the work-spec sweep (default
6h), not by the fast-path reaper. Tag behaviour: a pass that deletes zero
objects logs nothing and only writes the gauge.

A bare `> 0` for a fixed `for:` false-fires when one sweep catches a benign
in-flight census and freezes that reading until the next sweep.

`min_over_time(...[13h]) > 0` requires the value to stay positive across two
sweep passes. A genuine leak never returns to 0, so the next sweep still
catches it. `deriv` does not: a step from 0 to N that then stays flat has
derivative 0.

That per-series `min_over_time` regressed on a leader change. The new leader's
series has no history, so its startup sweep alone decides the minimum, and a
benign census at that moment fires immediately. One later 0 sample clears the
13h minimum at once. The expression aggregates with
`max without (pod, instance)` **before** `min_over_time`. Do not drop that
aggregation. Behavioural pin:
`scripts/ci/kopiur-projected-secrets-leak-alert-test.py`.

`kopiur_repository_breaker_open`, `kopiur_snapshot_gated` and
`kopiur_snapshot_waiting_for_slot` are sparse gauges. They disappear while
healthy. Do not rewrite them onto an always-present neighbour.

## Reading exporter output

Per-run projection evidence is the operator log and
`kopiur_secrets_projected_total`, not the live gauge and not
`credsReapedAt`. See [credentials.md](credentials.md).
