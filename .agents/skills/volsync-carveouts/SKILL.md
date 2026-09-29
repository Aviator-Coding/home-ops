---
name: volsync-carveouts
description: "Read before editing kubernetes/components/volsync/**, the paperless-ngx, paperless-ngx-media or syncthing-data overlays, a ReplicationSource or ReplicationDestination, just kube restore, or scripts/volsync-retired-expiry/**."
---

# VolSync carve-outs

VolSync is the second engine on three claims, and nowhere else. Do not add
`components/volsync` to a new app. kopiur is the norm (skill
`kopiur-backups`). Delete this skill when those three claims leave VolSync.

The three: `selfhosted/paperless-ngx` (permanent), `paperless-ngx-media`,
`syncthing-data`. Each still gets three `ReplicationSource` objects
(ceph, minio, r2) and one `ReplicationDestination`.

## Tripwires

1. **`${APP}-dst.status.latestImage` is frozen at first apply.** The
   destination runs `trigger.manual: restore-once` once, then
   `ssa: IfNotPresent` stops Flux from reconciling it. `pvc.yaml` populates
   from that VolumeSnapshot, not from the restic repository. If the first run
   logged `No eligible snapshots found`, `latestImage` is an empty volume
   forever. Deleting only the PVC restores nothing while `Bound`, `Running`
   and `Ready` all stay green. Delete the `ReplicationDestination` together
   with the PVC. Read `status.latestMoverStatus.logs` and `status.lastSyncTime`
   before trusting `dataSourceRef`.
   [restore-procedures.md](references/restore-procedures.md)
2. **Never patch `<app>-dst` to trigger a restore.** The manual edit persists
   forever and can drift `spec.restic.repository`. Create a new, uniquely
   named scratch `ReplicationDestination` plus PVC against the existing
   read-only credential Secret. Live restore into the app claim is
   `just kube restore <namespace> <app>`, which derives `<app>-manual` and
   does not patch `<app>-dst`.
3. **A restore relaxes every mode by one group-write bit** (`644` to `664`,
   `600` to `660`, `755` to `775`). The mover stages the destination writable,
   so kubelet's `fsGroup` walk runs before restic writes. Ownership is
   unchanged. kopiur restores keep the original modes. Do not use a VolSync
   restore to reproduce a permissions bug.
4. **Deleting a `ReplicationSource` never touches the restic repository.**
   That is why retirement is survivable, and why a retention policy cannot
   empty a frozen repository.
5. **`paperless-ngx` still has `${VOLSYNC_CLAIM:-*app}`.** That alias is
   armed on purpose. Removing `VOLSYNC_CLAIM` makes `flate` fail the
   Kustomization. `syncthing` and `syncthing-data` are different claims in one
   overlay file.
6. **Retired-repository expiry is NOT IN FORCE.** Nothing in Git applies it.
   Rook's ObjectBucketClaim controller has no update path, so
   `bucketLifecycle` on the `volsync` bucket is a silent no-op. Match a
   repository by the exact path segment `<name>/`. `volsync/syncthing` is a
   string prefix of `volsync/syncthing-data`.
   [retired-repo-expiry.md](references/retired-repo-expiry.md)
7. **A uid mismatch does not mean the restic backup is incomplete.** Writable
   staging lets `fsGroup` add group-read before restic opens the file. Measure
   with a restore, or with `find <mount> -type f ! -readable` as the mover
   uid. kopiur fails closed on the same mismatch.
8. **One component include is one volume.** A second PVC is a second
   Kustomization with `path: ./kubernetes/components/volsync/backup` and `APP`
   set to the claim name. [multi-volume.md](references/multi-volume.md)
9. **`VOLSYNC_CACHE_CAPACITY`** is 20-50% of the PVC for large claims and
   50-100% for small ones. Cache is a mover scratch volume, not the kopiur
   restore-cache cliff.

## Where things live

| What | Path |
|---|---|
| Component | `kubernetes/components/volsync/` |
| Operator, admission policy, alerts | `kubernetes/apps/base/system/volsync/` |
| Carve-out overlays | `kubernetes/apps/main/selfhosted/paperless-ngx.yaml`, `syncthing.yaml` |
| Scratch restore drill | [volsync-scratch-drill.md](references/volsync-scratch-drill.md) |
| Corrupt-claim rebuild | [corrupt-claim-recreation.md](references/corrupt-claim-recreation.md) |
| Expiry decision (not applied) | [retired-repo-expiry.md](references/retired-repo-expiry.md) |
| Expiry apply (needs a go-ahead) | [retired-expiry-apply-plan.md](references/retired-expiry-apply-plan.md) |
| Ledger the apply plan reads | `scripts/volsync-retired-expiry/ledger.yaml` |
| Gate | `scripts/ci/volsync-retired-expiry-test.py` |

## Verify

- `python3 scripts/ci/volsync-retired-expiry-test.py`
- `python3 scripts/ci/corrupt-claim-recreation-contract-test.py`
- Do not `kubectl get secret … -o yaml` to "check repository access". That
  prints credentials. Confirm the Secret exists and that a mover Job
  authenticated. Skill `secrets-1password`.
