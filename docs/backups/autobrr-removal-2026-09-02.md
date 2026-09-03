# Removing `downloads/autobrr` - and keeping its backups

- **Date:** 2026-09-02
- **Decision:** captain, "can you remove qbitorrent and autobrr we have not been
  using it" -> "Just delete remove."
- **Scope:** remove the app in full. **Keep every byte of its kopiur backup
  history.**

This is the first time an app protected by kopiur has been removed outright, so
the mechanics of "remove the app, keep the backups" are recorded here rather
than left to be re-derived. It is a different operation from a Stage 5
retirement, which removes an *engine* and always leaves the claim in the fleet.

qBittorrent needed no work: it was already removed on 2026-08-30 under the
usenet-only decision (`data/decisions-2026-08-30/downloads-usenet-only.md`), and
nothing named qbittorrent existed in the repo or the cluster. Verified before
acting.

## Why the backup data is kept

The captain's standing directive of 2026-08-23 holds **all** backup-data
deletions and permits them only on an explicit instruction naming the data.
"Just delete remove" names the app, not its backups, so it does not meet that
bar.

It is also the safer outcome on its own merits. VolSync was retired from this
claim earlier the same day (Stage 5 wave two), so kopiur is its only engine -
and removing the app prunes the live PVC. The kopiur repositories are therefore
the **only** remaining copy of that volume. Kept, it stays restorable.

## What `Retain` actually retains: the DATA, not the CRs

The component pins `deletion.onPolicyDelete` and `deletion.onScheduleDelete:
Retain`, and this was verified against the live CRDs before anything was
removed rather than taken on trust. The finding is sharper than the prose in
`kubernetes/components/kopiur/Readme.md` was, and that file has been corrected.

Every scheduled `Snapshot` carries a **`controller: true` ownerReference** to its
`SnapshotSchedule`:

```
ownerReferences: [{kind: SnapshotSchedule, name: autobrr-ceph,
                   controller: true, blockOwnerDeletion: false}]
finalizers:      ["kopiur.home-operations.com/snapshot-cleanup"]
spec:            {deletionPolicy: Delete, onScheduleDelete: Retain, ...}
```

So when Flux prunes the `SnapshotSchedule`, Kubernetes GC **cascade-deletes the
Snapshot CRs** and nothing prevents that. `Retain` does not stop the cascade; it
changes what the `snapshot-cleanup` finalizer does on the way out. The operator
stamps `onScheduleDelete` onto each produced Snapshot, and the CRD documents it
verbatim:

> What the deletion of a `SnapshotSchedule` does to the `Snapshot` CRs it
> produced (which Kubernetes GC cascade-deletes via their ownerReference).
> Default `Retain`: the CRs are removed but their kopia snapshots survive and
> the catalog rediscovers them as `origin: discovered`.

`SnapshotPolicy.spec.deletion.onPolicyDelete` covers the other path -
*"consulted by the Snapshot finalizer when the deletion is external and the
owning `SnapshotPolicy` is gone. Absent resolves to `Retain`."*

Three consequences worth carrying forward:

1. **Expect the Snapshot CR count for the claim to go to zero.** That is the
   documented success path, not data loss.
2. **A CR census is the wrong instrument** for proving preservation across a
   removal. The kopia snapshots are the artefact; the CRs are a catalog view of
   them, and the catalog is expected to be rebuilt by rediscovery.
3. **Nothing prunes the retained snapshots afterwards.** GFS retention is
   policy-driven, and the policy is gone. `Maintenance` (`system/ceph`,
   `system/r2`) is kopia repository upkeep - blob GC and compaction - and the
   snapshot manifests survive, so their blobs stay referenced.

`deletionProtection.threshold: 10` on both `ClusterRepository` objects is an
additional backstop: 13 snapshots exceed it, so a mass *data* deletion would be
refused rather than silently executed.

## Inventory at removal time

13 kopia snapshots, all `Succeeded`, measured immediately before the change.

| Destination | CRs | Origin | ownerReference | Fate of the CR | Fate of the kopia data |
|---|---|---|---|---|---|
| ceph | 9 (`autobrr-ceph-2026083021…` … `-20260902213617`) | `scheduled` | `SnapshotSchedule/autobrr-ceph` | GC-cascaded | **retained** (`onScheduleDelete: Retain`) |
| r2 | 3 (`autobrr-r2-20260831…`, `-20260901…`, `-20260902155521`) | `scheduled` | `SnapshotSchedule/autobrr-r2` | GC-cascaded | **retained** (`onScheduleDelete: Retain`) |
| r2 | 1 (`autobrr-r2-stage1-verify`) | `manual` | **none** | **survives as an orphan** | **retained** (`onPolicyDelete: Retain`, and absent resolves to Retain) |

The manual Stage 1 verification snapshot is the exception and is worth knowing
about: it has no ownerReference, so the cascade does not reach it. It stays in
`downloads` as a CR whose `policyRef` names a policy that no longer exists.
That is harmless - it owns real data and keeps it - but it will look anomalous
to the next person who lists Snapshots in that namespace.

Claim content at removal: **1 file, 2,179 bytes** (`config.toml`), matching each
snapshot's `status.stats` (`{"filesNew":1,"sizeBytes":2179}`) exactly. The
volume was thin by design - autobrr's real state lived in `postgres-17`.

Fleet-wide baseline for the no-collateral-damage check: 335 Snapshot CRs, 60
`SnapshotPolicy`, 60 `SnapshotSchedule`. Removing autobrr should move those by
exactly its own 13 / 2 / 2 and nothing else.

## What was removed

| Thing | Where |
|---|---|
| HelmRelease, ExternalSecret, ServiceMonitor, kustomization | `kubernetes/apps/base/downloads/autobrr/` |
| Flux `Kustomization` overlay | `kubernetes/apps/main/downloads/autobrr.yaml` |
| Namespace kustomization entry | `kubernetes/apps/main/downloads/kustomization.yaml` |
| CI gate pins | `scripts/ci/kopiur-stage{1,2,3,5}-test.py`, `kopiur-timezone-test.py` |
| Docs describing the current fleet | `AGENTS.md`, `README.md`, `docs/reference.md`, `docs/media-stack.md`, both component readmes, skill `kopiur-backups` |

Left deliberately intact: every historical record that measured autobrr on a
given date - the Stage 1/2 drill (`kopiur-restore-drill-2026-08-30.md`), the
fleet proof, the wave-two reproof and retirement records, and the
drill-document assertions in `kopiur-stage2-test.py` that pin its measured
`lastSync` and snapshot times. Those are evidence of what happened and do not
become false because the app was later removed.

`pvc-writable-check` and `backup-silent-failure-alerting` keep autobrr as
synthetic fixture data and as the historical exemplar of the empty-volume bug
class it actually caused. Those alerts are not autobrr-specific and needed no
change.

## 1Password

The `ExternalSecret` read two items from the `onepassword` ClusterSecretStore:

- **`autobrr`** - autobrr's own item (`POSTGRES_DB_NAME`,
  `POSTGRES_DB_USER_NAME`, `POSTGRES_DB_USER_PASSWORD`,
  `AUTOBRR_SESSION_SECRET`). **Now unreferenced by this repo.** It has NOT been
  deleted - 1Password is out of scope for GitOps changes and deleting it is the
  captain's call.
- **`cloudnative-pg`** - shared by every app with a database in `postgres-17`.
  **Must not be touched.**

## Post-merge operational steps

These cannot run before merge. Flux tracks `main`, so the app is live until this
lands, and the database drop specifically **cannot** be done earlier: the
running pod holds connections (`DROP DATABASE` would be refused) and the app's
`postgres-init` initContainer would simply recreate the database on its next
restart.

1. Let Flux reconcile, then confirm the app is gone:

   ```sh
   kubectl -n downloads get helmrelease,pod,pvc,externalsecret,servicemonitor | grep -i autobrr   # expect nothing
   kubectl -n downloads get kopiasp,kopiasched | grep -i autobrr                                   # expect nothing
   ```

2. Confirm the backups survived. **Do not use a CR census** - see above. The
   scheduled CRs are expected to be gone; the kopia snapshots are not:

   ```sh
   # the manual Stage 1 snapshot should still be listed as a CR
   kubectl -n downloads get kopiasnap autobrr-r2-stage1-verify

   # and the catalog should rediscover the cascaded ones
   kubectl -n downloads get kopiasnap -l 'kopiur.home-operations.com/origin=discovered'
   ```

   If rediscovery has not produced them, that is a question rather than a
   failure - the snapshots live in the repository, not in the CR - but it should
   be chased before the record is closed. No `discovered` snapshot existed
   anywhere in the fleet at the time of writing, so this path is documented but
   unexercised here.

3. Drop the database, once nothing is connected to it:

   ```sh
   PRIMARY=$(kubectl -n database get pods -l 'cnpg.io/cluster=postgres-17,role=primary' -o name | head -1)
   kubectl -n database exec "$PRIMARY" -- psql -U postgres -c "\l" | grep autobrr
   kubectl -n database exec "$PRIMARY" -- psql -U postgres -c 'DROP DATABASE autobrr;'
   kubectl -n database exec "$PRIMARY" -- psql -U postgres -c 'DROP ROLE autobrr;'
   ```

   The database is **not** declared in Git - there is no `Database` CR in this
   repo. It was created imperatively by the `postgres-init` initContainer from
   the 1Password item, which is why removing the app does not remove it and why
   this step is operational rather than a commit. Its content is not the only
   copy: `postgres-17` has its own CNPG `ScheduledBackup`
   (`kubernetes/apps/base/database/cloudnative-pg/cluster-17/scheduledbackup.yaml`),
   which is cluster-wide and untouched by this change.

4. Confirm nothing else moved: `postgres-17` still healthy and its other
   databases present; the remaining `downloads` apps still running; kopiur
   policy/schedule counts down by exactly 2 each (60 -> 58).
