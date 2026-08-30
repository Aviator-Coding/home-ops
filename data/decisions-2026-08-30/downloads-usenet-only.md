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
