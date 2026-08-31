# kopiur Backup Configuration

Reusable Flux component that backs up one PVC to the cluster's two kopiur
repositories. It is the deliberate sibling of [`../volsync`](../volsync/Readme.md)
and, for now, runs **alongside** it rather than replacing it.

Operator, repositories and credentials are **not** here - they are Stage 0, in
[`kubernetes/apps/base/system/kopiur/`](../../apps/base/system/kopiur/README.md).
This component only declares what to back up.

> **Migration status: Stages 1-2.** Live on exactly two volumes -
> `downloads/autobrr` (Stage 1 pilot) and `downloads/sabnzbd-config` (Stage 2
> fidelity subject) - both running **alongside** an untouched
> `components/volsync`. Every other volume is still VolSync-only. Stage 2's
> restore gate **passed** on 2026-08-30:
> [`docs/backups/kopiur-restore-drill-2026-08-30.md`](../../../docs/backups/kopiur-restore-drill-2026-08-30.md)
> - sabnzbd-config restored byte-identically from both ceph and r2 (2062 files,
> 2.06 GiB, per-file sha256, modes and ownership included). Do **not** onboard
> any further app: that is Stage 3 and needs its own captain decision; passing
> Stage 2 is explicitly not authorisation to begin it. `KOPIUR_PUID`/`KOPIUR_PGID`
> must match the workload that owns the claim's files, or the backup fails
> outright on any file lacking a world-read bit (kopiur fails closed; its
> admission webhook warns at apply time) - a prerequisite for every future
> onboarding, not a sabnzbd quirk (drill finding 2). The Stage 1 pilot holds
> **zero** files (all state is in `postgres-17`), so it is a valid mechanism test
> but never could prove byte-level fidelity; a `Succeeded` snapshot with
> `.status.stats sizeBytes 0` is not evidence backup works. Plan of record:
> firstmate's `homeops-kopiur-vs-volsync-scout` report, section 6.

## Directory Structure

```
components/kopiur/
├── kustomization.yaml   # Component -> ./backup  (what an app includes)
├── backup/              # Kustomization -> ../ceph ../r2  (Flux `path` for multi-claim apps)
├── ceph/                # local, in-cluster RGW: policy + schedule + Restore
└── r2/                  # offsite, Cloudflare: policy + schedule
```

Same two-level split as `../volsync`, and for the same reason: `backup/` is a
plain `Kustomization` so it can be used directly as a Flux `path`, while the
parent is the `Component` an app includes. `kustomize build` cannot render a
Component, so a Component alone could not serve the multi-claim case below.

## How it differs from `../volsync`, on purpose

| | `../volsync` | this component |
|---|---|---|
| Destinations | ceph, minio, r2 | **ceph, r2** - Stage 0 gave kopiur no MinIO repository (MinIO is being retired over its licensing change). The VolSync MinIO backups keep running. |
| Objects per claim | 3 ReplicationSource + 1 ReplicationDestination + 3 ExternalSecret + 3 Secret | 2 SnapshotPolicy + 2 SnapshotSchedule + 1 Restore - and **no credential objects at all**, standing or per-claim, because the operator mints them per run (see "Credentials") |
| Creates the app's PVC | yes (`pvc.yaml`) | **no** - VolSync still owns every claim during the parallel run, and two components creating the same PVC from `${APP}` is a kustomize collision |
| Cache PVCs | one per source, standing (matches the live VolSync `ReplicationSource` count in `../volsync/Readme.md`) | **none** - `mover.cache.mode: Ephemeral`, the cache lives only for the run |
| Stagger | hand-maintained per-app minute table + a `MutatingAdmissionPolicy` injecting `sleep $(shuf -i 0-90 -n 1)` | native hashed `H` minute + `jitter` |
| Deleting the backup object | never touches the restic repository | **can delete backup data** - see below |

### Deletion protection is load-bearing here, and it was free in VolSync

A kopiur `Snapshot` CR **owns its kopia snapshot through a finalizer**. Deleting
CRs can therefore delete real backup data - upstream ADR-0006 records a
production incident where ownerReference GC fired 600-700 concurrent snapshot
deletions at one repository. We run Flux with `prune: true`, which is exactly
that shape.

Both protective fields are pinned explicitly in Git rather than left to their
(currently safe) defaults, so neither an upstream default change nor a careless
edit can quietly arm the cascade:

* `SnapshotPolicy.spec.deletion.onPolicyDelete: Retain`
* `SnapshotSchedule.spec.deletion.onScheduleDelete: Retain`

`spec.defaultDeletionPolicy` is deliberately left at its `Delete` default - that
one governs **retention pruning** and has to be `Delete` or expired snapshots
would never reclaim space. The repository-level circuit breaker
(`deletionProtection.threshold: 10`) is pinned in Stage 0.

## Credentials - operator-minted, per-run, nothing at rest

> Captain decision 2026-08-30, key `credential-scope` (Option B). This closes
> the question Stage 1 deliberately deferred; it is the permanent shape, not
> scaffolding. **No repository credential sits at rest in any app namespace.**

A kopiur mover Job runs in the **workload** namespace and loads its repository
credentials with `envFrom`, which is namespace-local. Both `ClusterRepository`
objects pin their Secrets to `system`, so a backup in `downloads` cannot see
them - the CRs reconcile perfectly clean and the backup fails at run time. That
was a real gap found in Stage 0, and it is the failure mode to keep in mind
whenever anything in this area changes: **every CR goes green and the mover
still fails.**

The answer we run is upstream's own **credential projection**. The operator
SSA-copies the repository's credential Secret(s) into the mover's namespace for
the length of the run, owner-refs them to the `Snapshot`, and reaps them
afterwards. Exposure is one mover run, not permanent.

### Three legs, and all three are required

Projection is gated three times over, deliberately, and each leg lives in a
different file. Turning on fewer than three is the trap: with leg 1 missing the
operator surfaces an actionable `403` in the resource status, but with **leg 2
or leg 3 missing everything reconciles clean and the mover fails at run time**.

| # | What | Where | Field |
|---|---|---|---|
| 1 | Operator RBAC | [`apps/base/system/kopiur/app/helmrelease.yaml`](../../apps/base/system/kopiur/app/helmrelease.yaml) | `features.credentialProjection.enabled: true` |
| 2 | Repository-owner consent | [`apps/base/system/kopiur/repository/clusterrepository.yaml`](../../apps/base/system/kopiur/repository/clusterrepository.yaml) (**both** `ceph` and `r2`) | `credentialProjection.allowed: true` |
| 3 | Consumer opt-in | `./ceph/snapshotpolicy.yaml`, `./r2/snapshotpolicy.yaml`, `./ceph/restore.yaml` | `credentialProjection.enabled: true` |

A fourth thing is load-bearing and easy to delete by accident: every `secretRef`
in the `ClusterRepository` spec sets `namespace:` **explicitly**. The CRD
requires that for projection - without it the operator does not know what to
copy.

Because leg 3 lives in this component, **an app onboarded through
`components/kopiur` gets projection automatically**. There is nothing per-app to
add and nothing per-namespace to create: no `ExternalSecret`, no
`ClusterSecretStore`, no `dependsOn` credential edge.

### The accepted trade-off, stated plainly

Leg 1 is the only thing that adds `create`/`patch`/`delete` on `secrets` to the
operator's ClusterRole, and **`create` cannot be scoped to a Secret name**. A
compromised kopiur operator therefore becomes a **cluster-wide credential-rewrite
primitive** - it can write a Secret in any namespace it manages. The captain took
that cost with it named. It is not glossed and it is not mitigated away.

What it buys, measured against the alternative it replaced: the pilot kept three
standing credential objects in one namespace. Extending that shape to Stage 3
would have meant **18 standing credential objects across 6 app namespaces**, each
granting read **and** write to both backup repositories - so compromising any
single app namespace would have yielded the ability to read every backup and
write to both. That is the ransomware-shaped risk. Projection drops exposure
from permanent to the length of one mover run.

Two properties make the cost tolerable rather than merely accepted:

* **Observable.** The operator exports `kopiur_projected_secrets_live`. It should
  read `0` between runs, and rise only while a mover is running. A non-zero
  reading at rest means a reap did not happen - investigate, do not ignore.
  Per-run, `Snapshot.status.cleanup.credsReapedAt` records the reap, and
  `kopiur_secrets_projected_total` counts projections.
* **Reversible**, by flipping one boolean per repository (leg 2). Note that
  reverting alone leaves movers outside `system` with **no** credentials at all:
  the standing per-namespace copies are gone, so a rollback means re-creating
  them, not just unflipping a flag.

**Do not widen anything further on top of this.** The decision authorises
exactly the RBAC projection needs and nothing else. In particular it does not
authorise `features.kopiaUi`, which asks for the same unscoped Secret verbs for
a different purpose.

### What this replaced

Stage 1 unblocked the pilot with a **`downloads`-only** copy: three
`ExternalSecret`s in `apps/base/downloads/kopiur-credentials/` producing
`kopiur`, `kopiur-ceph-secret` and `kopiur-r2-secret`, fed partly by a
kubernetes-provider `ClusterSecretStore` restricted to `namespaces: [downloads]`.
All of it is **deleted** - a standing credential nothing needs is exactly what
this change exists to eliminate. Do not reintroduce any of it; if a mover cannot
reach its credentials, the fault is in one of the three legs above.

One historical detail survives the deletion and still matters: the Ceph S3 keys
are generated and rotated by Rook's `ObjectBucketClaim`, so `system/kopiur` is
their authoritative source. Stage 0's `kopiur-ceph-bucket` 1Password item stays a
**record that nothing reads**, exactly as its README says.

### Restores carry the same caveat as any `ssa: IfNotPresent` object

`./ceph/restore.yaml` carries leg 3, but it also carries
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`, so **Flux creates it once and
never reconciles it again**. Adding the field does not reach a `Restore` that
already exists in the cluster. The two live `*-kopiur-dst` objects in `downloads`
predate this change; they are inert (`target.populator`, and every claim's
`dataSourceRef` still points at VolSync's `${APP}-dst`), so nothing breaks today,
but they must be recreated or hand-patched before Stage 5 repoints anything at
them. A scratch drill `Restore` written today needs `credentialProjection.enabled:
true` in its own spec - see "Restore" below.

## Per-Application Usage

In the app's Flux Kustomization (`apps/main/<ns>/<app>.yaml`), **alongside**
volsync - not instead of it:

```yaml
  dependsOn:
    - name: volsync
      namespace: system
    - name: kopiur-repository        # applies the ceph/r2 ClusterRepositories
      namespace: system              # and itself dependsOn the operator
  components:
    - ../../../../../components/volsync
    - ../../../../../components/kopiur
  postBuild:
    substitute:
      APP: *app
```

`dependsOn: kopiur-repository` is not optional. The kopiur admission webhook is
`failurePolicy: Fail`, so with the operator down the API server **rejects** these
CRs outright rather than leaving them unreconciled.

There is deliberately **no credential dependency here**. Credentials are minted
per run by the operator and reaped afterwards - see "Credentials" above. A new
namespace needs nothing created in it.

### Apps with more than one volume

Unchanged from VolSync's constraint, because it is a Flux limitation and not an
operator one: every object here is named from `${APP}`, and Flux allows one
`postBuild.substitute` map per Kustomization. A second volume therefore needs a
second Flux Kustomization in the overlay with `path:
./kubernetes/components/kopiur/backup` and `APP` set to the claim name. See
[`../volsync/Readme.md`](../volsync/Readme.md) "Apps with more than one volume"
for the worked example - the shape is identical.

kopiur's `sources[].pvcSelector` would remove this tax, and it was measured to
work, but it is deliberately not used: `Restore.spec.source.fromPolicy` has no
`pvc` field, so a selector policy cannot say *which* of N volumes to restore -
added complexity at exactly the wrong moment.

## Configuration Variables

| Variable | Default | Notes |
|---|---|---|
| `APP` | *(required)* | names every object |
| `KOPIUR_CLAIM` | `${APP}` | source PVC when it differs from the app name |
| `KOPIUR_SNAPSHOTCLASS` | `csi-ceph-blockpool` | |
| `KOPIUR_CACHE_CAPACITY` | `2Gi` | ephemeral, discarded after each run |
| `KOPIUR_PUID` / `KOPIUR_PGID` | `1000` | must match the workload uid/gid or backup fails closed on non-world-readable files (drill finding 2); mirrors `VOLSYNC_PUID`/`PGID` defaults only |
| `KOPIUR_SCHEDULE_CEPH` | `H 1-23/4 * * *` | |
| `KOPIUR_SCHEDULE_R2` | `H 4 * * *` | |

## Schedules

Cadence matches VolSync per destination; the **hour** is offset so the two
systems cannot collide on the same claim:

| Destination | VolSync (autobrr) | kopiur | Retention |
|---|---|---|---|
| ceph | `45 */4 * * *` -> 00,04,08,12,16,20 at :45 | `H 1-23/4 * * *` -> **01,05,09,13,17,21** at a hashed minute | hourly 6, daily 14, weekly 10, monthly 6 |
| minio | `30 */6 * * *` -> 00,06,12,18 at :30 | *(no kopiur destination)* | - |
| r2 | `45 3 * * *` -> 03:45 | `H 4 * * *` -> **04:xx** | daily 30, weekly 12, monthly 12 |

Retention is copied field-for-field from each destination's VolSync `retain`
block so the two systems stay directly comparable through the parallel run.

**Do not hand-assign minutes.** `H` is Jenkins-style hashing: kopiur derives the
minute deterministically from the schedule's identity, which spreads 100+ future
volumes across the hour by itself. That is the native replacement for VolSync's
stagger table. Note kopiur accepts **bare `H` only** - the Jenkins range form
`H(0-19)` is rejected by the webhook (`CronPattern contains illegal character
'H'`), which is why the hour field, not a bounded minute, does the separating.
`jitter: 5m` decorrelates the actual firing instant on top of the hash.

## Restore

`ceph/restore.yaml` is the standing restore-target-in-waiting, kopiur's
counterpart to VolSync's `ReplicationDestination`. Only the local ceph
destination gets one, the same asymmetry VolSync has.

It is **passive for the whole parallel run**: `target.populator` means "wait to
be claimed by a PVC's `spec.dataSourceRef`", and every claim's `dataSourceRef`
still points at VolSync's `${APP}-dst`. It is inert until migration Stage 5.

It carries `policy.onMissingSnapshot: Continue`, a deliberate departure from the
CRD's `Fail` default: `Continue` provisions an empty volume when no snapshot
exists yet, which is what lets a first deploy of a brand-new app work at all.
The trade-off is that a restore finding nothing yields an empty volume silently.

**Never use it for a drill.** Per
[`docs/backups/restore-drill-2026-08-23.md`](../../../docs/backups/restore-drill-2026-08-23.md)
a drill must create a new, uniquely named target and never touch a live claim or
the standing destination. Use a scratch `Restore` with `target.pvc`, which makes
its own PVC and is fail-closed:

```yaml
apiVersion: kopiur.home-operations.com/v1alpha1
kind: Restore
metadata:
  name: autobrr-drill-20260830
  namespace: downloads
spec:
  repository: {kind: ClusterRepository, name: ceph}
  # Required, and easy to forget in a hand-written manifest: there are no
  # standing repository credentials in any app namespace, so a mover with no
  # projection opt-in has nothing to authenticate with. See "Credentials".
  credentialProjection: {enabled: true}
  source: {fromPolicy: {name: autobrr-ceph, offset: 0}}
  target:
    pvc: {name: autobrr-drill-restored, accessModes: [ReadWriteOnce], capacity: 5Gi}
```

The standing Restore also carries `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`,
so Flux creates it once and never reconciles it again - any manual edit persists
silently forever. A live VolSync `ReplicationDestination` has been drifted this
way since October 2025.

## Rollback

Either of these leaves VolSync completely untouched and loses nothing, because
VolSync is still backing up every volume this component covers:

1. **Suspend** - `spec.suspend: true` on the `SnapshotPolicy` (or
   `spec.schedule.suspend: true` on the `SnapshotSchedule`). Stops new
   snapshots, keeps existing ones.
   ```sh
   kubectl -n downloads patch snapshotpolicy autobrr-ceph --type=merge -p '{"spec":{"suspend":true}}'
   ```
2. **Remove** - drop `../../../../../components/kopiur` from the app's Flux
   Kustomization. Because `deletion.onPolicyDelete`/`onScheduleDelete` are
   pinned to `Retain`, the Flux prune that follows deletes the policy and
   schedule but **not** the `Snapshot` CRs or their kopia data.

## Troubleshooting

```sh
# Status of both destinations for one app
kubectl -n downloads get snapshotpolicy,snapshotschedule,snapshot -l app.kubernetes.io/name=autobrr

# Did it actually run, and did it succeed?
kubectl -n downloads get snapshot -o wide

# The mover Job's own logs
kubectl -n downloads logs -l app.kubernetes.io/managed-by=kopiur --tail=100

# Repositories (cluster-scoped, Stage 0)
kubectl get clusterrepository -o wide

# Confirm the parallel run is intact - VolSync must still be green
kubectl -n downloads get replicationsource
```

A `Ready=False` on a kopiur CR can be **stale**: controller-runtime backs a
failing reconcile off exponentially, so the condition keeps reporting the
original error long after the cause is cleared. Compare `lastTransitionTime`
against `date -u` before believing it, and
`kubectl -n system rollout restart deploy/kopiur-controller` to force an
immediate reconcile.

## References

* Stage 0 (operator, repositories, credentials): [`kubernetes/apps/base/system/kopiur/README.md`](../../apps/base/system/kopiur/README.md)
* The system this runs beside: [`../volsync/Readme.md`](../volsync/Readme.md)
* Restore drill procedure and its hard constraints: [`docs/backups/restore-drill-2026-08-23.md`](../../../docs/backups/restore-drill-2026-08-23.md)
* Stage 2 restore gate (both destinations, both findings): [`docs/backups/kopiur-restore-drill-2026-08-30.md`](../../../docs/backups/kopiur-restore-drill-2026-08-30.md)
* Upstream docs: <https://kopiur.home-operations.com>
