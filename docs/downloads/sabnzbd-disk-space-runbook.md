# Runbook: shared-downloads disk-full / SABnzbd auto-pause

When SABnzbd reports "Too little diskspace forcing PAUSE" (or sits globally
paused with a full queue) and the movie pipeline (download → import → transcode)
stalls. The disk that fills is the **2 Ti `shared-downloads` CephFS RWX PVC**
(ns `downloads`, StorageClass `ceph-filesystem-rwx`) that holds
`usenet/complete/` — *not* the RBD `sabnzbd-incomplete` scratch volume.

- **Guardrails in Git** → `kubernetes/apps/base/downloads/maintenance/` (janitor CronJob + capacity alert, this incident's output)
- **Prior art** → 2026-06-24 `_UNPACK_` cleanup (PR path not in this repo; see this runbook's incident notes and `kubernetes/apps/base/downloads/maintenance/`)
- **Ceph-side context** → [`../ceph-cluster-changelog.md`](../ceph-cluster-changelog.md)

---

## Incident 2026-07-03 — 96% full, ~1.7 TiB of dead data

`shared-downloads` hit **96% used (84 GiB free)**, tripping SABnzbd's
`complete_free=100G` auto-pause with 126 queued jobs. Ceph itself went
HEALTH_WARN (osd.3 nearfull, 10 PGs `backfill_toofull`) — a downloads-volume
problem became a storage-cluster problem. Nothing alerted before the pause.

Where the space went (all sizes from `du` in the **radarr** pod — see the
gotcha below for why not the sab pod):

| Tier | What | Size | Root cause |
|------|------|------|------------|
| a | **Ghost pre-migration `usenet/incomplete` tree on CephFS** | **498 GiB** | PR #983 moved SAB's `download_dir` onto an RBD PVC but never deleted the old CephFS data. The sab pod's RBD mount *shadows* the path, so the ghost tree was invisible in the sab pod while still consuming quota. |
| b | **Orphaned `_UNPACK_*` / `_FAILED_*` / numbered-duplicate dirs** under `complete/movies` | **1030 GiB** | Death spiral: disk fills → unpack fails partway → SAB retries → new numbered `_UNPACK_` turd → even less space. 18 Shutter Island attempts, 25 For.a.Few.Dollars.More, 12 Jaws. Nothing ever GC'd failed unpack dirs. |
| c | **Unpacked-but-never-cleaned dirs** whose movie was already in the NAS library at equal/better quality | **183 GiB** | "Not an upgrade" `importPending` items — Radarr won't import them, won't delete them, they sit forever. |
| — | **Root-level uncategorized side-channel dumps** (manual/uncategorized adds, no *arr tracking) | **237 GiB** | Downloads that never went through Radarr/Sonarr get no completed-download cleanup — a permanent leak. |

### Decisions made (2026-07-03, recorded here so they stick)

1. **The MegaPACK / root-level side-channel dumps were DELETED, not archived.**
2. **Side-channel downloads are STOPPED as policy.** Every download must flow
   through Radarr/Sonarr/Lidarr so completed-download handling cleans up after
   import. Corollary: the janitor deliberately does **not** delete non-empty
   regular dirs — under this policy any such dir is either an active job or a
   policy violation a human should look at, never something to auto-delete.

---

## ⚠️ The ghost-under-mount gotcha (read before deleting anything)

The sabnzbd pod mounts the RBD `sabnzbd-incomplete` PVC **on top of**
`/data/downloads/usenet/incomplete`, over the CephFS `shared-downloads` mount.
Two consequences:

- The old CephFS `usenet/incomplete` data is **invisible inside the sab pod**
  (hidden under the mountpoint) but still counts against the PVC. `df` in the
  sab pod shows the usage; `du` in the sab pod can't find it.
- **NEVER delete `usenet/incomplete` contents from inside the sab pod** — you
  would be deleting from the **live RBD scratch volume** (active article
  assembly), not the ghost data.

The correct vantage point is the **radarr pod** (or sonarr, or a one-off debug
pod): it mounts `shared-downloads` as plain CephFS with no RBD overlay, so it
sees — and can safely delete — the ghost tree. This asymmetry is the whole
reason the ghost data survived PR #983 unnoticed.

---

## Triage one-liners

```bash
# 1. How full is it really? (sab pod — df is accurate, du is not; see gotcha)
kubectl -n downloads exec deploy/sabnzbd -c app -- df -h /data/downloads

# 2. Where did it go? (radarr pod — sees the true CephFS tree incl. ghosts)
kubectl -n downloads exec deploy/radarr -c app -- \
  sh -c "du -sm /data/downloads/usenet/incomplete /data/downloads/usenet/complete/* /data/downloads/* 2>/dev/null | sort -rn | head -30"

# 3. Is SABnzbd paused, and what's queued? (API key: secret sabnzbd-secret, field SABNZBD_API_KEY)
kubectl -n downloads exec deploy/sabnzbd -c app -- \
  sh -c 'curl -s "http://localhost:8080/api?mode=queue&output=json&apikey=$SABNZBD_API_KEY" | head -c 2000'

# 4. What is Radarr stuck on? (API key: secret radarr-secret, field RADARR__AUTH__APIKEY)
kubectl -n downloads exec deploy/radarr -c app -- \
  sh -c 'curl -s -H "X-Api-Key: $RADARR__AUTH__APIKEY" "http://localhost:7878/api/v3/queue?pageSize=200"' | head -c 4000

# 5. Orphaned unpack turds right now?
kubectl -n downloads exec deploy/radarr -c app -- \
  sh -c "find /data/downloads/usenet/complete -mindepth 2 -maxdepth 2 -type d \( -name '_UNPACK_*' -o -name '_FAILED_*' \) | wc -l"
```

Cross-check before deleting any `_UNPACK_*` dir: a fresh mtime may belong to a
queued/paused SAB job that will resume and reuse it — compare against the SAB
queue (one-liner 3) and only remove dirs with no matching active job.

## ⚠️ Ceph caution: batch the deletes

Bulk `rm -rf` on CephFS is metadata work on the MDS, and sustained full-throttle
IO **wedges the small OSDs** (osd.0/osd.6 — the same reason data migrations use
`rsync --bwlimit=80M`). Delete in batches (a few hundred GiB at a time), watch
`ceph status` from the rook toolbox between batches, and pause if slow-ops
warnings appear. This matters double when Ceph is already nearfull, as it was
during this incident.

---

## Durable guardrails (deployed from this incident)

Both live in `kubernetes/apps/base/downloads/maintenance/` (Flux ks
`downloads-maintenance`, defined at `kubernetes/apps/main/downloads/maintenance.yaml`):

### 1. `downloads-janitor` CronJob — daily 05:30

Mounts `shared-downloads` at `/data/downloads` and, at depth 2 under
`usenet/complete` only (`complete/<category>/<dir>`):

- deletes `_UNPACK_*` / `_FAILED_*` dirs untouched for **>24 h** (a live unpack
  keeps its mtime fresh; day-old attempts are dead — SAB starts a *new*
  numbered dir on retry)
- prunes **empty** dirs older than 7 days

It prints every path before removing it, so the Job log is an audit trail
(`kubectl -n downloads logs job/<downloads-janitor-...>`). It never touches
non-empty regular dirs (see decision 2), the `complete/` root level, or
`usenet/incomplete`.

Manual dry-run of the same selection, any time:

```bash
kubectl -n downloads exec deploy/radarr -c app -- \
  sh -c "find /data/downloads/usenet/complete -mindepth 2 -maxdepth 2 -type d \( -name '_UNPACK_*' -o -name '_FAILED_*' \) -mmin +1440 -print"
```

### 2. `SharedDownloadsAlmostFull` PrometheusRule

Fires on kubelet volume stats for the `shared-downloads` PVC:

- **warning**: <15% free for 30m
- **critical**: <7% free for 15m

Rationale: SAB pauses at 100 G free ≈ **5%** of 2 Ti, so both thresholds fire
*before* the pipeline freezes — the whole point is that a human acts first.
(Consumed natively by kube-prometheus-stack's Prometheus Operator, same as
every other rule in this repo.)

---

## Notes

- **SAB's disk thresholds are runtime state, not GitOps.** `complete_free=100G`,
  `download_free=100G` and `direct_unpack=1` were set via the SAB API
  (2026-06-24) and live in the sabnzbd **config PVC** - they survive restarts
  but are invisible in Git. Live-confirmed 2026-08-21:
  `direct_unpack=true`, `complete_free=100G`, `download_free=100G`.
  If the config PVC is ever rebuilt, re-apply them via `api?mode=set_config`.
  TRaSH still recommends Direct Unpack OFF; this cluster's standing runtime
  value is ON.
- The Radarr-grabbed happy path is correctly configured and was NOT the leak
  (`enableCompletedDownloadHandling=true`, `autoRedownloadFailed=true`,
  `downloadClientWorkingFolders=_UNPACK_|_FAILED_`, SAB `fail_hopeless_jobs=1`).
  The leaks were the ghost tree, failed-unpack orphans, and side-channel
  downloads — the first is one-time, the other two are covered by the janitor
  and by decision 2.
- Tier-c "Not an upgrade" items must also be cleared from the **Radarr queue**
  (`DELETE /api/v3/queue/{id}?removeFromClient=true&blocklist=true`), not just
  from disk, or Radarr re-grabs them.
