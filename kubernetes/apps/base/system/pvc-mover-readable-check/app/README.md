# pvc-mover-readable-check

A sweep that catches a backup mover that cannot **read** the volume it is
backing up. It is the deliberate sibling of
[`pvc-writable-check`](../../pvc-writable-check/app/README.md), which catches an
app that cannot **write** its own volume - same CronJob shape, same split-role
RBAC design, same 6h cadence, same `kube_job_status_failed` alert path, no new
framework.

A 2026-08-31 fleet readability audit found `selfhosted/changedetection-config`
holding **2,292 of 3,058 files unreadable** to kopiur's mover identity while
every status surface in the cluster was green. kopia fails **closed** on the
first unreadable file, so an identity mismatch means no backup at all - but
nothing recurring looked for it. That audit was human-directed and one-off; its
own conclusion was that the problem is *detectable* rather than auditable, and
recommended exactly this check. Full context and the per-claim numbers:
`kubernetes/components/kopiur/Readme.md` "SecurityContextCompatible" and the
`docs/backups/` restore-drill and readable-check documents.

## What it does

Every 6 hours, a `CronJob` in `system`:

1. Resolves the covered claim set and **each engine's mover identity from the
   live CRs** - `ReplicationSource.spec.restic.moverSecurityContext` for VolSync
   and `SnapshotPolicy.spec.mover.podSecurityContext` for kopiur. Never from the
   components' defaults: `KOPIUR_PUID`/`PGID` are overridden per claim and range
   from `0` to `10000` across the fleet, so a default-derived identity would
   measure the wrong thing and report a false clean.
2. Picks, per claim, a running container that mounts the **volume root** (a
   `subPath` mount can only ever see a slice of the volume, so it can never
   stand in for one).
3. Pipes [`walk.sh`](./walk.sh) into that container over `kubectl exec` and runs
   one read-only `find` + `stat` pass, classifying every entry against both
   identities. **Readable** means owner with owner-read, or in the owning group
   with group-read, or other-read; **directories additionally need execute**,
   because one untraversable directory hides its whole subtree.
4. Exits non-zero if any claim has a kopiur finding or could not be enumerated.

The walk never writes, chmods or chowns anything, so it is safe against live
application data. `kube-state-metrics` already exposes `kube_job_status_failed`
for every Job, so the sweep's exit code becomes the alert with no exporter.

## Alert on kopiur, report-only for VolSync

**This asymmetry is the whole reason the check is worth having, and it is not a
style choice.** VolSync mounts its staged clone **writable** with
`fsGroupChangePolicy` unset, so the kubelet's recursive `fsGroup` walk rewrites
ownership and permissions on every mount *before* restic opens anything. kopiur
mounts its staged source `readOnly: true`, and the kubelet applies no `fsGroup`
fixup to a read-only mount, so kopia sees the original modes.

The same mismatch is therefore **invisible to VolSync and fatal to kopiur**.
That is measured, not assumed: `ai/hermes`'s volume root is `700 10000:10000`,
so at VolSync's uid 1000 the mover cannot even enter it - and restic still
processed all 89,305 files.

So:

* **kopiur counts > 0 → the Job fails and the alert fires.** kopiur is the
  engine that survives the VolSync retirement (migration Stage 5), and it has no
  rescue.
* **VolSync counts are collected, printed in a `VOLSYNC REPORT-ONLY` block, and
  never affect the exit code.** They are what restic would face *without* the
  fsGroup rescue. They are informational **today** and become actionable the
  moment that staged mount stops being writable - which is why they are measured
  and printed rather than skipped.

## Unmeasured is never passing

A claim the sweep could not measure is reported in its own `UNMEASURED` block
with its reason and its own counter, and is explicitly **not** counted as clean.
Structural, permanent gaps do not fail the Job - they would otherwise fire the
alert on every run forever - so the block is the signal, and it must be read.

| Reason | Live example | Handling |
|---|---|---|
| Namespace excluded from `pods/exec` RBAC | `database/pgadmin` | API-server denial; script short-circuits first so it is not mislabelled transient |
| No pod mounts the claim | `downloads/recyclarr-config` (a `@daily` CronJob claim; its pod exists ~18s/day) | `UNMEASURED (no pod mounts this claim)` |
| Every mount is a `subPath` | `selfhosted/ntfy` (only `cache` and `lib` are mounted; the volume root is not visible from any pod) | `UNMEASURED (... no container can see the volume root)` |
| Container has no `sh`/`find`/`stat`/`awk` | none today | `UNMEASURED (container lacks ...)` |
| Walk hit an error (a subtree could not be enumerated) | none today | `INCONCLUSIVE` - **fails the Job**, because zero counts from a partial walk prove nothing |

For the two structurally unmeasurable claims there is a documented one-off
procedure that does not need a running pod: snapshot the claim, restore it into
a scratch PVC, and walk the copy - `docs/backups/recyclarr-config-readable-check-2026-08-31.md`.

## The four silent-false-clean traps

Each of these makes the check report "all clear" while measuring **nothing**.
All four were hit for real while building the audit; three of them produced
false zeros that were only caught by validating the instrument against known
numbers. They are handled in [`walk.sh`](./walk.sh) and each has a regression
test in `scripts/ci/pvc-mover-readable-check-test.py`.

1. **busybox `find` has no `-uid`/`-gid`.** It prints usage text to stderr and
   exits, so the pipeline yields `0`. Ownership is evaluated in `awk` from
   `stat -c '%F|%a|%u|%g|%n'`, never by a `find` predicate.
2. **`/tmp` is read-only** in this repo's hardened containers, so a
   `2>"$ERRF"` redirect fails and kills the whole walk - three claims reported
   zero files where restic had processed 708, 46 and 37. The walker creates
   **no temp file at all**; walk errors are tagged inline onto the same stream
   through an fd swap.
3. **Never suppress stderr.** Errors are counted and sampled, and a non-zero
   `WALK_ERRORS` is `INCONCLUSIVE`, which fails the Job. It is never a pass.
4. **busybox `stat -c %F` says `"regular empty file"`** for a zero-byte file, so
   an exact `"regular file"` match silently drops every empty file from **both**
   the readable and unreadable totals. Matched by prefix instead.

A fifth, found while building this check: **`lost+found` is `0700 root:root` on
every ext4 volume by design**, so a walk running as the app's own non-root uid
cannot descend into it. Left in the descent that is one guaranteed
`Permission denied` per ext4 claim, which would report the entire fleet
`INCONCLUSIVE` - as useless as a false clean. It is pruned from the descent and
statted separately, so it stays counted and is never a finding.

## Why `pods/exec`, and the RBAC cost

**Stated plainly: this grants `create` on `pods/exec`, which is arbitrary
command execution inside every pod in the namespaces it is bound to. The captain
accepted that cost knowingly on 2026-08-31.**

It is the price of asking the real question. Reading the volume as root from the
host would always see "readable" and would measure nothing, because root
bypasses the exact permission check this exists to catch. The only way to
evaluate a volume's real modes and ownership is from a container that already
mounts it.

The grant is **narrower than `pvc-writable-check`'s, not wider**:

* **`pvc-mover-readable-check-read`** - `pods` get/list plus get/list on
  `replicationsources` and `snapshotpolicies`. Bound cluster-wide via
  `ClusterRoleBinding`. Discovery only; no exec, no secrets, nothing else.
* **`pvc-mover-readable-check-exec`** - `pods/exec` create, on a `ClusterRole`
  that is **never** bound by a `ClusterRoleBinding`. One namespaced `RoleBinding`
  points at it in each of the five namespaces that actually hold a
  backup-covered claim: `ai`, `downloads`, `home-automation`, `media`,
  `selfhosted`. `pvc-writable-check` binds ~20.

Exclusion is enforced by the API server, not by the script choosing not to look:

```console
$ for ns in ai downloads home-automation media selfhosted database rook-ceph security monitoring; do
    printf '%-18s %s\n' "$ns" "$(kubectl auth can-i create pods --subresource=exec \
      --as=system:serviceaccount:system:pvc-mover-readable-check -n "$ns")"
  done
ai                 yes
downloads          yes
home-automation    yes
media              yes
selfhosted         yes
database           no
rook-ceph          no
security           no
monitoring         no
```

Other mitigations, all matching the sibling: the roles grant nothing else; no
other workload uses this `ServiceAccount`; the remote command is always the same
read-only `walk.sh` with the mount path passed as its own `argv` element (never
spliced into a shell string, so a pod cannot inject through its own
`mountPath`); the sweep container is non-root with a read-only root filesystem
and all capabilities dropped; and it runs for seconds every 6 hours.

### Known coverage gap: `database/pgadmin`

`database` is one of `pvc-writable-check`'s three permanent exclusions, and this
check **does not widen that boundary**. `database/pgadmin` is therefore reported
`UNMEASURED` on every run, even though it is a genuine dual-engine claim with
one of the fleet's more divergent identity pairs (VolSync `1000:1000`, kopiur
`5050:5050`).

Measured out-of-band with cluster-admin credentials on 2026-08-31 for the record
- **not** by this check, and not continuously:

```text
FILES=3 DIRS=7 SYMLINKS=0 UNCLASSIFIED=0 LOST_FOUND=0
VS_UNREADABLE_FILES=1 VS_UNTRAVERSABLE_DIRS=3
KP_UNREADABLE_FILES=0 KP_UNTRAVERSABLE_DIRS=0
WALK_ERRORS=0
```

kopiur reads it today. Closing the gap for real needs a captain decision to add
a `database` RoleBinding, which is a deliberate widening of the sibling's
exclusion list rather than something to slip in here.

### Flux `targetNamespace` gotcha (do not reintroduce)

The overlay at `kubernetes/apps/main/system/pvc-mover-readable-check.yaml`
deliberately has **no** `targetNamespace`, for the reason the sibling documents:
Flux's `targetNamespace` unconditionally overwrites `metadata.namespace` on
every namespaced resource in the build output, even ones that already declare
their own, so the per-namespace `RoleBinding`s would all collapse onto `system`
and collide by name. `ServiceAccount`, `ConfigMap`, `CronJob` and
`PrometheusRule` declare `namespace: system` explicitly instead.

When a namespace gains its first backup-covered claim, add a matching
`pvc-mover-readable-check-exec` RoleBinding in `rbac.yaml`. Until then the claim
is reported `UNMEASURED (pods/exec forbidden, RBAC)` with its own counter, so
the drift is visible in the log rather than silently passing.

## Measured against the live cluster (2026-08-31)

Not a simulation: the manifests were applied directly to the live cluster
pre-merge, run via `kubectl create job --from=cronjob/pvc-mover-readable-check`,
and deleted again afterward.

**Instrument validation, before trusting any output.** The audit caught two bugs
in its own tooling that had produced false-clean zeros, so the walker was
validated against known-good numbers first:

| Check | Expected (audit / AGENTS.md) | Walker |
|---|---|---|
| `sabnzbd-config` unreadable @ uid 1000 | 7 | **7** (exact) |
| `sabnzbd-config` unreadable @ uid 2000 | 0 | **0** (exact) |
| `sabnzbd-config` total files | 2062 | 2063 (+1, live writes) |
| `hermes` untraversable dirs @ uid 1000 | 310 | **310** (exact) |
| `hermes` unreadable files @ uid 1000 | 1948 | 1950 (+2, live writes) |
| `hermes` unreadable @ uid 10000 | 0 | **0** (exact) |
| `WALK_ERRORS` | 0 | **0** on both |

**Positive controls**, to prove the instrument is not simply stuck at zero:
re-running `sabnzbd-config` with the *wrong* kopiur identity (1000 instead of
2000) reports `KP_UNREADABLE_FILES=7`, and `hermes` with 1000 instead of 10000
reports `1950` files and `310` directories - i.e. the check does report the
changedetection failure shape when it is present.

**First full fleet run:**

```text
claims_covered_by_a_backup_engine=31 measured=28 clean=28
unmeasured: namespace_excluded=1 unmounted=1 subpath_only=1 no_running_container=0 no_tools=0 rbac=0 transient=0
kopiur_findings=0 inconclusive=0
volsync (report-only): claims_with_unreadable_entries=9 total_unreadable_files=1987
```

This agrees with the 2026-08-31 audit: **kopiur reads 100% of every measurable
claim**, and all ten of the audit's VolSync raw-permission findings are
reproduced. Eight match **exactly**: `plex` 8, `sabnzbd-config` 7,
`matter-server` 5, `lidarr-config` 4, `radarr-config` 3, `readarr-config` 3,
`sonarr-config` 3, `esphome-config` 2 files + 5 dirs. `hermes` matches exactly
on its 310 untraversable directories - the consequential number, since they hide
the whole volume - and drifts by a couple of files (1948 → 1950/1952) because it
is actively written. The tenth, `database/pgadmin`, is unmeasured here by RBAC
and confirmed separately above at `1` file / `3` directories, also exact.

**Measured runtime: 10s of sweep wall-clock for 31 claims / ~230k entries**
(14s of total Job duration including scheduling); the largest volume,
`ai/hermes` at 104k entries, walks in ~1s. An earlier run of the same sweep took
21s. A 6h cadence is comfortably affordable.

**Alert path proven end-to-end.** A fault-injected run - the shipped CronJob
script unmodified, with only the walker ConfigMap swapped for a stub emitting a
synthetic kopiur finding - failed the Job and made the exact shipped alert
expression return a firing sample:

```text
kube_job_status_failed{job_name="pvc-mover-readable-check-faultinject",
                       reason="BackoffLimitExceeded"} = 1
```

The same expression returns nothing for the clean run. Every fault-injection
object was deleted afterward, and no application, PVC, `ReplicationSource`,
`SnapshotPolicy` or kopiur `Snapshot` was created, modified or deleted at any
point.
