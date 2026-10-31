# Stuck downloads: Cleanuparr and the queue watchdog

Two safety nets watch the usenet queues. Neither deletes media files.

| Piece | What it does | Where |
|---|---|---|
| Cleanuparr | Strikes stalled, failed-import and metadata-stuck queue items. After the strike limit it removes and blocklists the item, so the *arr searches again. Settings are in its UI, not Git | `kubernetes/apps/base/downloads/cleanuparr/` |
| `queue-watchdog` CronJob | Every 30 minutes, exits 1 if any Radarr, Sonarr or Lidarr queue item is older than 24h, exit 2 if a queue is unreadable. A failed Job feeds `KubeJobFailed` | `kubernetes/apps/base/downloads/queue-watchdog/` |

Radarr's `RadarrImportQueueBlocked` Gatus alert still pages on warnings and
errors after 30 minutes. The watchdog adds the age-based 24h limit and covers
Sonarr and Lidarr.

Keep the CronJob's `activeDeadlineSeconds` (a pod that never starts would
otherwise block every later run under `Forbid`, so `KubeJobFailed` never fires)
and `successfulJobsHistory: 1` (with none kept, `KubeJobFailed` latches on the
last failed Job).

## Cleanuparr settings (app state, not Git)

Cleanuparr 2.10 ignores env vars for instances and rules. Every setting lives in
its own database on the config PVC, which kopiur backs up. Git defines only the
pod. After a rebuild without a backup, redo this by hand in the UI
(`cleanuparr.${SECRET_DOMAIN}`, internal gateway only) or its REST API
(`X-Api-Key` header, key from the UI):

1. Instances, each with its API key: Radarr
   (`http://radarr.downloads.svc.cluster.local:7878`), Sonarr
   (`http://sonarr.downloads.svc.cluster.local:8989`) and Lidarr
   (`http://lidarr.downloads.svc.cluster.local:8080`).
2. Download client: SABnzbd (`http://sabnzbd.downloads.svc.cluster.local:8080`).
3. Queue cleaner on, every 5 minutes, 3 strikes for failed import, metadata and
   errored items. Dry run off.
4. Stall rule "Usenet stalled": 12 strikes (about an hour without progress),
   strikes reset on progress. 3 would remove queued items SABnzbd has not
   started yet.
5. Download Cleaner off (it is for torrent seeding). File deletion and the
   malware blocker stay off until you have reviewed a week of blocklist entries.
6. Per-app failed-import strikes stay at `-1`, which inherits the global value.

## Reading a watchdog failure

`kubectl -n downloads logs job/<queue-watchdog job>` prints one line per
service: queued count, stale count and the stale ids, never an API key or
error text. Exit 2 means the queue could not be read (service down or API key
rotated), not that it is healthy.

Stale items are usually one of:

- `importBlocked` or a path/permission error. Fix in Activity, Queue, or Manual Import.
- A monitored movie with no file that Radarr never searched. This is not a
  queue item. See [backlog-search.md](backlog-search.md).
- A download stalled on missing articles. Cleanuparr should strike it out. If
  it does not, check the strike counts in its UI.

If an *arr API key is rotated, update the 1Password item. The watchdog's
ExternalSecret refreshes within 5 minutes. Cleanuparr holds its own copy, so
update the key on its instance in the UI too.
