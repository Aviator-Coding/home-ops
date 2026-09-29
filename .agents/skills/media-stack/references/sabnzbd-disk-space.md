# SABnzbd disk-full runbook (shared-downloads)

Use when SABnzbd reports "Too little diskspace forcing PAUSE" (or is globally
paused with a full queue) and the download, import, transcode pipeline stalls.
The disk that fills is the 2 Ti `shared-downloads` CephFS RWX PVC (ns
`downloads`, class `ceph-filesystem-rwx`) holding `usenet/complete/`, not the
RBD `sabnzbd-incomplete` scratch volume. Alert text and the janitor CronJob
link this page. Keep the path.

## Ghost-under-mount gotcha (read before deleting anything)

The sabnzbd pod mounts the RBD `sabnzbd-incomplete` PVC on top of
`/data/downloads/usenet/incomplete`, over the CephFS mount. So:

- Old CephFS `usenet/incomplete` data is invisible in the sab pod but still
  counts against the PVC. `df` in the sab pod shows the usage, `du` cannot
  find it.
- **Never delete `usenet/incomplete` contents from the sab pod.** That is the
  live RBD scratch volume (active article assembly), not the ghost data.

Measure and delete from the **radarr** pod (or sonarr, or a debug pod): it
mounts `shared-downloads` as plain CephFS and sees the ghost tree.

## Triage

```bash
# 1. How full? (sab pod: df is accurate, du is not)
kubectl -n downloads exec deploy/sabnzbd -c app -- df -h /data/downloads

# 2. Where did it go? (radarr pod sees the true CephFS tree)
kubectl -n downloads exec deploy/radarr -c app -- \
  sh -c "du -sm /data/downloads/usenet/incomplete /data/downloads/usenet/complete/* /data/downloads/* 2>/dev/null | sort -rn | head -30"

# 3. SABnzbd paused / queue (key: secret sabnzbd-secret, field SABNZBD_API_KEY)
kubectl -n downloads exec deploy/sabnzbd -c app -- \
  sh -c 'curl -s "http://localhost:8080/api?mode=queue&output=json&apikey=$SABNZBD_API_KEY" | head -c 2000'

# 4. What Radarr is stuck on (key: secret radarr-secret, field RADARR__AUTH__APIKEY)
kubectl -n downloads exec deploy/radarr -c app -- \
  sh -c 'curl -s -H "X-Api-Key: $RADARR__AUTH__APIKEY" "http://localhost:7878/api/v3/queue?pageSize=200"' | head -c 4000

# 5. Orphaned unpack dirs right now
kubectl -n downloads exec deploy/radarr -c app -- \
  sh -c "find /data/downloads/usenet/complete -mindepth 2 -maxdepth 2 -type d \( -name '_UNPACK_*' -o -name '_FAILED_*' \) | wc -l"
```

Before deleting any `_UNPACK_*` dir, compare with the SAB queue (command 3): a
fresh mtime may be a queued or paused job that will resume and reuse it. Remove
only dirs with no matching active job.

## Ceph caution: batch the deletes

Bulk `rm -rf` on CephFS is MDS metadata work, and sustained full-throttle IO
wedges the small OSDs (osd.0 / osd.6; data migrations use `rsync
--bwlimit=80M` for the same reason). Delete a few hundred GiB at a time, watch
`ceph status` from the rook toolbox between batches, and pause on slow-ops
warnings. This matters double when Ceph is already nearfull.

## Where the space goes (what the 2026-07-03 96%-full incident found)

| Tier | What | Root cause |
|---|---|---|
| a | Ghost pre-migration `usenet/incomplete` tree on CephFS (498 GiB) | PR #983 moved SAB's `download_dir` to an RBD PVC but never deleted the old CephFS data |
| b | Orphaned `_UNPACK_*` / `_FAILED_*` / numbered duplicates under `complete/movies` (1030 GiB) | Death spiral: disk fills, unpack fails, SAB retries into a new numbered dir, less space. Nothing GC'd failed dirs |
| c | Unpacked dirs whose movie is already in the NAS library at equal or better quality (183 GiB) | "Not an upgrade" `importPending` items: Radarr neither imports nor deletes them |
| - | Root-level uncategorized side-channel dumps (237 GiB) | Downloads that skip Radarr/Sonarr get no completed-download cleanup |

That incident also took Ceph to HEALTH_WARN (osd.3 nearfull, PGs
`backfill_toofull`), and nothing alerted before the pause. Tier-c items must
also be cleared from the Radarr queue
(`DELETE /api/v3/queue/{id}?removeFromClient=true&blocklist=true`), not only
from disk, or Radarr re-grabs them.

Standing decisions: side-channel downloads are stopped as policy (every
download goes through Radarr/Sonarr/Lidarr), and the root-level dumps were
deleted, not archived. Hence the janitor never deletes non-empty regular dirs:
under that policy such a dir is an active job or a violation a human should
look at.

## Durable guardrails

Both live in `kubernetes/apps/base/downloads/maintenance/` (Flux ks
`downloads-maintenance`, `kubernetes/apps/main/downloads/maintenance.yaml`).

1. **`downloads-janitor` CronJob, daily 05:30.** Mounts `shared-downloads`
   and, at depth 2 under `usenet/complete` only, deletes `_UNPACK_*` /
   `_FAILED_*` dirs untouched for over 24 h (a live unpack keeps its mtime
   fresh; SAB starts a new numbered dir on retry) and prunes empty dirs older
   than 7 days. It prints every path before removing it, so
   `kubectl -n downloads logs job/<downloads-janitor-...>` is the audit trail.
   It never touches non-empty regular dirs, the `complete/` root, or
   `usenet/incomplete`. Manual dry run of the same selection:

   ```bash
   kubectl -n downloads exec deploy/radarr -c app -- \
     sh -c "find /data/downloads/usenet/complete -mindepth 2 -maxdepth 2 -type d \( -name '_UNPACK_*' -o -name '_FAILED_*' \) -mmin +1440 -print"
   ```

2. **`SharedDownloadsAlmostFull` PrometheusRule** on kubelet volume stats for
   the PVC: warning under 15% free for 30m, critical under 7% for 15m. SAB
   pauses at 100 G free (about 5% of 2 Ti), so both fire before the freeze.

## Notes

- SAB's thresholds are runtime state, not GitOps: `complete_free=100G`,
  `download_free=100G`, `direct_unpack=1` were set via the SAB API and live in
  the sabnzbd config PVC (live-confirmed 2026-08-21). After a config PVC
  rebuild, re-apply with `api?mode=set_config`. TRaSH recommends Direct Unpack
  off; this cluster's standing value is on.
- The Radarr-grabbed happy path was not the leak
  (`enableCompletedDownloadHandling=true`, `autoRedownloadFailed=true`,
  `downloadClientWorkingFolders=_UNPACK_|_FAILED_`, SAB
  `fail_hopeless_jobs=1`). The leaks were the ghost tree, failed-unpack
  orphans and side-channel downloads.
