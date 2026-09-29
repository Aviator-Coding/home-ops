---
name: kopiur-backups
description: "Read before ANY kopiur change: onboarding or retiring a claim, kubernetes/components/kopiur/** or kubernetes/apps/base/system/kopiur/**, KOPIUR_* overlays, Snapshot/SnapshotPolicy/Restore/ClusterRepository, a restore drill, or a failed backup or IndexBlobHealth alert."
---

# kopiur backups

kopiur is the backup engine. VolSync remains only on three carve-outs
(`selfhosted/paperless-ngx`, `paperless-ngx-media`, `syncthing-data`); skill
`volsync-carveouts`. Born-kopiur claims (`database/falkordb`,
`database/surrealdb`) never had VolSync. Assume kopiur-only unless the overlay
includes `components/volsync`.

The chart tag lives in
`kubernetes/apps/base/system/kopiur/app/ocirepository.yaml`. Read that file.
Comments that name an older tag are stale.

## Tripwires

1. **A `Snapshot` CR owns its kopia snapshot.** Deleting the CR deletes backup
   data. Flux `prune: true` is that shape. `onPolicyDelete` /
   `onScheduleDelete: Retain` keeps the **data**, not the CRs: prune drops the
   CR count to zero and the catalog rediscovers snapshots as
   `origin: discovered`. Creating a `Snapshot` can also evict one. r2 policies
   have no `keepHourly`, so an on-demand r2 snapshot evicts that day's newest.
   [retirement-and-removal.md](references/retirement-and-removal.md)
2. **Retirement is a PVC-ownership swap.** `components/kopiur/pvc` must carry
   `ssa: IfNotPresent` and must never carry `force: enabled`. `force` deletes
   the volume. `KOPIUR_CAPACITY` is create-time-only on a bound claim; grow
   with `kubectl patch`, and keep Git equal to the live size because a rebuild
   provisions the Git value.
3. **Mover identity is measured from the files**, not from `runAsUser`.
   `KOPIUR_PUID` / `KOPIUR_PGID` default to 1000. A mismatch fails the backup
   closed (read-only staging, no `fsGroup` fixup). Authoritative table:
   `EXPECTED_IDENTITY` in `scripts/ci/kopiur-stage3-test.py`.
   [onboarding.md](references/onboarding.md)
4. **Restore cache is a cliff.** Required capacity is
   `min(snapshot sizeBytes, ~6.2 GiB)` plus headroom. Under the plateau any
   value works; the first crossing fails terminally and never retries. A
   standing `Restore` is `ssa: IfNotPresent`, so a raised `KOPIUR_*` does not
   reach it until that object is deleted and Flux recreates it.
   [restore-and-cache.md](references/restore-and-cache.md)
5. **Deleting a `Restore` is safe. Deleting a `Snapshot` is not.** Four
   independent legs (no data-lifecycle field, read-only data flow, zero
   finalizers, none attached mid-run) are in
   [restore-and-cache.md](references/restore-and-cache.md).
6. **Drills use a new scratch `Restore`** with `target.pvc` and
   `credentialProjection.enabled: true`. The standing populator stays
   `onMissingSnapshot: Fail`. Never set `Continue`: after a cluster loss it
   provisions empty volumes and then backs that emptiness up to r2. The r2
   `ClusterRepository` stays `create.enabled: false`.
7. **Credential projection has three legs**, and all three are required. Miss
   one and the CRs stay green while the mover fails at run time.
   [credentials.md](references/credentials.md)
8. **`ClusterRepository.spec.parameters` is write-only from Git.** Removing the
   block stops asserting the value; it does not roll the live repository back.
   [repository-maintenance.md](references/repository-maintenance.md)
9. **`IndexBlobHealth=False` is epoch tuning**, not a broken maintenance run.
   Do not commit `takeoverPolicy: Force`. Do not interpolate `minDuration`
   (headroom steps). `ceph` is pinned at `4h`; `r2` is deliberately untuned.
10. **One component include protects one volume.** A second PVC needs a second
    Flux Kustomization with `APP` set to the claim name.
11. **Never put this component in a Kustomization with `wait: true`.** The
    standing `Restore` is `Ready=False` (`AwaitingPvcDataSourceRef`) for its
    whole life on an already-bound claim. `pgadmin` and
    `calibre-web-automated` keep a split shape: the backup Kustomization must
    not `dependsOn` the claim Kustomization (greenfield deadlock).
12. **Cron accepts bare `H` only.** Namespace r2 hours are fixed. Both
    schedules pin `timezone: America/New_York` so the VolSync stagger survives
    DST. [schedules-timezone.md](references/schedules-timezone.md)
13. **A `Succeeded` snapshot proves nothing until `.status.stats` covers the
    volume.** `SecurityContextCompatible` is positive-only; its absence is not
    a failure. [proof-ledger.md](references/proof-ledger.md)
14. **Both engines snapshot crash-consistently, with no application hook.** A
    database that does not fsync at the durability you want restores a healthy
    stale copy. FalkorDB on one snapshot: AOF `everysec` recovered 2174 of
    2199 nodes; stock RDB-only recovered 200 of 2199. Both startups looked
    healthy.
15. **CloudNativePG has one live barman destination per `Cluster`.** A second
    `ScheduledBackup` with its own `barmanObjectName` reports completed and
    writes to the original store (cloudnative-pg#7778). The off-site copy is a
    suspended `rclone copy` mirror, never `sync`.
    `kubernetes/apps/base/database/cloudnative-pg/offsite-mirror/README.md`.
16. **Full-cluster restore from r2** is
    [full-cluster-restore-r2.md](references/full-cluster-restore-r2.md). Do not invent a
    second procedure. Five claims have no populator path and are hand-restored;
    that runbook names them.

## Where things live

| What | Path |
|---|---|
| Component (policies, schedules, standing ceph `Restore`, pvc takeover) | `kubernetes/components/kopiur/` |
| Operator, repositories, alerts | `kubernetes/apps/base/system/kopiur/` |
| Per-claim `KOPIUR_*` | `kubernetes/apps/main/<ns>/<app>.yaml` |
| Identity, r2 hour, retired vs never-VolSync sets | `scripts/ci/kopiur-stage3-test.py` |
| Human restore (scratch) | [kopiur-scratch-drill.md](references/kopiur-scratch-drill.md) |
| Human claim rebuild | skill `volsync-carveouts`, [corrupt-claim-recreation.md](../volsync-carveouts/references/corrupt-claim-recreation.md) |
| Full-cluster r2 | [full-cluster-restore-r2.md](references/full-cluster-restore-r2.md) |
| Fleet proof rows (overlay "row N") | [proof-ledger.md](references/proof-ledger.md) |
| CI | `scripts/ci/kopiur-*.py` |

## Procedures

- Onboard or retire a claim: [onboarding.md](references/onboarding.md), [retirement-and-removal.md](references/retirement-and-removal.md).
- Size or raise cache, delete a stale populator, run a drill: [restore-and-cache.md](references/restore-and-cache.md) and [kopiur-scratch-drill.md](references/kopiur-scratch-drill.md).
- Read a green backup that might be empty or lossy: [proof-ledger.md](references/proof-ledger.md).
- `IndexBlobHealth` or a parameters change: [repository-maintenance.md](references/repository-maintenance.md).
- Leak alert or projection: [credentials.md](references/credentials.md), [alerts.md](references/alerts.md).
- Operator install, buckets, DR-mode commit: [stage0-operator.md](references/stage0-operator.md).
- Full-cluster loss, restore every volume from r2: [full-cluster-restore-r2.md](references/full-cluster-restore-r2.md).

## Verify

- `python3 scripts/ci/kopiur-stage0-test.py` through `stage5`, plus
  `kopiur-timezone-test.py`, `kopiur-epoch-tuning-test.py`,
  `kopiur-restore-cache-sizing-test.py`,
  `kopiur-projected-secrets-leak-alert-test.py`.
- `task flux:test:all`.
- After any `KOPIUR_*` change that the standing `Restore` consumes, read the
  live object. `Ready=True` on the Kustomization does not mean the populator
  moved. Deleting that `Restore` is the remedy in tripwire 5.
