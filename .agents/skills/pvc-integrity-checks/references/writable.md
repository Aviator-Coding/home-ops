# pvc-writable-check

CronJob `system/pvc-writable-check`. Every 6 hours it lists pods, and for
each container that mounts a PVC it execs `test -w <mountPath>`. A genuine
failure fails the Job. The PrometheusRule alerts on
`kube_job_status_failed`.

## Why it exists

A volume root owned `*:1000` is the stamp of a VolSync restore mover
(`VOLSYNC_PUID` / `PGID` default 1000). If the pod sets no matching
`fsGroup`, kubelet does not fix ownership and the process falls through to
the `other` bits of a `2775` directory. The app stays `Ready`. Two shapes
that shipped empty for months:

- A pod with `runAsUser` / `runAsGroup` set and no `fsGroup` at all.
- A correct `securityContext` under a key the chart does not read
  (`selfhostedPodOptions` on app-template). Helm drops it. `flate` does not
  flag it: app-template's values schema is too loose.

`fsGroup` with `fsGroupPolicy: File` (both Ceph CSI drivers) does re-own
existing content. `OnRootMismatch` skips the walk only when the root already
matches, so the first `fsGroup` is the mismatch that chowns the tree. That
chown persists on the volume after the manifest is reverted.

Prefer fixing the pod spec (skill `app-workloads`) over skipping the check.

## RBAC

| Role | Verbs | Binding |
|---|---|---|
| `pvc-writable-check-read` | `pods` get/list | ClusterRoleBinding, all namespaces |
| `pvc-writable-check-exec` | `pods/exec` create | ClusterRole is **not** cluster-bound. One RoleBinding per covered namespace |

No RoleBinding, and therefore API-server denial, for `rook-ceph`, `database`
and `security`. An exec grant there would be command execution inside Ceph,
the databases, and the secret plumbing. The script also short-circuits those
namespaces so a policy exclusion is not logged as a transient skip.

The overlay Kustomization must not set `targetNamespace`. Flux would rewrite
every RoleBinding's namespace to `system`.

A new namespace that should be scanned needs a RoleBinding **and** an update
to the CronJob's covered-namespace list, in the same change. A missing
binding is logged `SKIP (pods/exec forbidden, RBAC)` and does not page as a
volume bug. That skip is coverage drift. It is not a pass.

## What counts as a failure

Only a clean `test -w` failure (the binary ran and the path was not
writable). Everything else is a skip:

- `volumeMounts[].readOnly: true` (the supported opt-out; live user is
  `flux-system/headlamp`).
- No shell and no standalone `test` binary.
- Scaled to zero, or a claim with no running pod (no row).
- Exec errors, timeouts (RC 124/137/143), and API `Forbidden`, via
  `classify_exec_failure`.

The pod annotation `pvc-writable-check.home-operations.com/skip: "true"` is
honoured and has **zero** users. Do not add one to hide a failure.

Multiple containers on one claim are tested per `(pod, container, mountPath)`.

## Alert

`app/prometheusrule.yaml`. The annotation links
`kubernetes/apps/base/system/pvc-writable-check/app/README.md`. Keep that file
as a short pointer so the link resolves. Detail lives here.
