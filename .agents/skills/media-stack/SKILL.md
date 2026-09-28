---
name: media-stack
description: "Read before changing apps in kubernetes/apps/base/downloads/** or media/plex, seerr or calibre: Sonarr, Radarr, SABnzbd, Prowlarr, Recyclarr, Bazarr, Plex settings, or running a backlog search. Covers usenet-only by decision, Recyclarr matching by trash_id, Radarr never searching for missing movies on its own, sizing a search against free disk, and no hardlinks over CephFS/NFS."
---

# Media stack: *arr apps, SABnzbd, Recyclarr and Plex

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`docs/media-stack.md`](../../../docs/media-stack.md) - media stack reference
- [`docs/downloads/sabnzbd-disk-space-runbook.md`](../../../docs/downloads/sabnzbd-disk-space-runbook.md) - SABnzbd disk-space runbook
- [`data/decisions-2026-08-30/downloads-usenet-only.md`](../../../data/decisions-2026-08-30/downloads-usenet-only.md) - usenet-only decision

`AGENTS.md` entries (search for the opening words):

- A recyclarr `assign_scores_to` entry must match by `trash_id`
- Radarr never searches for a monitored-but-missing movie on its own

## Related skills

- `tdarr-transcoding`
- `intel-gpu`
