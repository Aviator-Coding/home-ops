# pvc-writable-check

A cluster-wide sweep that catches an app that cannot write to its own mounted
volume. A 2026-08-30 audit of underused claims found this bug independently
in both `downloads/autobrr` and `selfhosted/rsshub-playwright`: each pod's
`securityContext`/`fsGroup` didn't match its volume's ownership, so the
container's UID fell through to the volume's "other" permission bits and
could never write. Both apps ran `Ready`, passed every probe, stayed
Gatus-green, and had VolSync back up the resulting empty volume on schedule
for months. The only symptom was an empty volume; the only direct evidence
was one `permission denied` log line that one of the two apps happened to
print at startup and the other did not. Nothing in the existing stack -
manifest validation, HelmRelease status, pod readiness, Gatus - checks that a
container can actually write to what it mounts.

## What it does

Every 6 hours, a `CronJob` in `system`:

1. Lists every pod in the cluster and, for every container `volumeMount` that
   backs onto a `PersistentVolumeClaim`, decides whether to test it.
2. For each one it tests, execs `test -w <mountPath>` directly in that
   container - a single non-mutating `access()`/`stat()`-style check, not a
   write attempt.
3. Exits non-zero if any mount comes back genuinely not writable, printing
   exactly which namespace/pod/claim/path failed.

kube-state-metrics already exposes `kube_job_status_failed`/`_succeeded` for
every Job it sees, including CronJob-created ones, with no extra config - so
the Job's own exit code becomes the alert signal via `prometheusrule.yaml`,
with no custom exporter, textfile collector, or Pushgateway needed.

## Why this shape

- **Cron + PrometheusRule**, not a webhook or admission check, because the
  bug is about runtime filesystem permissions (actual UID/GID vs. actual
  volume ownership), which only exists once the pod is running - a manifest
  or admission-time check can't see it (this is exactly why `flate` and
  HelmRelease status both missed the real bugs: `selfhostedPodOptions` is
  legal YAML, and Helm silently drops the unknown key).
- **A single cluster-wide sweep, not a per-app CronJob**, because a
  per-app/per-namespace check has to be added by hand for every new app -
  exactly the "hand-maintained list that rots" this task was asked to avoid.
  Discovery is driven off live pod specs via the Kubernetes API, so a new app
  is covered automatically the next time the sweep runs, with zero YAML
  changes.
- **kube-state-metrics' existing Job metrics, not a new metric or exporter**,
  because it already tracks exactly the signal needed (did this Job succeed)
  for every Job in the cluster with zero additional code.

## Why cluster-wide `pods/exec`

Testing write access from the actual container's identity is the entire
point - a check that ran as root on the host (e.g. reading kubelet's volume
directories directly) would always see "writable" regardless of the app's
real UID/GID/fsGroup, because root bypasses the exact permission check this
is trying to catch. That leaves `exec`-ing into the container as the only way
to ask the real question, and "every PVC-mounting workload, not a
hand-maintained list" rules out a per-namespace `Role`/`RoleBinding` (RBAC has
no namespace-selector concept, so that would just move the rot from an app
list to a namespace list).

This makes `pvc-writable-check` the only `ServiceAccount` in this repo with a
**cluster-wide** `pods/exec: create` grant. Every existing precedent
(`ai/hermes`'s `hermes-exec-deploy`, `rook-ceph/rook-ceph-backup`) scopes
`pods/exec` to a single namespace, and the one existing cluster-wide
read-heavy `ClusterRole` in the repo (`ai/toolhive/mcp-servers/kubectl-mcp`)
explicitly excludes exec/proxy-style subresources even though it grants
almost everything else. This is a real, deliberate increase in blast radius:
a bug or compromise in this Job's container gets arbitrary command execution
in any pod in the cluster, including `rook-ceph`, `database`, and `security`.
Mitigations in place, none of which make the grant itself smaller:

- The `ClusterRole` grants nothing else - no `secrets`, no `get`/`list` on
  any other resource, no verbs beyond `get`/`list` on `pods` and `create` on
  `pods/exec`.
- No other workload references this `ServiceAccount` or mounts its token.
- The command run is always `test -w <mountPath>` (or the `sh -c` fallback
  below); the path comes from the cluster's own pod specs and is always
  passed as a separate `argv` element, never spliced into a shell string, so
  a pod that put shell metacharacters in its own `mountPath` cannot inject
  anything.
- The container is hardened the same as every other workload in this repo:
  non-root, read-only root filesystem, all capabilities dropped, no privilege
  escalation.
- The Job runs for a few minutes every 6 hours, not continuously.

If this tradeoff isn't acceptable, the only real alternative that still
covers every workload without a hand-maintained list is not building this
check at all and relying on the `test -w`-on-first-boot pattern some images
already log (as autobrr does) - which is exactly the gap this task exists to
close, since browserless (rsshub-playwright's container) never logs it.

## Declaring "expected read-only"

Two ways, in order of preference:

1. **`volumeMounts[].readOnly: true`** on the container - the standard
   Kubernetes way to say "this mount is read-only," already skipped
   automatically. No app in this repo uses it today (checked live 2026-08-30:
   0 of 78 PVC volumeMounts across the cluster set it), but any app that
   mounts a claim read-only for real should set it - it is enforced by
   kubelet, not just documentation.
2. **`pvc-writable-check.home-operations.com/skip: "true"` pod annotation** -
   an escape hatch for the rarer case where a claim can't be expressed as
   read-only at the `volumeMount` level (e.g. an app whose privileged
   initContainer does the only write, and the main container only reads).
   Skips every PVC mount on that pod. In use by exactly one app today,
   `flux-system/headlamp` (see "Measured against the live cluster" below for
   why) - add it elsewhere only when a real app hits this case, with a
   comment explaining why.

## Awkward cases, and how each is actually handled

| Case | Handling |
|---|---|
| Containers without a shell (distroless/scratch) | Try the standalone `test` binary first (no shell involved at all); if that's missing, retry via `sh -c` with the path as a positional parameter; if `sh` is also missing, log a `SKIP (no shell or test binary)` line and move on. Verified live against six genuinely shell-less containers in this cluster (`flux-system/konflate`, `database/surrealdb`, `monitoring/loki`, `monitoring/prometheus`, `monitoring/tempo`, `downloads/readarr`'s `exporter` sidecar - the last mounts two claims, so seven check rows total) - all skip cleanly, none raise a failure. |
| Declared read-only mounts | Skipped via `volumeMounts[].readOnly` before any exec is attempted - see above. |
| Scaled-to-zero apps | Discovery is pod-driven: a claim with no running pod produces zero rows and is never mentioned, let alone flagged. |
| CronJob-owned claims that only exist briefly | Same mechanism: by the time the sweep runs, most such Job pods have already completed and won't appear as `Running`, so they produce no row. If the sweep happens to overlap a live run, the container-running filter still applies per-container, and a container that exits mid-check is caught by the transient-error branch below, not treated as a failure. |
| Claims with no pod at all (orphaned, like `selfhosted/rsshub`) | Same as scaled-to-zero: no row, no mention. This check is about workloads, not claims, by design - an orphaned claim with no pod is a different problem (see the audit report's recommendation to delete it) that a writability check can't meaningfully speak to. |
| A pod being deleted/rescheduled mid-check, or any other kubectl/API-level exec error | Recognized by pattern (`error:`, `cannot exec into a container`, `unable to upgrade connection`, `container not found`) and logged as `SKIP (exec unavailable, transient)`, never as a failure. Only a clean process exit with code 1 and none of those error prefixes - i.e. `test -w` actually ran and said no - counts as `NOT WRITABLE`. |
| Multiple containers/mounts on the same claim | Tested independently per (pod, container, mountPath) tuple, since different containers in the same pod can run as different UIDs against the same claim (e.g. `readarr`'s `app` and `exporter` containers). |

## Measured against the live cluster (2026-08-30/31)

Not a simulation: the full manifest set (`ServiceAccount`, `ClusterRole`,
`ClusterRoleBinding`, `CronJob`, `PrometheusRule`) was applied directly to the
live cluster pre-merge, run via
`kubectl create job --from=cronjob/pvc-writable-check`, and deleted again
afterward, leaving no trace. `kubectl auth can-i` confirmed the deployed
`ServiceAccount` (not a debugging credential) can `create pods --subresource
exec` and `list pods` cluster-wide and nothing else. 77-88 PVC `volumeMount`s
across 9 namespaces at any given sweep (the range is real - VolSync mover
pods and Job-owned pods come and go between runs).

**First real run found three bugs, not two:**

- `selfhosted/rsshub-playwright` - flagged `NOT WRITABLE`, `command
  terminated with exit code 1`, while its `selfhostedPodOptions` typo was
  still live. The bug this check was built to catch, caught on the first
  in-cluster run.
- `downloads/autobrr` - already `WRITABLE` by the time of the in-cluster run:
  a separate, concurrently-running task fixing these two apps had already
  landed its fix live. Earlier in this same investigation, before that fix
  took effect, `kubectl exec ... -- test -w /config` against the
  then-current `autobrr` pod independently returned `command terminated with
  exit code 1` - the identical mechanism this check uses, confirming it
  would have caught this one too.
- `flux-system/headlamp` - a **previously undocumented third instance** of
  the same bug class, found by this check on its very first real run: no
  `fsGroup` ever declared, volume owned `root:root 0755`, main container
  uid 100/gid 101 falls through to "other" (`r-x`) and can never write to
  `/build/plugins`. Investigated read-only (`id`, `stat`, `test -w` in the
  running pod) before deciding what it meant - turned out to be
  correctly-designed, not a bug: a `runAsUser: 0` initContainer
  (`headlamp-plugins`) does the one-time write and exits 0; the main
  container only ever reads. Fixed by adding this check's own escape hatch
  (`pvc-writable-check.home-operations.com/skip: "true"`) to headlamp's pod
  annotations, with a comment recording why - not a manifest change to
  either of the two apps this task was told not to touch. The skip
  annotation was itself verified live: applied via a temporary `kubectl
  patch` to `flux-system/headlamp`, confirmed it dropped out of the
  `FAILURES` list on the next run while `rsshub-playwright` stayed flagged,
  then reverted (the deployed diff was never merged as of that patch, so
  Flux would have reverted the live edit on its own on the next reconcile
  regardless).
- Two containers were confirmed genuinely shell-less during design
  (`flux-system/konflate`, `database/surrealdb`). The in-cluster run found
  four more not identified during design (`monitoring/loki`,
  `monitoring/prometheus`, `monitoring/tempo`, `downloads/readarr`'s
  `exporter` sidecar) - all six containers (seven check rows, since the
  `readarr` exporter mounts two claims) skipped cleanly, zero false
  positives from any of them.
- The `readOnlyRootFilesystem: true` hardening on the check's own container
  broke it on the first in-cluster attempt: this bash build implements a
  `<<<` here-string for a payload this size (cluster-wide pod JSON, multi-MB)
  by writing an actual temp file, which fails outright with "cannot create
  temp file for here-document" when `/tmp` is read-only. Found, fixed
  (switched to a pipe and to `< <(process substitution)`, neither of which
  touch disk - see the comments in `cronjob.yaml`), and re-verified in-cluster,
  all before merge.

**False-positive rate after the headlamp fix: zero**, across every target
in two full in-cluster runs (77 and 88 candidates respectively) - every
writable mount reported `WRITABLE`, every declared-shell-less container
skipped cleanly, and the only two `NOT WRITABLE` results across both runs
were the one genuine bug still live (`rsshub-playwright`) and, before its own
fix, `headlamp`.

## What's not proven

- The Prometheus alert firing end-to-end in Alertmanager was not exercised
  pre-merge - that requires a real failed Job to persist for the full
  `for: 10m` window, which is only true once this ships and either a
  regression occurs or `rsshub-playwright` is still broken when the first
  scheduled (non-manual) run lands.
- The 6-hour schedule and 10-minute `for:` window are reasoned choices (cheap
  enough to run often; long enough to not page on a single transient Job
  retry), not empirically tuned - there is no historical data yet on how
  often this fires in practice.
- This sweep covers PVC-mounting **containers**; it does not and cannot
  check `initContainers` (headlamp's own `headlamp-plugins` initContainer,
  for example, is never a row in the sweep - only the main container's
  mount of the same claim is). An initContainer that itself can't write to a
  claim it needs to populate is a real instance of this bug class that this
  check does not see. Not implemented here because every PVC-mounting
  initContainer found live in this cluster (during this investigation) turns
  out to run as `runAsUser: 0` specifically to do that write, making the
  check moot for every current case - worth adding if a future app's
  initContainer needs write access without running as root.
