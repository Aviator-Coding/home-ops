# recyclarr-config readable check - 2026-08-31

> **Historical snapshot, but the procedure is durable.** Measured live against the cluster
> on 2026-08-31 at 11:29-11:31Z. Re-measure before relying on the numbers if the app's
> `securityContext` or file layout changes; the *procedure* (manifests, ordering,
> classification logic, cleanup) is the reusable part.

## Result

**`downloads/recyclarr-config` is fully readable by kopiur's mover identity (uid/gid 2000).**

| | count |
|---|---:|
| files | 2913 / 2913 readable |
| directories | 607 / 607 traversable |
| symlinks | 0 |
| unclassified entries | 0 |
| walk errors | 0 |
| `lost+found` | not present |

This clears the **readability** sub-criterion the 2026-08-31 fleet audit flagged this claim as
unable to measure. It does **not** by itself satisfy migration Stage 5's full "per-volume
restore proof" bar (`kubernetes/components/kopiur/Readme.md`) - that still needs a restore +
fidelity check of the kind done for `sabnzbd-config`, `changedetection-config`, and
`matter-server` (`docs/backups/kopiur-restore-drill-2026-08-30.md`). This document proves the
narrower, previously-missing fact: kopiur's mover identity can read every byte of metadata on
this claim, so a restore-fidelity drill for it is no longer blocked on an unmeasurable
precondition.

## Why this was unmeasurable before

`recyclarr` is a `CronJob` (`@daily`, `America/New_York`,
`kubernetes/apps/base/downloads/recyclarr/app/helmrelease.yaml`), and its pod exists for
roughly 18 seconds a day. The fleet audit's live-pod-exec method (used for all 29 other
mountable claims) has no running container to exec into between runs.

A prior read of this CronJob's history mistakenly read it as failing for seven days. That is
wrong and must not be re-derived from Job objects: `successfulJobsHistoryLimit: 0` deletes
every successful Job immediately, so the only Job ever visible is a stale historical failure.
The CronJob is healthy - `kubectl -n downloads get cronjob recyclarr -o jsonpath='{.status.lastSuccessfulTime}'`
is the correct signal, not Job history.

## Measurement approach chosen, and why the alternatives were rejected

Three shapes were considered:

1. **Measure during the CronJob's own run.** Rejected. The container has
   `readOnlyRootFilesystem: true` and the `ghcr.io/recyclarr/recyclarr` image is a minimal
   .NET image with no guarantee of `stat`/`awk`/`find`. The ~18-second run window makes a
   `kubectl exec` race unreliable to hit deterministically, and coupling verification to
   production run timing (or manually triggering an extra run to get a bigger window) adds
   operational fragility for no benefit - and manually triggering the CronJob is exactly the
   kind of lifecycle action this task's brief was scoped to avoid.
2. **Mount the claim read-only from a short-lived pod directly against the live PVC.**
   Rejected. `recyclarr-config` is `ReadWriteOnce` (verified:
   `kubectl -n downloads get pvc recyclarr-config -o jsonpath='{.spec.accessModes}'`), and the
   CronJob has `concurrencyPolicy: Forbid` / `backoffLimit: 0` - a single exclusive mount
   window per day. A probe holding that same exclusive RWO attachment for any duration is a
   standing collision risk against the CronJob's own mount if a manual reconcile or backfill
   run happens to overlap, which directly threatens "the CronJob must keep succeeding" -
   worth avoiding structurally rather than judging as low-probability given the current
   schedule state.
3. **Measure a restored copy. Chosen.** A CSI `VolumeSnapshot` of the live PVC is a
   storage-level, read-only operation that does **not** attach or lock the RWO volume - it
   cannot race the CronJob's mount by construction, not merely by favorable timing (confirmed
   live: the snapshot reached `readyToUse: true` in under 6 seconds with the CronJob and its
   PVC completely untouched). Restoring that snapshot into a brand-new, uniquely-named scratch
   PVC and walking *only* the scratch copy - never the live claim - satisfies "read-only
   against the claim's contents" trivially, observes the volume's current (not day-old) state,
   and mirrors kopiur's own production mechanism: the live `recyclarr-ceph` `SnapshotPolicy`
   already runs with `copyMethod: Snapshot`, staging the source into a `*-src` PVC before
   kopia reads it (`kubectl -n downloads get snapshotpolicy recyclarr-ceph -o
   jsonpath='{.spec.copyMethod}'`). Standing cost is zero - every scratch object was deleted
   immediately after the walk.

This follows the same "never touch the live claim, use a new uniquely-named scratch object"
house standard as `docs/backups/restore-drill-2026-08-23.md` and
`docs/backups/kopiur-restore-drill-2026-08-30.md`, adapted to a claim that has no running
container to restore or exec into.

## Resolving the mover identity

Resolved from the **live** `SnapshotPolicy`, not component defaults:

```bash
kubectl -n downloads get snapshotpolicy recyclarr-ceph \
  -o jsonpath='{.spec.mover.podSecurityContext}'
# {"fsGroup":2000,"runAsGroup":2000,"runAsUser":2000}
```

This matches the value substituted on the app's Flux Kustomization
(`KOPIUR_PUID`/`KOPIUR_PGID: "2000"` in `kubernetes/apps/main/downloads/recyclarr.yaml`), which
in turn is documented there as taken from the pod's *declared* `securityContext` rather than
live file measurement, because - as above - there was never a running container to measure
through. This check is what turns that declared value into a measured one.

## Procedure

### 1. Snapshot and restore, never touching the live claim

```yaml
# 01-volumesnapshot.yaml
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: recyclarr-config-readable-check
  namespace: downloads
  labels:
    fm.homeops/readable-check: recyclarr-config
spec:
  volumeSnapshotClassName: csi-ceph-blockpool
  source:
    persistentVolumeClaimName: recyclarr-config
---
# 02-pvc.yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: recyclarr-config-readable-check
  namespace: downloads
  labels:
    fm.homeops/readable-check: recyclarr-config
spec:
  accessModes: [ReadWriteOnce]
  storageClassName: ceph-block
  resources:
    requests:
      storage: 5Gi
  dataSource:
    name: recyclarr-config-readable-check
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
---
# 03-pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: recyclarr-config-readable-check
  namespace: downloads
  labels:
    fm.homeops/readable-check: recyclarr-config
spec:
  restartPolicy: Never
  securityContext:
    runAsUser: 0
    runAsNonRoot: false
    fsGroup: 0
  containers:
    - name: walker
      image: alpine/k8s:1.36.2
      command: ["sleep", "3600"]
      securityContext:
        allowPrivilegeEscalation: false
        capabilities: { drop: ["ALL"] }
      volumeMounts:
        - name: data
          mountPath: /check
          readOnly: true          # volume-source readOnly, not just the mount -
                                   # otherwise kubelet's fsGroup walk rewrites the
                                   # very ownership/permission evidence being measured
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: recyclarr-config-readable-check
        readOnly: true
```

The walker container runs **as root**, deliberately - not as the mover's uid/gid. Running as
root lets `stat` see every entry's real mode/owner/group unconditionally, so the walk itself
never hits a permission wall and can enumerate the *entire* tree with zero ambiguity. The
mover's actual access is then computed from the recorded owner/group/mode bits (see below),
exactly as the task's own acceptance criteria specify, rather than inferred from what the walk
process itself could or couldn't open. The mount stays read-only regardless of the walker's
uid, so nothing on the scratch copy (and therefore nothing derived from the live claim) is ever
written to.

### 2. The walk and its classification logic

Two traps a naive version of this hits, both producing a **silent false-clean zero**:

- **busybox `stat`'s `%F` reports `"regular empty file"` for zero-byte files, not
  `"regular file"`.** An exact-string match on `"regular file"` silently drops every empty
  file from both the readable and unreadable counts - caught live here: two zero-byte files
  (`recyclarr.yml`, a `.verbose.log`) fell through an exact match into an unclassified bucket
  before the classifier was changed to `substr(type,1,7) == "regular"`.
- **busybox `find` has no `-uid`/`-gid`.** Not used at all here - ownership is read from
  `stat -c '%a|%u|%g|%n'` and evaluated in `awk`, never via a `find` permission predicate.

Every entry (files, directories, symlinks) under the scratch mount is enumerated with a single
`find | xargs stat` pass, classified by `awk` using the POSIX rule the task specified: readable
if owner-with-owner-read, or in the owning group with group-read, or other-read; directories
additionally need the execute bit to be traversable. `lost+found` is counted separately and
excluded from both totals - it is root-owned `0700` by ext4 design and not a finding (this
volume has none). Walk errors (`find`/`stat` failures) are captured to a file and counted
explicitly, never suppressed - the script reports `WALK_ERRORS=0`, not silence, and a non-zero
count would be reported as inconclusive rather than folded into either count.

Install the measure script into the walker, then run it. The walker pod above is **not** one
of this repo's hardened `readOnlyRootFilesystem` app containers, so `/tmp` is writable here
and is a safe place to stage the script and the walk-error file. Do **not** copy this `/tmp`
scratch pattern onto a live app container - that is the fourth false-clean trap.

```bash
# Resolve mover identity first (section above), then export it into the install.
MOVER_UID=2000
MOVER_GID=2000

kubectl -n downloads exec -i recyclarr-config-readable-check -c walker -- \
  sh -c 'cat > /tmp/measure.sh && chmod 0755 /tmp/measure.sh' <<'EOF'
#!/bin/sh
# Classify every entry under ROOT for whether MOVER_UID:MOVER_GID could read it.
# Readable: owner+owner-read, or group+group-read, or other-read.
# Directories additionally need the matching execute bit to be traversable.
# Traps this script is written to avoid:
#   - busybox stat %F is "regular empty file" for zero-byte files -> prefix match
#   - busybox find has no -uid/-gid -> ownership from stat via awk only
#   - never suppress stderr; walk errors are counted and fail closed as inconclusive
#   - lost+found is root-owned 0700 by ext4 design -> counted separately, not a finding
set -eu

ROOT="${ROOT:-/check}"
MOVER_UID="${MOVER_UID:?MOVER_UID required}"
MOVER_GID="${MOVER_GID:?MOVER_GID required}"

# Keep errors out of the classification stream so a failed walk cannot look like a clean zero.
ERRF="$(mktemp /tmp/walk-errors.XXXXXX)"
STATF="$(mktemp /tmp/walk-stat.XXXXXX)"
trap 'rm -f "$ERRF" "$STATF"' EXIT

# Enumerate the whole tree. stderr is captured, never discarded.
if ! find "$ROOT" -print0 2>>"$ERRF" | xargs -0 -r stat -c '%F|%a|%u|%g|%n' 2>>"$ERRF" >"$STATF"; then
  # xargs returns 123/124 on entry failures; still classify what we got, then surface errors.
  :
fi

WALK_ERRORS="$(wc -l < "$ERRF" | tr -d ' ')"

awk -F'|' -v muid="$MOVER_UID" -v mgid="$MOVER_GID" -v walk_errors="$WALK_ERRORS" '
function oct2dec(s,   i, n, c) {
  n = 0
  for (i = 1; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (c < "0" || c > "7") return -1
    n = n * 8 + (c + 0)
  }
  return n
}
function bit(mode, mask) { return int(mode / mask) % 2 == 1 }
function is_lost_found(path) {
  return path == "/check/lost+found" || index(path, "/check/lost+found/") == 1
}
function can_read(mode, uid, gid) {
  if (uid == muid && bit(mode, 256)) return 1
  if (gid == mgid && bit(mode, 32))  return 1
  if (bit(mode, 4))                  return 1
  return 0
}
function can_exec(mode, uid, gid) {
  if (uid == muid && bit(mode, 64)) return 1
  if (gid == mgid && bit(mode, 8))  return 1
  if (bit(mode, 1))                 return 1
  return 0
}
BEGIN {
  files_total = files_readable = files_unreadable = 0
  dirs_total = dirs_traversable = dirs_untraversable = 0
  symlinks_total = unclassified_total = 0
  lost_found_entries = 0
  lost_found_present = "no"
  lost_found_owner = ""
  lost_found_mode = ""
}
{
  type = $1; mode_s = $2; uid = $3 + 0; gid = $4 + 0; path = $5
  for (i = 6; i <= NF; i++) path = path "|" $i
  mode = oct2dec(mode_s)
  if (mode < 0) { unclassified_total++; next }

  if (is_lost_found(path)) {
    lost_found_entries++
    if (path == "/check/lost+found") {
      lost_found_present = "yes"
      lost_found_owner = uid ":" gid
      lost_found_mode = mode_s
    }
    next
  }

  # busybox: zero-byte files are "regular empty file", not "regular file".
  if (substr(type, 1, 7) == "regular") {
    files_total++
    if (can_read(mode, uid, gid)) files_readable++
    else files_unreadable++
    next
  }
  if (type == "directory") {
    dirs_total++
    if (can_read(mode, uid, gid) && can_exec(mode, uid, gid)) dirs_traversable++
    else dirs_untraversable++
    next
  }
  if (substr(type, 1, 7) == "symboli") {
    # symlinks: the entry itself is always statted; count only, no mode check
    symlinks_total++
    next
  }
  unclassified_total++
}
END {
  print "FILES_TOTAL=" files_total
  print "FILES_READABLE=" files_readable
  print "FILES_UNREADABLE=" files_unreadable
  print "DIRS_TOTAL=" dirs_total
  print "DIRS_TRAVERSABLE=" dirs_traversable
  print "DIRS_UNTRAVERSABLE=" dirs_untraversable
  print "SYMLINKS_TOTAL=" symlinks_total
  print "UNCLASSIFIED_TOTAL=" unclassified_total
  print "LOST_FOUND_ENTRIES=" lost_found_entries
  print "LOST_FOUND_PRESENT=" lost_found_present " owner=" lost_found_owner " mode=" lost_found_mode
  print "WALK_ERRORS=" walk_errors
}
' "$STATF"

if [ "$WALK_ERRORS" != "0" ]; then
  echo "INCONCLUSIVE: walk reported $WALK_ERRORS error line(s); do not treat counts as a pass" >&2
  cat "$ERRF" >&2 || true
  exit 2
fi
EOF

kubectl -n downloads exec recyclarr-config-readable-check -c walker -- \
  env MOVER_UID="$MOVER_UID" MOVER_GID="$MOVER_GID" ROOT=/check sh /tmp/measure.sh
```

produced:

```
FILES_TOTAL=2913
FILES_READABLE=2913
FILES_UNREADABLE=0
DIRS_TOTAL=607
DIRS_TRAVERSABLE=607
DIRS_UNTRAVERSABLE=0
SYMLINKS_TOTAL=0
UNCLASSIFIED_TOTAL=0
LOST_FOUND_ENTRIES=0
LOST_FOUND_PRESENT=no owner= mode=
WALK_ERRORS=0
```

Cross-checked: `2913 + 607 + 0 = 3520`, matching a plain `find /check | wc -l` (no type filter)
exactly - the walk enumerated the entire tree with nothing skipped. The file count also lines
up with kopiur's own most recent production snapshot of this claim from ~2 hours earlier
(`kubectl -n downloads get snapshots.kopiur.home-operations.com recyclarr-ceph-20260831090426
-o jsonpath='{.status.stats}'` -> `{"filesNew":2913,"sizeBytes":78554393}`), independent
corroboration that kopiur's actual production mover already reads this claim successfully -
consistent with the finding that kopia fails closed on the first unreadable file
(`kubernetes/components/kopiur/Readme.md` "SecurityContextCompatible").

### 3. Cleanup and verification nothing live was touched

```bash
kubectl -n downloads delete pod/recyclarr-config-readable-check
kubectl -n downloads delete pvc/recyclarr-config-readable-check
kubectl -n downloads delete volumesnapshot/recyclarr-config-readable-check

# live claim's PVC uid is unchanged - proves it was never re-provisioned or written to
kubectl -n downloads get pvc recyclarr-config -o jsonpath='{.metadata.uid}'

# CronJob still healthy after the check
kubectl -n downloads get cronjob recyclarr \
  -o jsonpath='lastScheduleTime={.status.lastScheduleTime}{"\n"}lastSuccessfulTime={.status.lastSuccessfulTime}{"\n"}'
```

## References

* Fleet audit context and Stage 4 status: `kubernetes/components/kopiur/Readme.md`
  "SecurityContextCompatible"
* Restore-drill house standard this procedure adapts:
  `docs/backups/restore-drill-2026-08-23.md`,
  `docs/backups/kopiur-restore-drill-2026-08-30.md`
* Open, separate decision (not addressed here): a fleet-wide `pvc-mover-readable-check`
  CronJob generalizing this one-off check, captain decision `mover-readable-check`.
