# Alertmanager routing and the Watchdog

Alertmanager is a standalone app-template release.
`kube-prometheus-stack` sets `alertmanager.enabled: false`. The ServiceMonitor
at `kubernetes/apps/base/monitoring/alertmanager/app/servicemonitor.yaml` must
relabel `job` to `kube-prometheus-stack-alertmanager`. The chart's
`alertmanager.rules` match that job name. The operator default (the Service
name) scrapes successfully and leaves every one of those rules inert.

Config: `kubernetes/apps/base/monitoring/alertmanager/app/config/alertmanager.yml`.
Secret: ExternalSecret `alertmanager-secret` (1Password item `healthchecks-io`
plus the Pushover keys). `url_file` is read on each send, so a ping URL change
does not need a pod restart.

## Severities

| Label | Receiver | Pushover priority | What a human sees |
|---|---|---|---|
| `critical` | critical | 2 (emergency, repeats until ack) | a page |
| `warning`, or no severity label | warning | 0 | a page |
| `info` or `notify` | info | -2 | app history only |

`retry` and `expire` are ignored by Pushover below priority 2. `ttl` is
ignored at priority 2. Resolved notifications are quieter than firing ones.

`notify` is the coder rules' local severity and shares the info route.
`InfoInhibitor` (shipped for this) suppresses `info` in any namespace that has
no warning or critical. That is inhibition, not a silence: an info alert beside
a real warning or critical in the same namespace still pages, because
InfoInhibitor stops firing there. The two upstream rules that match the same
alertname at two severities stay; they almost never match. Label a rule
`warning` when a human must act.

A latched critical at priority 2 re-pages until it clears. Any comparison
against a gauge that sticks needs the recency `unless` in
[rule-traps.md](rule-traps.md).

Critical `group_wait` / `group_interval` / `repeat_interval` in the config are
the expire-and-repeat contract. Do not lengthen `repeat_interval` on the
critical route to "reduce noise".

## Watchdog to Healthchecks.io

`Watchdog` is routed to the `healthchecks-io` webhook receiver
(`repeat_interval: 5m`), matched by alertname because its severity is `none`.
A firing Watchdog is the heartbeat. A resolved one is not, so the receiver
sends only on firing.

The Watchdog route sets `group_wait: 0s`, `group_interval: 5m`, and
`repeat_interval: 5m` on purpose. Inheriting the root route would be
30s/5m/12h. Watchdog is one always-firing alert, so the ping cadence is
`repeat_interval` alone. A grace shorter than that false-alarms on jitter.

The contract is live. Rechecked read-only: ExternalSecret
`monitoring/alertmanager-secret` is `SecretSynced`, and
`healthchecks_ping_url` is 56 bytes. Do not print the URL. The Healthchecks.io
check itself is a captain-owned account: Period 5 minutes, grace at least 15
minutes, matching `repeat_interval`. Only this receiver's send errors are the
dead-man failure. Pushover is independent.

The ExternalSecret leaves `deletionPolicy` unset, which is Retain. An
unresolvable `healthchecks-io` / `PING_URL` reference does not delete or empty
the Secret, so the Pushover keys stay. A missing `url_file` fails only this
receiver: `amtool check-config` still succeeds and Alertmanager still starts.

`InfoInhibitor` is also matched by name, same reason: it must not fall through
onto a severity route and page.
