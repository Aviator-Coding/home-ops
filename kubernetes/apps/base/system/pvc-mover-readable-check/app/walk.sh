#!/bin/sh
# pvc-mover-readable-check walker.
#
# Runs INSIDE an application container that already mounts the claim, as that
# container's own identity, delivered over `kubectl exec -i ... -- sh -s`.
# Strictly read-only: `find` and `stat` only. It never writes, chowns, or
# chmods anything, so it is safe against live application data.
#
#   argv: <root> <vsUid> <vsGid> <kpUid> <kpGid>
#
# Pass -1 for both of an engine's ids when that engine does not cover the
# claim; its counters then report NA instead of a misleading 0.
#
# Readable  = owner and owner-read, or in the owning group and group-read, or
#             other-read.
# Traversable (directories) additionally requires the matching execute bit.
# A directory is evaluated for BOTH read and execute because one untraversable
# directory hides its entire subtree from the mover - a far larger failure
# than a single unreadable file (ai/hermes's volume root is 0700 10000:10000,
# so at uid 1000 the whole 89k-file volume is invisible behind one entry).
#
# The four traps below each produce a SILENT FALSE-CLEAN ZERO: the check
# reports "all clear" while having measured nothing. All four were hit for
# real while building the 2026-08-31 fleet audit this check generalises.
#
#  1. busybox `find` has no -uid/-gid. It prints usage text to stderr and
#     exits, so a `find -uid` pipeline yields 0, which reads as clean.
#     Ownership is therefore evaluated in awk from `stat`, never by `find`.
#  2. /tmp is read-only in this repo's hardened containers, so a `2>"$ERRF"`
#     redirect fails and kills the whole walk. NO temp file is created here:
#     walk errors are tagged inline onto the same stream as WALKERR| lines.
#  3. stderr is never suppressed. Error lines are counted and sampled, and a
#     non-zero WALK_ERRORS is INCONCLUSIVE to the caller, never a pass.
#  4. busybox `stat -c %F` prints "regular empty file" for a zero-byte file,
#     so an exact "regular file" match silently drops every empty file from
#     BOTH the readable and the unreadable totals. Matched by prefix instead.
ROOT=$1; VS_UID=$2; VS_GID=$3; KP_UID=$4; KP_GID=$5
LF="$ROOT/lost+found"

# Everything that feeds awk goes inside this block. fd 9 is the block's own
# stdout (the pipe into awk); `2>&1 1>&9` swaps the two, so stderr reaches the
# sed that tags it WALKERR| while stdout bypasses that sed. Both end up in awk,
# which is why no error can be lost and no temp file is needed (trap 2/3).
{
  (
    # lost+found is mode 0700 root:root on every ext4 volume by design, so a
    # walk running as the app's own non-root uid cannot descend into it. Left
    # in the descent, that is one guaranteed "Permission denied" on every ext4
    # claim in the fleet, which would report the whole fleet INCONCLUSIVE -
    # exactly as useless as reporting a false clean. It is pruned from the
    # descent and its own entry statted separately here, so it stays counted
    # and reported, is never silently dropped, and is never a finding.
    if [ -e "$LF" ]; then stat -c '%F|%a|%u|%g|%n' "$LF"; fi
    find "$ROOT" -path "$LF" -prune -o -print0 \
      | xargs -0 -r stat -c '%F|%a|%u|%g|%n'
  ) 2>&1 1>&9 | sed 's/^/WALKERR|/'
} 9>&1 | awk -F'|' \
    -v root="$ROOT" -v vsu="$VS_UID" -v vsg="$VS_GID" -v kpu="$KP_UID" -v kpg="$KP_GID" '
function oct2dec(s,   i,n,c) {
  n = 0
  for (i = 1; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (c < "0" || c > "7") return -1
    n = n * 8 + (c + 0)
  }
  return n
}
function bit(mode, mask) { return int(mode / mask) % 2 == 1 }
function can_read(mode, uid, gid, muid, mgid) {
  if (uid == muid && bit(mode, 256)) return 1     # 0400
  if (gid == mgid && bit(mode,  32)) return 1     # 0040
  if (bit(mode, 4))                  return 1     # 0004
  return 0
}
function can_exec(mode, uid, gid, muid, mgid) {
  if (uid == muid && bit(mode, 64)) return 1      # 0100
  if (gid == mgid && bit(mode,  8)) return 1      # 0010
  if (bit(mode, 1))                 return 1      # 0001
  return 0
}
function count(n, on) { return on ? n "" : "NA" }
BEGIN {
  lf = root "/lost+found"
  vs_on = (vsu >= 0)
  kp_on = (kpu >= 0)
}
$1 == "WALKERR" {
  walk_errors++
  if (walk_errors <= 10) errs[walk_errors] = substr($0, 9)
  next
}
{
  type = $1; mode_s = $2; uid = $3 + 0; gid = $4 + 0
  path = $5
  for (i = 6; i <= NF; i++) path = path "|" $i    # a path may itself contain "|"
  if (NF < 5) { unclassified++; next }
  mode = oct2dec(mode_s)
  if (mode < 0) { unclassified++; next }

  if (path == lf || index(path, lf "/") == 1) { lost_found++; next }

  if (substr(type, 1, 7) == "regular") {          # trap 4: "regular empty file"
    files++
    if (vs_on && !can_read(mode, uid, gid, vsu, vsg)) vs_f++
    if (kp_on && !can_read(mode, uid, gid, kpu, kpg)) kp_f++
    next
  }
  if (type == "directory") {
    dirs++
    if (vs_on && !(can_read(mode, uid, gid, vsu, vsg) && can_exec(mode, uid, gid, vsu, vsg))) vs_d++
    if (kp_on && !(can_read(mode, uid, gid, kpu, kpg) && can_exec(mode, uid, gid, kpu, kpg))) kp_d++
    next
  }
  # A symlink is always mode 0777 on Linux; the target is statted in its own
  # right when the walk reaches it, so only count these.
  if (substr(type, 1, 7) == "symboli") { symlinks++; next }
  # Sockets, fifos and device nodes. kopia does not read their contents, so
  # they are informational and belong in neither readability total.
  unclassified++
}
END {
  printf "FILES=%d DIRS=%d SYMLINKS=%d UNCLASSIFIED=%d LOST_FOUND=%d\n", \
    files, dirs, symlinks, unclassified, lost_found
  printf "VS_UNREADABLE_FILES=%s VS_UNTRAVERSABLE_DIRS=%s\n", count(vs_f + 0, vs_on), count(vs_d + 0, vs_on)
  printf "KP_UNREADABLE_FILES=%s KP_UNTRAVERSABLE_DIRS=%s\n", count(kp_f + 0, kp_on), count(kp_d + 0, kp_on)
  printf "WALK_ERRORS=%d\n", walk_errors
  for (i = 1; i <= walk_errors && i <= 10; i++) printf "WALKERR_SAMPLE=%s\n", errs[i]
}'
