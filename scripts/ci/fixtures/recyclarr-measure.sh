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
