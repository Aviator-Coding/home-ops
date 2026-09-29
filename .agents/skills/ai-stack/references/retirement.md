# Retiring or reviving an `ai` app

`git revert` restores manifests. It does not restore PVC contents. Flux
Kustomizations here are `prune: true`, so a removed claim is deleted.
`reclaimPolicy: Delete` then destroys the Ceph image. A Helm-owned PVC
without `helm.sh/resource-policy: keep` is deleted by `helm uninstall`
as well.

## Before deleting manifests

1. `grep -rn '<app>' scripts/ci/`. Gates that `path.is_file()` a
   HelmRelease go red on a missing file. `task flux:test:all` does not
   run those tests. Update the gate in the same change, or the invariant
   it protects if the app was only an example.
2. Decide the volumes explicitly. "Remove the app" does not say whether
   the data goes. ComfyUI's removal destroyed the claims on purpose
   (models and output were never backed up; user, custom-nodes, and
   input survive only as retained kopia snapshots).
3. Orphaned objects survive `helm uninstall` when they were not in the
   release: HTTPRoutes, ExternalSecrets, extra PVCs, Gatus exclusions.
   List them.
4. Do not delete a kopiur `Snapshot` CR. It owns its kopia snapshot
   through a finalizer. With `deletion.onPolicyDelete: Retain` and
   `onScheduleDelete: Retain`, prune drops the policy and schedule, GC
   drops the Snapshot CRs, and the catalog rediscovers snapshots as
   `origin: discovered`. A zero CR count is the success path. Skill
   `kopiur-backups`.
5. A standing `Snapshot` CR that was **not** GC'd must stay. The
   agentmemory one is that case: policy and schedule were pruned, the
   CR was retained on purpose.
   - Snapshot CR: `ai/agentmemory-ceph-20260831215039`
   - kopia id: `b383822fe09da5adeaf99997bc977845`
   - 716,155,105 bytes, source `/pvc/agentmemory`
   Deleting that CR deletes the backup. Three VolSync restic repos named
   `volsync/agentmemory` (ceph, minio, r2) also survive, because deleting
   a `ReplicationSource` never touches its repository. Expiring them is
   skill `volsync-carveouts`, exact path segment, never a prefix.
6. A namespace-wide privilege that existed for the retired app comes off
   with it. `kopiur.home-operations.com/privileged-movers` was on `ai`
   only for comfyui's root-owned claims. No remaining `ai` claim asks
   for a root mover (`hermes` 10000, `opencode` and `repo-wiki` 1000).
   `home-automation` keeps its own grant.

## Do not edit on the way out

`kubernetes/apps/base/system/generic-device-plugin/app/config/config.yaml`
is a `configMapGenerator` with the name-suffix hash enabled, mounted
`subPath`. A comment-only edit changes the ConfigMap name and rolls the
device-plugin DaemonSet. Leave historical mentions. Skill `intel-gpu`.

`kubernetes/apps/base/rook-ceph/.../cephfs-rwx-subvolumegroup.yaml` is the
`csi-rwx` group for every RWX volume. Deleting it is a cluster outage.
An inventory comment is not a reason to touch the CR.

## What a revival needs

Re-add the manifests, then restore into a new PVC from the retained
snapshot **before** the app writes an empty volume. A fresh claim does
not populate itself from a retired repo. VolSync `latestImage` frozen at
first deploy is skill `volsync-carveouts`. kopiur restore cache sizing
is skill `kopiur-backups`.

`agentmemory` is not a compatibility target. A future memory backend for
OpenCode or Hermes is a new integration. Hermes' own long-term memory is
the `holographic` provider in its image (human notes in the Hermes README).

ComfyUI model checkpoints are re-downloadable. Captain-authored
`comfyui-user` settings exist only in the retained kopia snapshots.
ComfyUI and chat cannot share the B70 without collapsing decode. Skill
`b70-llm-serving`. Do not revive it onto that card beside `ai/vllm`.
