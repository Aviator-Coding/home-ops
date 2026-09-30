# Hermes restarts

Treat every restart as an unclean exit. That includes a config byte, a
runtime-skill byte, a Reloader rollout, and a node drain.

## What rolls the pod

- `kustomization.yaml`'s `configMapGenerator` entries for `resources/config.yaml`
  and `skills/homelab-commit-watcher/`. Any byte changes the hash.
- `reloader.stakater.com/auto: "true"` on the controller. Secrets projected
  into env are read at process start. Without this annotation a rotated
  token stays stale until something else restarts the pod. Do not remove it.
- A comment-only edit to either payload rolls the pod like any other change:
  batch such edits and land them alone.

## Why the exit is unclean

`gateway-default` is registered directly in `/run/service`. It is not an
s6-rc service and not a legacy `/etc/services.d` service. On container
SIGTERM, PID 1 runs `s6-linux-init-shutdown` and `s6-linux-init-shutdownd`
is started with `-g 3000` (3 seconds, then SIGKILL).

The gateway's own shutdown wants up to 65s for the cron ticker and 35s for
housekeeping before `mark_exited`. It never gets that long. Raising
`terminationGracePeriodSeconds` does nothing. `S6_SERVICES_GRACETIME` only
covers the empty legacy-services path. The `-g` value is baked into the
image. Do not reach for either lever.

## The integrity-check loop

An unclean exit leaves `state/gateway.lifecycle.json` at `phase=running`.
The next boot runs `lifecycle_ledger.check_state_db_integrity`
(`PRAGMA quick_check(1)` over the whole of `state.db`). Unlike the backup
helper, this call has no size ceiling, no timeout, and no progress lease.

`record_startup` rewrites the sentinel only after the check returns, and
the watchdog's `mark_exited` will not touch a sentinel owned by another
pid. If the check is killed mid-flight, the next boot repeats it. That
loop has no exit until a human deletes the sentinel or the watchdog runway
outlasts the check.

`HERMES_STARTUP_WATCHDOG_TIMEOUT_S: "900"` makes the runway `4 x timeout`
(3600s). The 6Gi memory limit stops the check re-reading most of `state.db`
off Ceph. Both live on the `app` container in the HelmRelease. Do not drop
either to "save" resources.

## What looks healthy while this is happening

The pod stays `Running` with `RESTARTS 0`. Gatus stays green. All three
probes are TCP on dashboard port `9119`, and the `dashboard` s6 service is
independent of `gateway-default`. No probe in this manifest can see the
gateway. s6 respawns the service inside a container that never restarts, so
`kubectl get pods` shows nothing.

Read `/opt/data/logs/gateway-startup-watchdog.log` and the `gateway.start`
cadence in `gateway-exit-diag.log`.

Do not add a liveness probe on the gateway. A legitimate post-unclean-exit
boot is many minutes, and a probe recreates the kill loop one level up.
