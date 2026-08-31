# Decision: downloads stack is usenet-only

Date: 2026-08-30

## Decision

Commit the `downloads` namespace to usenet-only acquisition. Remove the dead
torrent path rather than leave it half-configured:

- Deleted Radarr's `qBittorrent` download client (id 1).
- Deleted Prowlarr's four definition-less torrent indexers: `BitSearch` (id 1),
  `TorrentGalaxyClone` (id 7), `Isohunt2` (id 81), `iDope` (id 80).

## Why

Radarr's qBittorrent download client pointed at
`qbittorrent.downloads.svc.cluster.local`, which has no pod and no Service in
the cluster - `qbittorrent` was already removed as a dead, unreferenced app
directory (see `docs/media-stack.md`, `kubernetes/components/volsync/Readme.md`
line ~384). The client was harmless only because it was `enable: false` and
every torrent indexer in Radarr was disabled, but its presence implied a
torrent path that did not actually exist and could silently be relied upon by
a future change.

Separately, Prowlarr's own `IndexerNoDefinitionCheck` health check flagged
`BitSearch`, `TorrentGalaxyClone`, `Isohunt2`, and `iDope` as having no
working indexer definition ("Indexers have no definition and will not work
... Please remove and (or) re-add to Prowlarr"). These four were disabled,
torrent-protocol, and structurally broken regardless of the qBittorrent
decision.

## Evidence gathered before deletion (verify-before-delete)

- `kubectl -n downloads get pods,svc`: no `qbittorrent` pod or Service exists.
- Radarr `GET /api/v3/downloadclient`: qBittorrent client (id 1) had
  `"enable": false`; SABnzbd (id 2) had `"enable": true` and was the only
  enabled client.
- Prowlarr `GET /api/v1/health` (before deletion): reported
  `IndexerNoDefinitionCheck` error naming exactly these four indexers.
- Prowlarr `GET /api/v1/indexer`: confirmed all four as `protocol: torrent`,
  `enable: false`.
- No GitOps manifest anywhere in `kubernetes/` references qbittorrent, any of
  the four indexer names, or their download-client/indexer IDs - this
  configuration lives entirely in each app's own Postgres-backed database,
  reached only through its REST API.

## What changed and how (manual/live API steps - NOT GitOps)

This is entirely runtime application state, not committed manifests, so there
is no Kubernetes YAML diff for the deletions themselves:

- `DELETE /api/v3/downloadclient/1` against Radarr (`radarr.downloads.svc`),
  authenticated with the API key from Kubernetes Secret `radarr-secret`
  (`RADARR__AUTH__APIKEY`). Response: 200.
- `DELETE /api/v1/indexer/{1,7,81,80}` against Prowlarr
  (`prowlarr.downloads.svc`), authenticated with the API key from Secret
  `prowlarr-secret` (`PROWLARR__AUTH__APIKEY`). Response: 200 for each.

Anyone reproducing or rolling back this change must use the same app APIs
(or the web UI) - there is nothing to `git revert` for the deletions
themselves. Only the documentation update in `docs/media-stack.md` is a Git
change.

## Verification after deletion

- Prowlarr `GET /api/v1/health`: `IndexerNoDefinitionCheck` error is gone;
  only the routine "new update available" notice remains.
- Radarr `GET /api/v3/health`: clean (only the routine update notice).
- Radarr `GET /api/v3/downloadclient`: only SABnzbd (id 2) remains.
- Radarr `POST /api/v3/downloadclient/test` against the SABnzbd client:
  200, empty body (success).
- SABnzbd `GET /api?mode=queue` and `mode=version`: responsive, healthy
  (v5.1.0), confirming the usenet path itself was never touched and still
  works.
- Prowlarr `GET /api/v1/indexer`: the remaining list contains no BitSearch,
  TorrentGalaxyClone, Isohunt2, or iDope entries; all other indexers
  (usenet and torrent) are untouched.

## Explicitly out of scope (not touched)

- Storage layout / shared-filesystem question for `downloads`.
- sabnzbd configuration.
- Any other arr-app settings.
- Backups.
- The remaining disabled torrent indexers in Prowlarr that DO have working
  definitions (e.g. `1337x`, `The Pirate Bay`, `Nyaa.si`, etc.) - those were
  left alone since they are not "definition-less," they are simply unused.

## Reversibility

Deploying qBittorrent (or another torrent client) later remains entirely
possible: add its app directory back under `kubernetes/apps/base/downloads/`
and `kubernetes/apps/main/downloads/`, then re-add the download client in
Radarr and any indexers wanted in Prowlarr. Nothing about this change removes
that option; it only removes state that falsely implied the option was
already exercised.

## Re-verification (post-commit, no-mistakes pipeline)

Date/time: 2026-08-30T23:57:33Z (UTC) / 2026-08-30 19:58 EDT

Fresh live checks against the cluster from the no-mistakes fix step, using
`KUBECONFIG=/Users/coder/firstmate/projects/home-ops/kubeconfig`. These are
independent of the original verification section above and re-prove the
deletions still hold in application runtime state.

**Result: no contradictions.** Every check matches the original post-delete
state. No qBittorrent pod/Service, no deleted Radarr client, none of the four
deleted Prowlarr indexers, clean health (routine update notices only), and
SABnzbd still healthy on the usenet path.

1. **No qbittorrent in-cluster**
   - `kubectl -n downloads get pods,svc` listed Running pods for autobrr,
     bazarr, lidarr, prowlarr, radarr, readarr, reading-glasses, sabnzbd,
     sonarr (plus completed CronJobs) and matching Services.
   - `grep -i qbit` over that listing: **no matches** (no pod, no Service).

2. **Radarr download clients** (`GET /api/v3/downloadclient`, API key from
   Secret `radarr-secret` / `RADARR__AUTH__APIKEY`, port-forward
   `svc/radarr 17878:7878`)
   - List contains **exactly one** client: SABnzbd id **2**,
     `enable: true`, `protocol: usenet`, `implementation: Sabnzbd`,
     host `sabnzbd.downloads:8080`.
   - **No** qBittorrent entry (id 1 remains deleted).

3. **Radarr health** (`GET /api/v3/health`)
   - Clean except routine notice only:
     `UpdateCheck` warning — "New update is available: v6.4.2.10590".
   - No download-client or indexer errors.

4. **Prowlarr indexers** (`GET /api/v1/indexer`, API key from Secret
   `prowlarr-secret` / `PROWLARR__AUTH__APIKEY`, port-forward
   `svc/prowlarr 19696:9696`)
   - 16 indexers total. **None** of BitSearch, TorrentGalaxyClone, Isohunt2,
     or iDope are present (ids 1 / 7 / 81 / 80 remain deleted).
   - Deliberately-retained disabled torrent indexers still present, e.g.
     `1337x` (id 45, enable false), `The Pirate Bay` (id 4, enable false),
     `Nyaa.si` (id 9, enable false).
   - Enabled usenet indexers still present: Miatrix (12), NZBFinder (11),
     NZBgeek (14).

5. **Prowlarr health** (`GET /api/v1/health`)
   - `IndexerNoDefinitionCheck` error is **gone**.
   - Clean except routine notice only:
     `UpdateCheck` warning — "New update is available: v2.6.2.5562".

6. **SABnzbd usenet path** (API key from Secret `sabnzbd-secret` /
   `SABNZBD_API_KEY`, port-forward `svc/sabnzbd 18080:8080`)
   - `GET /api?mode=version&output=json` → `{"version": "5.1.0"}`.
   - `GET /api?mode=queue&output=json` → `status: Idle`, `paused: false`,
     `noofslots_total: 0`, empty slots. Responsive and healthy.

7. **Radarr → SABnzbd connection test**
   - `POST /api/v3/downloadclient/test?forceTest=true` with the body of
     download client id 2 (SABnzbd).
   - HTTP **200**, empty JSON body `{}` (Servarr success shape).

## Re-verification (post-commit, no-mistakes pipeline — second live pass)

Date/time: 2026-08-30T23:59:39Z (UTC)

Second independent live pass from the no-mistakes test/fix step after the
user confirmed cluster reachability via
`KUBECONFIG=/Users/coder/firstmate/projects/home-ops/kubeconfig`. Distinct
from both the original post-delete verification and the 23:57:33Z
re-verification above; full command transcript saved outside Git as
pipeline evidence.

**Result: no contradictions.** Runtime state still matches the usenet-only
decision. No qBittorrent pod/Service, no Radarr qBittorrent client, none of
the four deleted definition-less Prowlarr indexers, clean health (routine
update notices only), SABnzbd healthy, and Radarr→SABnzbd connection test
still passes.

1. **No qbittorrent in-cluster**
   - `kubectl -n downloads get pods,svc | grep -i qbit` → **NO_QBIT_MATCH**.
   - Running pods: autobrr, bazarr, lidarr, prowlarr, radarr, readarr,
     reading-glasses, sabnzbd, sonarr (+ completed CronJobs).
   - Services: same set; **no** `qbittorrent` Service.

2. **Radarr download clients** (`GET /api/v3/downloadclient`, API key from
   Secret `radarr-secret` / `RADARR__AUTH__APIKEY`, port-forward
   `svc/radarr 17878:7878`)
   - Exactly one client: SABnzbd id **2**, `enable: true`,
     `protocol: usenet`, `implementation: Sabnzbd`, host `sabnzbd.downloads`,
     port `8080`, category `movies`.
   - **No** qBittorrent entry (deleted id 1 still absent).

3. **Radarr health** (`GET /api/v3/health`)
   - Only routine `UpdateCheck` warning: "New update is available:
     v6.4.2.10590".
   - No download-client, indexer, or connection errors.

4. **Prowlarr indexers** (`GET /api/v1/indexer`, API key from Secret
   `prowlarr-secret` / `PROWLARR__AUTH__APIKEY`, port-forward
   `svc/prowlarr 19696:9696`)
   - 16 indexers total.
   - BitSearch / TorrentGalaxyClone / Isohunt2 / iDope: **ABSENT** (string
     search and ids 1 / 7 / 81 / 80 all absent).
   - Retained disabled torrent indexers still present: `1337x` (45),
     `The Pirate Bay` (4), `Nyaa.si` (9), plus others left deliberately.
   - Enabled usenet indexers still present: Miatrix (12), NZBFinder (11),
     NZBgeek (14).

5. **Prowlarr health** (`GET /api/v1/health`)
   - `IndexerNoDefinitionCheck` **gone**.
   - Only routine `UpdateCheck` warning: "New update is available:
     v2.6.2.5562".

6. **SABnzbd usenet path** (API key from Secret `sabnzbd-secret` /
   `SABNZBD_API_KEY`, port-forward `svc/sabnzbd 18080:8080`)
   - `mode=version` → `{"version": "5.1.0"}`.
   - `mode=queue` → `status: Idle`, `paused: false`, `noofslots_total: 0`,
     empty slots; responsive.

7. **Radarr → SABnzbd connection test**
   - `POST /api/v3/downloadclient/test?forceTest=true` with download client
     id 2 body.
   - HTTP **200**, body `{}` (Servarr success).
