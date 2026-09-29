# Plex scans and the token

## Scan triggers are not GitOps

Media is on NFS. Plex's "scan my library automatically"
(`FSEventLibraryUpdatesEnabled`) never fires for files written by another
host. Leave it off.

Two settings are on, and both live in application databases. A restore of
Plex, Radarr or Sonarr drops them. Nothing in this repo puts them back.
Re-check after a scratch restore:

1. Radarr and Sonarr, Settings, Connect, "Plex Media Server", host
   `plex.media.svc.cluster.local:32400`. Enable On Import, On Upgrade, On
   Rename, and the delete event for that app. This refreshes one folder
   after each import.
2. Plex, Settings, Library, "Update my library periodically"
   (`ScheduledLibraryUpdatesEnabled`), hourly. This covers files that never
   pass through Radarr or Sonarr.

Seerr polls Plex "recently added" and stays green while the library is frozen.
Compare the movie folder count on disk with the library `totalSize`, and how
old `scannedAt` is. With `X-Plex-Container-Size=0`, the page `size` is 0. Read
`totalSize`.

```sh
kubectl --kubeconfig="$KUBECONFIG" exec -n media deploy/plex -c app -- \
  find /data/nas-media/Movies -mindepth 1 -maxdepth 1 -type d | wc -l
```

Pass `--kubeconfig` explicitly. A mise-shimmed `kubectl` uses the worktree
kubeconfig and talks to localhost (skill `flux-gitops` notes, AGENTS.md).

`autoEmptyTrash` is on, so a scan also drops entries whose file was deleted.

## Token

`kubernetes/apps/base/media/plex/app/pushsecret.yaml` upserts `PLEX_TOKEN` on
the existing 1Password item `plex` in the Homelab vault. The store is the
shared `onepassword` ClusterSecretStore, because `onepassword-automation` only
sees the Automation vault (skill `secrets-1password`).

The source Secret `plex-token` is created once, by copying `PlexOnlineToken`
out of the server's Preferences.xml. Nothing generates it. An empty
`PLEX_TOKEN` still shows ExternalSecret `SecretSynced`. plex-exporter then
returns 500 (Plex 401 HTML) and Bazarr reads the same field. Check decoded
byte length, not status.

Do not print the token.
