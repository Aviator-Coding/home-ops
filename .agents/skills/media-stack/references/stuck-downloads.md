# Stuck downloads: Cleanuparr and the queue watchdog

Two safety nets watch the usenet queues. Neither deletes media files.

| Piece | What it does | Where |
|---|---|---|
| Cleanuparr | Strikes stalled, failed-import and metadata-stuck queue items. After 3 strikes it removes and blocklists the item, so the *arr searches again | `kubernetes/apps/base/downloads/cleanuparr/` |
| `queue-watchdog` CronJob | Every 30 minutes, exits 1 if any Radarr, Sonarr or Lidarr queue item is older than 24h, exit 2 if a queue is unreadable. A failed Job feeds `KubeJobFailed` | `kubernetes/apps/base/downloads/queue-watchdog/` |

Radarr's `RadarrImportQueueBlocked` Gatus alert still pages on warnings and
errors after 30 minutes. The watchdog adds the age-based 24h limit and covers
Sonarr and Lidarr.

## One-time Cleanuparr setup (app state, not Git)

Git injects only the Radarr and Sonarr instances. Everything else lives in
Cleanuparr's config PVC, which kopiur backs up:

1. Open `cleanuparr.${SECRET_DOMAIN}` (internal gateway only).
2. Add the Lidarr instance (`http://lidarr.downloads.svc.cluster.local:8080`)
   and its API key.
3. Add SABnzbd as a download client
   (`http://sabnzbd.downloads.svc.cluster.local:8080`) and its API key.
4. Leave file deletion and the malware blocker off until you have reviewed a
   week of its blocklist entries.

## Reading a watchdog failure

`kubectl -n downloads logs job/<queue-watchdog job>` prints one line per
service: queued count, stale count and the stale ids. Exit 2 means the queue
could not be read (service down or API key rotated), not that it is healthy.

Stale items are usually one of:

- `importBlocked` or a path/permission error. Fix in Activity, Queue, or Manual Import.
- A monitored movie with no file that Radarr never searched. This is not a
  queue item. See [backlog-search.md](backlog-search.md).
- A download stalled on missing articles. Cleanuparr should strike it out. If
  it does not, check the strike counts in its UI.

If an API key is rotated, update the 1Password item. Both ExternalSecrets
refresh within 5 minutes and the reloader restarts Cleanuparr.
