# kopiur Backup Configuration

Reusable Flux component that backs up one PVC to the cluster's two kopiur
repositories. It is the deliberate sibling of [`../volsync`](../volsync/Readme.md)
and, for now, runs **alongside** it rather than replacing it.

Operator, repositories and credentials are **not** here - they are Stage 0, in
[`kubernetes/apps/base/system/kopiur/`](../../apps/base/system/kopiur/README.md).
This component only declares what to back up.

> **Migration status: Stage 1 (pilot).** Exactly one volume - `downloads/autobrr`
> - is on this component. Every other volume in the cluster is still backed up
> by VolSync alone, and autobrr is backed up by **both**. Do not add a second app
> here without the migration's Stage 2 restore proof. Plan of record:
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
| Objects per claim | 3 ReplicationSource + 1 ReplicationDestination + 3 ExternalSecret + 3 Secret | 2 SnapshotPolicy + 2 SnapshotSchedule + 1 Restore |
| Creates the app's PVC | yes (`pvc.yaml`) | **no** - VolSync still owns every claim during the parallel run, and two components creating the same PVC from `${APP}` is a kustomize collision |
| Cache PVCs | one per source, standing (105 PVCs / 324 GiB fleet-wide) | **none** - `mover.cache.mode: Ephemeral`, the cache lives only for the run |
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

## Credentials - a known, deliberate, PILOT-ONLY compromise

> **This is scaffolding with an open decision behind it, not the permanent
> design. Do not extend it to another namespace.** Captain decision 2026-08-30,
> key `kopiur-workload-ns-credentials`.

A kopiur mover Job runs in the **workload** namespace and loads its repository
credentials with `envFrom`, which is namespace-local. Both `ClusterRepository`
objects pin their Secrets to `system`, so a backup in `downloads` cannot see
them - the CRs reconcile perfectly clean and the backup fails at run time. This
was a real gap in Stage 0, found while building this component.

Upstream's own answer is **credential projection**, where the operator copies the
Secret into the mover's namespace for the duration of a run and reaps it after.
That is the better end state and it is **deferred, not rejected**: turning it on
requires granting the operator unscoped Secret create/patch/delete cluster-wide,
which makes a compromised backup operator a cluster-wide credential-rewrite
primitive. That is a permanent, security-sensitive architecture change, and
making it to unblock a single 5Gi pilot volume is the wrong order of operations.

So the pilot instead copies the credentials into `downloads` only:

* `kubernetes/apps/base/downloads/kopiur-credentials/` - three `ExternalSecret`s
  producing `kopiur`, `kopiur-ceph-secret` and `kopiur-r2-secret`, under the
  exact names the `ClusterRepository` specs reference.
* `kubernetes/apps/base/security/external-secrets/stores/kopiur-system-secrets/` -
  a kubernetes-provider `ClusterSecretStore` reading `system`, **restricted to
  `namespaces: [downloads]`**. That restriction is load-bearing: `system` also
  holds `cluster-secrets`, which every Flux `postBuild` substitution reads.

The Ceph S3 keys are mirrored from the ObjectBucketClaim's own generated Secret
(`system/kopiur`), not from 1Password. Rook generates and rotates them, so the
OBC Secret is the authoritative source; Stage 0's `kopiur-ceph-bucket` 1Password
item stays a **record that nothing reads**, exactly as its README says.

### The accepted risk, stated plainly

At pilot scale this is three objects in one namespace - negligible. **At Stage 3
it would mean standing backup credentials in ~19 app namespaces, so compromising
any single app namespace would yield read/write access to the backup
repositories.** That is the ransomware-shaped risk, and it is precisely why the
permanent shape is deferred to a decision rather than defaulted into by
inheritance. The captain chooses it before Stage 3, when the cost actually
appears.

**Anyone onboarding a second namespace must resolve that decision first.** This
section exists so the compromise cannot become the permanent design by inertia.

## Per-Application Usage

In the app's Flux Kustomization (`apps/main/<ns>/<app>.yaml`), **alongside**
volsync - not instead of it:

```yaml
  dependsOn:
    - name: volsync
      namespace: system
    - name: kopiur-repository        # applies the ceph/r2 ClusterRepositories
      namespace: system              # and itself dependsOn the operator
    - name: kopiur-credentials       # PILOT ONLY - see "Credentials" above.
      namespace: downloads           # A new namespace has no equivalent yet,
                                     # and creating one needs that decision.
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
| `KOPIUR_PUID` / `KOPIUR_PGID` | `1000` | mirrors `VOLSYNC_PUID`/`PGID` |
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
* Upstream docs: <https://kopiur.home-operations.com>
