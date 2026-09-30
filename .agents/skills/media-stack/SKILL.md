---
name: media-stack
description: "Read before changing apps in kubernetes/apps/base/downloads/** or media/plex, seerr or calibre: Sonarr, Radarr, SABnzbd, Prowlarr, Recyclarr, Bazarr, Plex settings, or running a backlog search. Covers usenet-only by decision, Recyclarr matching by trash_id, Radarr never searching for missing movies on its own, sizing a search against free disk, and no hardlinks over CephFS/NFS."
---

# Media stack: *arr apps, SABnzbd, Recyclarr and Plex

The downloads namespace is usenet-only. Quality profiles, library scans and
which movie sits on which profile are application database state, not Git.

## Tripwires

1. **Usenet only.** No torrent client is deployed. Radarr's disabled
   qBittorrent client and Prowlarr's four definition-less torrent indexers
   were deleted through the apps' APIs. Re-adding a torrent path is a new
   decision, not a leftover to "finish". [arr-settings.md](references/arr-settings.md)
2. **Recyclarr `assign_scores_to` matches `trash_id`, never profile `name`.**
   A TRaSH rename orphans name-matched scores onto the old profile and creates
   an empty new one. [recyclarr.md](references/recyclarr.md)
3. **Radarr never searches for a monitored movie that has no file.** There is
   no missing-movie task, and import lists run `searchOnAdd: false`. Size a
   backlog search against free disk before running it.
   [backlog-search.md](references/backlog-search.md)
4. **Hardlinks across CephFS and NFS are impossible.** Imports copy. Budget
   double disk for the copy window.
5. **Do not measure or delete `shared-downloads` from the SABnzbd pod.** Its
   RBD incomplete mount shadows the CephFS path. Use the Radarr pod. The
   human steps are [sabnzbd-disk-space.md](references/sabnzbd-disk-space.md).
6. **Plex does not see NFS filesystem events.** `FSEventLibraryUpdatesEnabled`
   looks right and does nothing. Scan triggers are Radarr/Sonarr Connect plus
   Plex's periodic update, and both live in app databases.
   [plex.md](references/plex.md)
7. **The Plex token is a PushSecret into the existing `plex` 1Password item,
   not a generated secret.** An empty `PLEX_TOKEN` still reports
   `SecretSynced` and breaks plex-exporter and Bazarr. Check decoded length.
   [plex.md](references/plex.md)
8. **Tdarr library scope and flow state are not in Git.** `processTranscodes`
   on the library is the scope gate. `librariesToNotProcess` is a Tdarr Pro
   feature and a no-op on this unlicensed install. Skill `tdarr-transcoding`.
   Keep `transcodecpuWorkers` at least 1.
9. **Only Readarr runs an exportarr sidecar (metrics port 9707).** Sonarr and Radarr have none:
   exportarr v2.3.0 rejects any API key that is not 20-32 alphanumeric characters, and
   their current keys contain symbols. Rotate to alphanumeric keys before retrying.

## Where things live

| What | Path |
|---|---|
| *arr, SABnzbd, Prowlarr, Recyclarr, Bazarr | `kubernetes/apps/base/downloads/` |
| Recyclarr config | `kubernetes/apps/base/downloads/recyclarr/app/config/recyclarr.yml` |
| Shared downloads PVC and janitor | `kubernetes/apps/base/downloads/pvc/`, `downloads/maintenance/` |
| Plex, Seerr, Calibre, Tdarr | `kubernetes/apps/base/media/` |
| Plex token PushSecret | `kubernetes/apps/base/media/plex/app/pushsecret.yaml` |
| Disk-full runbook (alert text links here) | `.agents/skills/media-stack/references/sabnzbd-disk-space.md` |
| VA-API check after a GPU change | skill `intel-gpu`, [vaapi-check.md](../intel-gpu/references/vaapi-check.md) |

## Procedures

- Quality profiles and TRaSH scores: [recyclarr.md](references/recyclarr.md).
- Missing-movie backlog: [backlog-search.md](references/backlog-search.md).
- Plex scans and the token: [plex.md](references/plex.md).
- Paths, categories, hardlinks: [arr-settings.md](references/arr-settings.md).
- SABnzbd out of disk, ghost tree, janitor: [sabnzbd-disk-space.md](references/sabnzbd-disk-space.md).

## Verify

- `python3 scripts/ci/docs-budget-test.py` and `python3 scripts/ci/doc-links-test.py`.
- Tdarr behaviour: `node docs/tdarr/flow-nodes/behavior-test.js` (do not edit
  `docs/tdarr/flow-nodes/`).
- A profile change does not assign movies. That is a Radarr API pass, documented
  in [recyclarr.md](references/recyclarr.md).
