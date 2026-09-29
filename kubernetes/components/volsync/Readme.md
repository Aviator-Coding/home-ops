# VolSync component

Second engine on three claims only: `selfhosted/paperless-ngx`,
`paperless-ngx-media`, `syncthing-data`. Do not add it to a new app.

Read skill `volsync-carveouts` before changing this directory.

## Apps with more than one volume

One include is one volume. A second PVC is a second Kustomization with
`path: ./kubernetes/components/volsync/backup` and `APP` set to the claim
name. `syncthing` and `syncthing-data` are different claims in one overlay
file. Skill reference `references/multi-volume.md`.

## Invariants

- `${APP}-dst` is `ssa: IfNotPresent` and `trigger.manual: restore-once`.
  `latestImage` freezes at first apply. Delete the `ReplicationDestination`
  together with the PVC. Never patch `<app>-dst` to force a restore.
- A restore relaxes every mode by one group-write bit. kopiur restores do not.
- Deleting a `ReplicationSource` does not touch the restic repository.
- Do not `kubectl get secret … -o yaml` to check repository access. That
  prints credentials. Confirm the Secret exists and that a mover Job
  authenticated.

Scratch restore: `docs/backups/restore-drill-2026-08-23.md`.
Retired-repository expiry is not in force:
`docs/backups/volsync-retired-repository-expiry.md`.
