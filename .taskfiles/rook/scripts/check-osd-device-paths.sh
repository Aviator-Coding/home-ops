#!/usr/bin/env bash
# Audit Rook OSD device-path drift + reboot-safety gate (Rook bug #17224).
#
# Raw-mode OSD deployments store an unstable /dev/nvmeXn1 kernel name in
# ROOK_BLOCK_PATH (NOT the stable /dev/disk/by-id path from the CephCluster CR).
# Kernel names reshuffle across reboots, so the stored path drifts and goes STALE.
# Running OSDs are unaffected; a STALE OSD relies on the activate "relocate" fallback
# (full `ceph-volume raw list`) on its NEXT restart -- which works under normal load
# but can fail when the cluster is already degraded (that turned a single OSD restart
# into a long outage on 2026-06-14).
#
# This audit is informational; the real go/no-go for rebooting a node or restarting an
# OSD is cluster health -- it exits non-zero unless Ceph is HEALTH_OK.
# Recovery for a stuck OSD: docs/ceph/osd-device-path-recovery.md
set -euo pipefail

# Don't let git-bash/MSYS rewrite container-side paths (e.g. /var/lib/ceph/...,
# /dev/...) into Windows paths before they reach `kubectl exec`. No-op on Linux.
export MSYS_NO_PATHCONV=1

CTX="${CLUSTER:-admin@kubernetes}"
NS="${ROOK_NAMESPACE:-rook-ceph}"
K=(kubectl --context "${CTX}" -n "${NS}")

echo "== Rook OSD device-path audit (Rook #17224) =="
printf '%-7s %-9s %-13s %-13s %s\n' OSD NODE DEPLOY RUNNING STATUS
stale=0
for dep in $("${K[@]}" get deploy -l app=rook-ceph-osd -o name | sort -V); do
  id="${dep##*-osd-}"
  dpath="$("${K[@]}" get "$dep" -o jsonpath='{.spec.template.spec.initContainers[?(@.name=="activate")].env[?(@.name=="ROOK_BLOCK_PATH")].value}')"
  pod="$("${K[@]}" get pod -l app=rook-ceph-osd,osd="$id" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  node="$("${K[@]}" get pod -l app=rook-ceph-osd,osd="$id" -o jsonpath='{.items[0].spec.nodeName}' 2>/dev/null || true)"
  rpath="?"
  [ -n "$pod" ] && rpath="$("${K[@]}" exec "$pod" -c osd -- readlink -f "/var/lib/ceph/osd/ceph-$id/block" 2>/dev/null || echo '?')"
  if [ -n "$dpath" ] && [ "$dpath" = "$rpath" ]; then
    st="OK"
  else
    st="STALE"
    stale=$((stale + 1))
  fi
  printf '%-7s %-9s %-13s %-13s %s\n' "osd.$id" "${node:-?}" "${dpath:-?}" "$rpath" "$st"
done
if [ "$stale" -gt 0 ]; then
  echo "  ($stale OSD(s) STALE -- expected under Rook #17224; harmless until restarted,"
  echo "   then they self-heal via the relocate fallback IF the cluster is healthy)"
fi

echo
echo "== Reboot-safety gate (cluster health) =="
health="$("${K[@]}" exec deploy/rook-ceph-tools -- ceph health 2>/dev/null | tr -d '\r' | head -1 || echo UNKNOWN)"
echo "$health"
case "$health" in
  HEALTH_OK*)
    echo "SAFE: HEALTH_OK -> a node reboot / OSD restart will self-heal stale paths."
    ;;
  *)
    echo "NOT SAFE: cluster is not HEALTH_OK. The relocate fallback can return empty"
    echo "under load, so a restarted STALE OSD may fail to re-activate (stuck Init)."
    echo "Fix cluster health first. Recovery: docs/ceph/osd-device-path-recovery.md"
    exit 1
    ;;
esac
