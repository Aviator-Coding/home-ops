---
name: volsync-carveouts
description: "Read before editing kubernetes/components/volsync/**, the paperless-ngx, paperless-ngx-media or syncthing-data overlays, a ReplicationSource or ReplicationDestination, running just kube restore, or touching scripts/volsync-retired-expiry/**. Covers latestImage frozen at first deploy, deleting the RD with the PVC, restores relaxing file modes, and exact-segment matching for retired repo expiry."
---

# VolSync carve-outs: the three dual-engine claims and restores

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`kubernetes/components/volsync/Readme.md`](../../../kubernetes/components/volsync/Readme.md) - component, defaults, multi-volume
- [`docs/backups/restore-drill-2026-08-23.md`](../../../docs/backups/restore-drill-2026-08-23.md) - proven restore procedure
- [`docs/backups/corrupt-claim-recreation-runbook.md`](../../../docs/backups/corrupt-claim-recreation-runbook.md) - claim recreation runbook
- [`docs/backups/volsync-retired-repository-expiry.md`](../../../docs/backups/volsync-retired-repository-expiry.md) - retired repository expiry decision
- [`docs/backups/volsync-retired-expiry-apply-plan.md`](../../../docs/backups/volsync-retired-expiry-apply-plan.md) - expiry apply plan (not executed)

`AGENTS.md` entries (search for the opening words):

- Volsync triple-backup (3 carve-outs only)
- VolSync restore is proven end-to-end
- `${APP}-dst.status.latestImage` is frozen at first-deploy time
- A VolSync restore permanently relaxes every file mode
- Nothing expires a retired VolSync restic repository
- An app/mover identity mismatch does NOT mean the VolSync backups are incomplete

## Related skills

- `kopiur-backups`
- `pvc-integrity-checks`
