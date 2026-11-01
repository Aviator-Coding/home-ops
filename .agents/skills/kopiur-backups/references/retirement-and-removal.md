# Retirement and removal

## Current fleet

30 VolSync engines were retired across four waves. `downloads/autobrr` and
`downloads/readarr` then left with their apps, so **28 retired claims remain**,
all kopiur-only. No dual-engine claim remains: the last,
`selfhosted/paperless-ngx`, left on 2026-11-01 (issue #2202 PR-4), and the
operator is removed separately (PR-6). Born-kopiur claims sit outside that set
(`NEVER_VOLSYNC` in `scripts/ci/kopiur-stage3-test.py`).

The machine-readable record is `RETIRED_CLAIMS` in that same test, asserted
both ways. Do not leave a removed app in the set: a row with no overlay reads
as a volume with no backup.

Wave one (regenerable): `ai/repo-wiki`, `downloads/recyclarr-config`,
`downloads/sabnzbd-config`, `media/seerr`. Wave two: `downloads/prowlarr-config`,
`selfhosted/ntfy`, `selfhosted/obsidian-livesync` (a real vault; retired on an
explicit captain decision). Wave three took the remaining eligible claims.
Tiering was sequencing, not a different evidence standard. Every retired claim
was a destination-identical pass in [proof-ledger.md](proof-ledger.md), with
the prowlarr caveat closed by the later matched-pair drill recorded there.

Wave four, 2026-11-01: `selfhosted/paperless-ngx-media` (issue #2202 PR-2), the
first **backup-only** retirement. It had been left dual-engine because its restore
cache did not cover a real payload. The cache was raised to the claim size
(50Gi), a scratch drill from ceph and r2 at that cache passed (owner decision
(b), the `*` near-empty row in [proof-ledger.md](proof-ledger.md)), and then the
VolSync Kustomization (`path: components/volsync/backup`) was deleted. The claim
is chart-owned (HelmRelease `persistence.media`), so there is no PVC swap: do
**not** add `components/kopiur/pvc`, which would give a chart-owned claim a
second owner. Merging it prunes 3 ExternalSecrets, 3 ReplicationSources and 1
ReplicationDestination; the restic cache PVCs are owned by their
ReplicationSource and go with them, and the restic repositories are never
touched (ledger tier `redundant`, expiry NOT IN FORCE). The test shape is the
`owner_hr` row in `scripts/ci/kopiur-stage5-test.py`.

`selfhosted/syncthing-data` (PR-3) followed the same way, 2026-11-01, at a 15Gi
cache (drill in [proof-ledger.md](proof-ledger.md)). Its claim is a plain
`PersistentVolumeClaim` in `base/selfhosted/syncthing/app/pvc.yaml`, created by
the `syncthing` Kustomization (not by the deleted one, and not a HelmRelease), so
the stage 5 row names that file as `owner_hr` and the claim name as `owner_key`.
Merging it prunes the same 7 VolSync objects plus 3 restic cache PVCs, and never
touches the PVC. PR-3 also repointed the expiry applier's credentials, because the
Secrets it read are pruned here ([retired-expiry-apply-plan.md](retired-expiry-apply-plan.md)).
See [restore-and-cache.md](restore-and-cache.md).

`selfhosted/paperless-ngx` (PR-4) closed the wave, 2026-11-01, and is the one
**PVC takeover** of it: the normal shape below, not backup-only. Its claim came
from `components/volsync/pvc.yaml` in the `paperless-ngx` Kustomization, and
`components/kopiur/pvc` replaced it there under the same name (`KOPIUR_CAPACITY:
10Gi`, equal to the live claim), so the inventory ID never changed and Flux
skips the live claim. The HelmRelease alias became
`${KOPIUR_CLAIM:-paperless-ngx}`. Its drill also covered the populator path
([proof-ledger.md](proof-ledger.md) row 27). Merging it prunes 3 ExternalSecrets,
3 ReplicationSources, 1 ReplicationDestination and (by owner reference) 3 restic
cache PVCs. The live claim keeps an inert `dataSourceRef` to the pruned
`paperless-ngx-dst`.

## The swap

`components/volsync/pvc.yaml` emitted the claim on a dual-engine app (none is left).
Every overlay runs `prune: true`. Removing the volsync component without a
replacement makes Flux **delete the PVC**.

`components/kopiur/pvc` exists as its own component so it never collided
with volsync's PVC on apps that were still dual-engine. It must carry
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent` and must never carry
`force: enabled`.

`dataSourceRef` is immutable on a bound PVC. A server-side dry-run that sets
or drops it returns `spec: Forbidden: spec is immutable after creation except
resources.requests and volumeAttributesClassName`. `IfNotPresent` keeps the
claim in the inventory and skips the apply. The live claim keeps an inert
VolSync `dataSourceRef`. That field is read once, at provision time. A rebuilt
claim is created from this file and is seeded from `${APP}-kopiur-dst`.

`KOPIUR_CAPACITY` is therefore create-time-only. Grow a live claim with
`kubectl patch pvc` (`resources.requests.storage`). Keep the Git value equal
to the live claim: it is what a rebuild provisions.
`scripts/ci/kopiur-stage5-test.py` asserts the pair. Worked growth:
`ai/hermes` 25Gi to 40Gi, cache 16Gi to 48Gi, in the same change.

## What `Retain` retains

`deletion.onPolicyDelete` and `deletion.onScheduleDelete: Retain` are pinned
on every policy and schedule in the component.

Every `Snapshot` has a `controller: true` ownerReference to its
`SnapshotSchedule`. Deleting the schedule (what Flux prune does) cascade-deletes
the CRs. `Retain` changes the `snapshot-cleanup` finalizer: the CRs go away,
the kopia snapshots survive, and the catalog rediscovers them as
`origin: discovered`. With the policy gone, GFS does not prune them either.

A CR census of zero is the success path, not data loss. Do not verify a
removal by counting `Snapshot` objects.

`downloads/autobrr` was removed as an app, which is not a Stage 5 retirement.
Its kopia snapshots were kept under `Retain`. Nothing in Git declares that
claim any more. Do not put it back into `RETIRED_CLAIMS`.

## Removing an app

1. Do not delete `Snapshot` CRs by hand.
2. Remove the overlay and the component includes together with the app.
3. Expect the CR count for that claim to hit zero.
4. Confirm preservation in the repository (kopia snapshots still listed), not
   in the CR list.
5. Retired VolSync restic repositories are **not** expired by this
   ([retired-repo-expiry.md](retired-repo-expiry.md)). Deleting a
   `ReplicationSource` never touches restic.

## Rollback of a retirement commit

Revert the overlay commit. Flux recreates that claim's `ReplicationSource`,
`ReplicationDestination` and `ExternalSecret` objects. The claim goes back to
being emitted by `components/volsync/pvc.yaml`, which is the same name and the
same `dataSourceRef` the live claim already has, so the apply is clean. No
restic content was deleted, and no `Snapshot` CR is touched by the retirement
or by reverting it.
