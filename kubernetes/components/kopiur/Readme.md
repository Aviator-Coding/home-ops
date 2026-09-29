# kopiur component

Per-claim backup objects: `SnapshotPolicy`, `SnapshotSchedule`, the standing
ceph `Restore`, and (for a VolSync retirement) the PVC takeover. The operator
and repositories are `kubernetes/apps/base/system/kopiur/`.

Read skill `kopiur-backups` before changing this directory. The tripwires are
in `SKILL.md`. Procedures are in `references/`.

## Invariants

- One include protects one volume. A second PVC is a second Flux Kustomization
  with `APP` set to the claim name. See skill `volsync-carveouts`,
  `references/multi-volume.md`, for the same shape on the three carve-outs.
- `pvc/` carries `ssa: IfNotPresent` and must never carry `force: enabled`.
  `force` deletes the volume.
- `KOPIUR_PUID` / `KOPIUR_PGID` default to 1000. Measure file ownership. A
  mismatch fails the backup closed.
- Deleting a `Snapshot` CR deletes kopia data. `onScheduleDelete: Retain`
  keeps the data, not the CRs.
- The standing `Restore` stays `Ready=False` for its whole life on a bound
  claim. Do not put this component in a Kustomization with `wait: true`.

Fleet proof rows ("row N") are
`.agents/skills/kopiur-backups/references/proof-ledger.md`.
Human restore: `docs/backups/kopiur-restore-runbook.md`.
Corrupt-claim rebuild: `docs/backups/corrupt-claim-recreation-runbook.md`.
