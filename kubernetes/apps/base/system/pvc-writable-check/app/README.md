# pvc-writable-check

Every 6 hours this CronJob execs `test -w <mountPath>` in containers that
mount a PVC. The Job fails, and the PrometheusRule fires, when a mount is
not writable. An app can be `Ready`, probed, and Gatus-green while it cannot
write its volume. Nothing else in CI sees that.

Skill `pvc-integrity-checks`, reference `references/writable.md`.

## What a page means

The alert is `kube_job_status_failed` for this CronJob's Job, with a
non-empty `reason`. Read the Job log for `namespace/pod/claim/path`. The
check does not write. It calls `test -w`.

## Limits (these are not passes)

- `pods/exec` is not cluster-wide. Exec is bound per namespace. `rook-ceph`,
  `database`, and `security` are excluded permanently.
- A new namespace that should be checked needs a RoleBinding in lockstep
  with the CronJob's list. The overlay must not set `targetNamespace`.
- Expected read-only is `volumeMounts[].readOnly: true` (headlamp). The skip
  annotation is unused. Do not use it to silence a real failure.
- The mount path is an argv element. Do not shell-interpolate it.
