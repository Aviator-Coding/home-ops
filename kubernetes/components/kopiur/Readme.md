# kopiur Backup Configuration

Reusable Flux component that backs up one PVC to the cluster's two kopiur
repositories. It is the deliberate sibling of [`../volsync`](../volsync/Readme.md)
and runs **alongside** it on 22 of 30 claims; on the eight Stage 5 retired
volumes it has replaced it outright.

Operator, repositories and credentials are **not** here - they are Stage 0, in
[`kubernetes/apps/base/system/kopiur/`](../../apps/base/system/kopiur/README.md).
This component only declares what to back up.

> **Migration status: Stage 5 IN PROGRESS - 8 of 30 volumes retired, in two
> waves (2026-09-01 pilot, 2026-09-02 wave two).**
> kopiur is live on **all 30 of the fleet's 30** VolSync-protected claims - zero
> deferred (Stage 3 onboarded namespace by namespace on 2026-08-30; Stage 4
> added both remaining claims on 2026-08-31).
>
> **VolSync has now been RETIRED from eight of them**, in two waves, so those
> eight have kopiur as their ONLY backup engine:
>
> * wave one, 2026-09-01 - `ai/repo-wiki`, `downloads/recyclarr-config`,
>   `downloads/sabnzbd-config`, `media/seerr`: chosen for regenerable or
>   reconstructible content and clean restore proofs.
> * wave two, 2026-09-02 - `downloads/prowlarr-config`, `selfhosted/ntfy`,
>   `downloads/autobrr`, `selfhosted/obsidian-livesync`: **not all regenerable.**
>   `ntfy` holds real auth state and `obsidian-livesync` is a genuine Obsidian
>   vault, retired on an explicit captain decision after an objection. What
>   authorises them is completeness of proof (100% of claim content,
>   destination-identical in content *and* metadata) plus, for the two 2Gi
>   `selfhosted` claims, a PVC that cannot outgrow its restore cache.
>
> **The other 22 remain dual-engine** with every VolSync source live. Retiring
> any of them is a separate captain decision. `selfhosted/paperless-ngx` is a
> permanent carve-out and stays dual-engine indefinitely;
> `selfhosted/syncthing-data` and `selfhosted/paperless-ngx-media` were assessed
> in wave two and found NOT ready - their proofs cover nothing meaningful and
> both sit behind a restore cache they would cross the first time they hold real
> data.
>
> Authoritative machine-readable record of which claims are single-engine:
> `RETIRED_CLAIMS` in
> [`scripts/ci/kopiur-stage3-test.py`](../../../scripts/ci/kopiur-stage3-test.py),
> asserted exactly in both directions. Selection rationale, mechanics and the
> post-retirement re-proof:
> [`docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md`](../../../docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md)
> (wave one) and
> [`docs/backups/kopiur-wave-two-retirement-2026-09-02.md`](../../../docs/backups/kopiur-wave-two-retirement-2026-09-02.md)
> (wave two).
>
> **Retiring a volume swaps which component owns its PVC - it does not delete
> the claim, and getting that wrong would.** See "Retiring a volume" below
> before touching any of this.
>
> Stage 4 onboardings:
> * `selfhosted/changedetection-config` did **not** need a root mover. It had
>   **no `securityContext` at all**, so it ran as its image default (root) and
>   wrote 2292 mode-`0600` root-owned files, while its Flux Kustomization
>   declared an `APP_UID`/`APP_GID` of 2000:2000 that no manifest in this repo
>   consumes. Giving the app the 1000:1000 identity its data already carried
>   and re-owning the volume removed the need for a `0:1000` mover - onboarded
>   with no `KOPIUR_PUID`/`PGID` override and no privileged-mover grant on
>   `selfhosted`.
> * `home-automation/matter-server` stays root by design: explicit
>   `KOPIUR_PUID/PGID: 0` plus the namespace-wide
>   `kopiur.home-operations.com/privileged-movers=true` annotation on the
>   overlay that actually produces the Namespace (`not-used` patch target).
>
> **All 30 claims are restore-proven on both destinations** (2026-09-01).
> Per-volume evidence table and `CACHEDIR.TAG` adjudication:
> [`docs/backups/kopiur-restore-proof-2026-09-01.md`](../../../docs/backups/kopiur-restore-proof-2026-09-01.md).
> That fleet table is the authority on restore coverage, and it is the satisfied
> Stage 5 **prerequisite**. It records the state *before* any retirement - every
> VolSync source was still live when it was written. Four have since been
> retired (above). Its finding 2 (r2 needs more kopia cache than ceph) **was
> closed for `ai/hermes` on 2026-09-02** - authority on cache sizing is now
> [`docs/backups/kopiur-r2-restore-cache-gate-2026-09-02.md`](../../../docs/backups/kopiur-r2-restore-cache-gate-2026-09-02.md)
> (see "Sizing the mover cache" below); `media/plex` 10Gi is predicted safe but
> not itself r2-exercised. `media/tdarr` and `downloads/radarr` were raised 2Gi -> 10Gi on
> 2026-09-02 and `media/tdarr` is r2-proven at that value
> (`docs/backups/kopiur-populator-drift-2026-09-02.md`), which also records that a raised
> capacity does not reach the standing `Restore` without a one-time delete.
>
> The earlier per-volume drills remain the procedural precedent it is built on,
> and are still accurate:
> * Stage 2 (2026-08-30) restore gate **passed** - `sabnzbd-config` from **both** ceph and r2,
>   byte-identical (2062 files, 2.06 GiB, modes and ownership): durable
>   procedure in
>   [`docs/backups/kopiur-restore-drill-2026-08-30.md`](../../../docs/backups/kopiur-restore-drill-2026-08-30.md).
> * Stage 4 (2026-08-31) - `changedetection-config` (kopia snapshot `c1127a61`,
>   3058 files, per-file sha256 manifest identical to live, modes reproduced
>   exactly where the VolSync restore of the same volume returns `660`/`664`
>   because it stages writable).
> * Stage 4 (2026-08-31) - `home-automation/matter-server` from ceph only. A root
>   restore preserves modes and content but rewrites mixed live uids to `0:0`
>   (see "Root movers" below) - functional for this root app, not a
>   content-fidelity failure.
>
> Those three are spot checks superseded in *scope* by the 2026-09-01 fleet
> table, which covers all 30 claims on both destinations; do not read them as
> the current limit of restore coverage.
> `KOPIUR_PUID`/`KOPIUR_PGID` must match the workload that owns the claim's
> files, or the backup fails outright on any file lacking a world-read bit
> (kopiur fails closed; its admission webhook warns at apply time) - a
> prerequisite for every onboarding, not a sabnzbd quirk (drill finding 2).
> The Stage 1 pilot `downloads/autobrr` held **zero** files at Stage 1 time
> (all state is in `postgres-17`), so it is a valid mechanism test but never
> could prove byte-level fidelity; a `Succeeded` snapshot with `.status.stats
> sizeBytes 0` is not evidence backup works. Fleet continuous signal:
> `KopiurBackupEmpty` in
> [`apps/base/system/kopiur/app/prometheusrule.yaml`](../../apps/base/system/kopiur/app/prometheusrule.yaml)
> (`kopiur_policy_last_backup_files == 0`). Plan of record: firstmate's
> `homeops-kopiur-vs-volsync-scout` report, section 6.


## Directory Structure

```
components/kopiur/
├── kustomization.yaml   # Component -> ./backup  (what an app includes)
├── backup/              # Kustomization -> ../ceph ../r2  (Flux `path` for multi-claim apps)
├── ceph/                # local, in-cluster RGW: policy + schedule + Restore
├── r2/                  # offsite, Cloudflare: policy + schedule
└── pvc/                 # Component, added ONLY by a volume that has retired volsync
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
| Creates the app's PVC | yes (`pvc.yaml`) | **only after retirement**, via the separate `./pvc` Component. While both engines run, VolSync owns the claim and two components emitting the same PVC from `${APP}` would be a kustomize collision - see "Retiring a volume" |
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

* **Observable** - but read the right signal. `kopiur_projected_secrets_live` is
  a **population gauge sampled on the operator's periodic sweep, not a per-run
  indicator**. Measured here 2026-08-31: two projected Secrets existed for ~90s
  during a real backup and the gauge read `0` at every 30s scrape across that
  window. **A flat 0 during a run is correct and is not evidence that projection
  is not happening.** Upstream's HELP text still prescribes alerting on
  `deriv() > 0` over a day; we deliberately do **not** follow that. A one-shot
  permanent leak that steps 0 to N and stays flat leaves `deriv` approximately 0
  once older zeros age out of the window, so a deriv rule goes quiet exactly when
  credentials sit permanently at rest - worse than no alert, because it reads as
  proof of safety. `KopiurProjectedCredentialsLeaking` in
  [`apps/base/system/kopiur/app/prometheusrule.yaml`](../../apps/base/system/kopiur/app/prometheusrule.yaml)
  is the single authoritative source for the alert expression and its sampling
  rationale: it watches the gauge level (not `deriv`) so a permanent left-behind
  copy keeps firing for its whole life, while a single frozen census of a healthy
  short-lived run does not. None of the chart's 12 shipped alerts watch the
  series, so without this rule the decision's "observable" mitigation would be a
  claim rather than a fact.

  For **per-run** evidence use the operator log line
  `reaped projected credentials copy secret=<snapshot>-creds-N` and the
  `kopiur_secrets_projected_total` counter. Do **not** use
  `Snapshot.status.cleanup.credsReapedAt` as proof projection happened: that
  field is populated on every run, projection on or off (it is set on Snapshots
  taken before this change).
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
already exists in the cluster. A missing `credentialProjection` is fixed by
**deleting and recreating** that Restore (safe: it has no finalizers and no
ownerReferences, and owns no backup data, unlike a Snapshot) - not by repointing
anything. `sabnzbd-kopiur-dst` was recreated during the Stage 5 pilot;
`downloads/autobrr` is the one still missing the field and must be recreated
before it is ever retired. Bound claims never repoint: `spec.dataSourceRef` is
immutable, so a live claim keeps its (now often inert) VolSync `${APP}-dst` ref
forever and the standing Restore stays Pending until a **rebuilt** claim from
`components/kopiur/pvc` claims it. A scratch drill `Restore` written today needs
`credentialProjection.enabled: true` in its own spec - see "Restore" below.

## `SecurityContextCompatible` is positive-only AND narrower than it looks

The operator emits this condition on a `Snapshot` only when the mover's uid
matches **every container of every pod that mounts the claim - initContainers
included**, not merely the container that actually writes it. It is
positive-only: there is no `False` variant, so **absence is not a pass and is
also not a failure**. Read it as "the operator could prove compatibility",
never as "the operator found a problem".

Measured across the live fleet 2026-08-31, absence has four distinct causes and
only one of them is a real defect:

| Claim | Why absent | Real problem? |
|---|---|---|
| `downloads/recyclarr-config` | no pod exists at snapshot time (CronJob, ~18 s/day) | no - readability independently proven 2026-08-31 via a snapshot-and-restore probe against a scratch copy (2913/2913 files, 607/607 dirs readable by mover identity 2000:2000, 0 walk errors): [`docs/backups/recyclarr-config-readable-check-2026-08-31.md`](../../../docs/backups/recyclarr-config-readable-check-2026-08-31.md) |
| `database/pgadmin` | `fix-permissions` **initContainer** runs as uid 0 vs mover 5050 | no |
| `selfhosted/changedetection-config` | `browser` sidecar runs as uid 999 vs mover 1000, and mounts nothing | no |
| `media/tdarr-config`, `media/calibre-web-automated` | pod really does run `runAsUser: 0` vs mover 2000 | the known measured mismatch |

`changedetection`'s sidecar **cannot** be moved onto the mover's uid to buy the
condition: browserless/chrome was tested at uid 1000 on 2026-08-31 and Chrome
fails to launch outright (`chrome_crashpad_handler: --database is required`,
"retries exhausted") while the container still reports `Ready` - so forcing it
would silently kill every browser-backed watch and trade a working app for a
cosmetic condition. Do not do it.

The condition is also not reliably emitted even when it should be: `autobrr`'s
04:54 r2 run carried it while its 01:36 ceph run did not, same claim, same pod,
same identity, no operator restart in between. So a run without it is not even
evidence about the pod shape.

**The load-bearing proof is the mover's own read, not this condition.** kopia
fails closed on the first unreadable file, so a `Succeeded` snapshot whose
`.status.stats` covers the whole volume already proves the identity works. The
direct check, which is what Stage 4 used, is to mount the claim **read-only**
in a pod running as the mover's uid and open every file - read-only is the
point, because kubelet applies no `fsGroup` fixup to a read-only mount, which
is exactly what the mover sees:

```bash
kubectl -n <ns> exec <probe> -- sh -c 'find /datastore -type f ! -readable | wc -l'
```

## Per-Application Usage

In the app's Flux Kustomization (`apps/main/<ns>/<app>.yaml`), **alongside**
volsync while both engines run. Retiring volsync from a volume is a separate,
evidence-gated step - see "Retiring a volume" below; do not drop the volsync
entry as part of onboarding.

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

## Retiring a volume (Stage 5)

Removing VolSync from a volume that kopiur already protects. Eight volumes have
been through this, in two waves - `ai/repo-wiki`, `downloads/recyclarr-config`,
`downloads/sabnzbd-config`, `media/seerr` (2026-09-01), then
`downloads/prowlarr-config`, `selfhosted/ntfy`, `downloads/autobrr`,
`selfhosted/obsidian-livesync` (2026-09-02). The full records, including why
those volumes, are
[`docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md`](../../../docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md)
and
[`docs/backups/kopiur-wave-two-retirement-2026-09-02.md`](../../../docs/backups/kopiur-wave-two-retirement-2026-09-02.md).

**One trap the wave-two retirement found, which the next one will hit too.** Four
`selfhosted` HelmReleases still read `existingClaim: ${VOLSYNC_CLAIM:-*app}`
(`linkwarden`, `n8n`, `paperless-ngx`, `syncthing`). That default is a **dangling
YAML alias**, not an anchor reference - kustomize emits the token verbatim, so
the moment the overlay stops defining `VOLSYNC_CLAIM` the render becomes
`existingClaim: *app` and `flate` fails the whole Kustomization with
`unknown anchor 'app' referenced`. Rename it to `${KOPIUR_CLAIM:-<claim>}` with
the default corrected to the literal claim name, as `ntfy`, `obsidian-livesync`
and `ai/repo-wiki` now do.

**A per-volume restore proof comes first.** The fleet proof
(`docs/backups/kopiur-restore-proof-2026-09-01.md`) is that evidence for all 30
claims. Its finding 2 (r2 cache sizing) is **closed for `ai/hermes`** and the
large size class is r2-proven
(`docs/backups/kopiur-r2-restore-cache-gate-2026-09-02.md`); before retiring a
large claim still confirm its standing `KOPIUR_CACHE_CAPACITY` clears
`min(snapshot sizeBytes, ~6.2 GiB)` and recreate any create-time
`*-kopiur-dst` populator left behind by `ssa: IfNotPresent`.

The overlay change is small:

```yaml
  dependsOn:
    # the `volsync` (system) entry goes - nothing here renders a VolSync object
    - name: kopiur-repository
      namespace: system
  components:
    - ../../../../../components/kopiur
    - ../../../../../components/kopiur/pvc     # <- NEW, and not optional
  postBuild:
    substitute:
      APP: *app
      KOPIUR_CAPACITY: 5Gi                     # must match the LIVE claim
      # every VOLSYNC_* key is deleted
```

### The claim is the hazard, and `./pvc` is the whole point

`../volsync/pvc.yaml` is the **only** manifest that emits the app's PVC, and app
overlays run `prune: true`. Dropping the volsync Component therefore takes the
claim out of the Flux inventory and Flux garbage-collects it - **deleting the
app's data volume** for a change meant only to remove a backup engine. `./pvc`
puts the claim back in the inventory, owned by kopiur instead.

It is a separate Component rather than part of this one because retirement is
per-volume: a claim moves engines one app at a time, and folding the PVC into
`../kustomization.yaml` would make it collide with `../volsync/pvc.yaml` for
every app still running both. When the last volume retires, it collapses in.

### `dataSourceRef` is immutable, hence `ssa: IfNotPresent`

Repointing an existing claim's `spec.dataSourceRef` from `${APP}-dst` to
`${APP}-kopiur-dst` is **rejected** - `spec is immutable after creation except
resources.requests and volumeAttributesClassName for bound claims`. Omitting the
field is the same forbidden change, because Flux owns it and SSA would remove
it. `ssa: IfNotPresent` keeps the claim inventoried (never pruned) while
skipping the apply, so an existing claim keeps its now-inert VolSync
`dataSourceRef` and a **rebuilt** one is created from this file and seeded from
kopiur. Never add `kustomize.toolkit.fluxcd.io/force: enabled` to "fix" that
drift - it resolves an immutable-field conflict by deleting and recreating the
object, which here is the volume. Full mechanics and the measured API error are
in [`pvc/pvc.yaml`](pvc/pvc.yaml).

### Check the standing `Restore` before you rely on it

`ceph/restore.yaml` carries `ssa: IfNotPresent`, so Flux created each
`${APP}-kopiur-dst` once and never reconciles it again. Objects created before
`credentialProjection` was added still lack it, and a `Restore` without it fails
at mover time while every CR reads clean. `downloads/sabnzbd` was recreated for
exactly this on retirement; **`downloads/autobrr` is still in that state** and
must be recreated before it is ever retired. Deleting a `Restore` is safe - it
has no finalizers and owns no data, unlike a `Snapshot` - but verify that on the
object first.

### What gets removed, and what cleans itself up

3 `ReplicationSource` + 1 `ReplicationDestination` + 3 `ExternalSecret` are
Flux-managed and go with the Component. Their 3 cache PVCs (2Gi each) and 3
Secrets are **cascades** - the `ReplicationSource` is its cache PVC's
`controller: true` owner, and ESO's `creationPolicy: Owner` owns the target
Secret - so no manual cleanup is needed. Nothing touches the restic repository:
deleting a `ReplicationSource` never does, which is the load-bearing asymmetry
against kopiur's `Snapshot`. The old restic repositories stay readable as a
fallback.

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
| `KOPIUR_CACHE_CAPACITY` | `2Gi` | a generic ephemeral **PVC** (not an emptyDir) on the default StorageClass, discarded with the mover pod. Feeds the backup movers *and* the standing `Restore`; **size it for the restore**, which is the demanding direction - see "Sizing the mover cache" below |
| `KOPIUR_PUID` / `KOPIUR_PGID` | `1000` | must match the workload uid/gid or backup fails closed on non-world-readable files (drill finding 2); mirrors `VOLSYNC_PUID`/`PGID` defaults only. Root (`0`) also needs the privileged-mover annotation below |
| `KOPIUR_SCHEDULE_CEPH` | `H 1-23/4 * * *` | |
| `KOPIUR_SCHEDULE_R2` | `H 4 * * *` | |
| `KOPIUR_SCHEDULE_TIMEZONE` | `America/New_York` | IANA zone the cron above is evaluated in - see "Timezone: kopiur vs VolSync" below |
| `KOPIUR_CAPACITY` | `5Gi` | **`./pvc` Component only** - size of the claim. Must match the LIVE claim: under `ssa: IfNotPresent` it is create-time-only and therefore unexercised until someone rebuilds the claim, which is what makes a wrong value dangerous rather than harmless |
| `KOPIUR_ACCESSMODES` | `ReadWriteOnce` | `./pvc` Component only |
| `KOPIUR_STORAGECLASS` | `ceph-block` | `./pvc` Component only |

## Sizing the mover cache

`KOPIUR_CACHE_CAPACITY` has to be sized against a **restore**, not a backup. A
backup streams a read-only staged clone and stays small; a restore fills the
cache with everything it pulls out of the repository, and **if it runs out the
`Restore` fails terminally and never retries** - discovered, if you get it wrong,
during an actual disaster.

Measured on `ai/hermes` from `r2` on 2026-09-02
(`docs/backups/kopiur-r2-restore-cache-gate-2026-09-02.md`): the cache grows
**~1:1 with the bytes written into the restore target** until it reaches kopia's
own internal budget - observed as a **~6.2 GiB plateau** - and only then holds
flat while the restore runs on to completion.

```
restored:  0.4   1.9   2.5   3.1   4.2   5.4   6.4   7.2   8.6   9.7  GiB
cache:     0.4   1.7   2.2   2.9   4.0   5.2   6.2   6.1   6.2   6.0  GiB
                                              ^ plateau: eviction engages
```

So the working rule is:

> **required cache ≈ min(snapshot `sizeBytes`, ~6.2 GiB), plus headroom.**

Two consequences worth internalising:

- **It is a cliff, not a slope.** While a claim's snapshot is smaller than its
  cache, the cache simply never reaches the limit and any value works. The first
  time a growing claim's snapshot crosses its cache capacity, the requirement
  jumps straight to the full plateau - so a claim sitting just under its capacity
  is not "nearly fine", it is one growth spurt from a terminal restore failure.
- **Do not lean on the plateau.** kopiur sends `"cache":{}` in its work spec, so
  it passes kopia neither `--content-cache-size-mb` nor `--metadata-cache-size-mb`
  and kopia's built-in defaults govern eviction. That budget is a *soft* limit
  this repo has never configured and does not pin, so anything large should be
  sized to survive the no-eviction case too (i.e. cover the whole snapshot).

The CRD does expose `mover.cache.contentCacheSizeMb` / `metadataCacheSizeMb`,
which would let a small capacity be made safe by bounding kopia explicitly
instead of by growing the PVC. Nothing in this repo sets them today; that is the
structural fix if these capacities ever become expensive.

Cost is not a reason to under-size: `mode: Ephemeral` renders a generic ephemeral
`volumeClaimTemplate` on `ceph-block`, which is thin-provisioned and deleted with
the mover pod, so a raised figure only ever consumes what a run actually writes.

**Raising the substitute does not update a live standing `Restore`.**
`${APP}-kopiur-dst` carries `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`, so
Flux applies the new capacity to both `SnapshotPolicy` objects but leaves an
already-created populator at its create-time value. After raising
`KOPIUR_CACHE_CAPACITY` on an onboarded claim, **delete and recreate** that
`Restore` before relying on the CEPH populator path (safe: no finalizers, no
ownerReferences, owns no backup data - Stage 5 did this for `sabnzbd-kopiur-dst`).
Hand-written drill Restores always set capacity in their own spec and are
unaffected. Full evidence:
`docs/backups/kopiur-r2-restore-cache-gate-2026-09-02.md`.

**This applies to every templated field, not just the capacity - including fields
added to the template *after* an object was created.** The 2026-09-02 fleet audit
(`docs/backups/kopiur-populator-drift-2026-09-02.md`) compared all 30 populators
against their resolved Git values and found two frozen: `ai/hermes` at a stale
`5Gi`, and `downloads/autobrr` - the oldest object, from the Stage 1 pilot -
carrying identity `1000:1000` where Git says `2000:2000` **and no
`credentialProjection` at all**, either of which fails its restore closed. Both
were recreated and all 30 agree with `main`. The same day's tdarr/radarr 2Gi->10Gi
raises then needed post-merge delete+reconcile; that live recreate is **closed**
(both verified at 10Gi):
`docs/backups/kopiur-wave-two-reproof-2026-09-02.md` Part 0. Re-read the live object
after changing any `KOPIUR_*` value; a green Kustomization proves nothing here.

## Root movers (`KOPIUR_PUID`/`PGID: 0`)

The component already substitutes `mover.podSecurityContext.runAsUser` /
`runAsGroup` / `fsGroup` from `KOPIUR_PUID`/`KOPIUR_PGID`, so a root mover needs
**no component change** - set both substitutions to `"0"` on the app overlay
(example: `kubernetes/apps/main/home-automation/matter-server.yaml`). Prefer
explicit substitutions over `mover.inheritSecurityContextFrom.pvcConsumer`:
inherit is a SnapshotPolicy-only field and does **not** flow to the standing
`Restore`, which uses the same `KOPIUR_*` substitutions.

A root mover still trips kopiur's privileged-mover gate regardless of how the
identity was chosen. Measured 2026-08-31: a SnapshotPolicy with `runAsUser: 0`
is **admitted**, then the Snapshot sits `Pending` with
`MoverPermitted=False` / `PrivilegedMoverNotPermitted` until the namespace
carries `kopiur.home-operations.com/privileged-movers=true`.

Apply that annotation through GitOps on the overlay that actually produces the
Namespace - typically a strategic patch of `kind: Namespace` / `name: not-used`
(the common component's pre-transform name) on
`kubernetes/apps/main/<ns>/kustomization.yaml`. The base `namespace.yaml` is
often not in any Flux inventory, and `kustomize.toolkit.fluxcd.io/prune:
disabled` only prevents DELETE - it does not freeze annotations. The annotation
is **namespace-wide**: it permits a root mover for every claim in that
namespace. It is a gate, not an identity change - sibling claims keep their
existing non-root `KOPIUR_PUID`/`PGID` values (home-automation: esphome 2000,
home-assistant 1000, zigbee2mqtt 2000).

A root **restore** mover preserves modes and file content but materialises
mixed live uids as `0:0` (matter-server: 151 files were `1000:0` live and
`0:0` restored; the five `0600` fabric files stayed `0:0`). That is expected
for a root-owned workload and is not a content-fidelity failure.

## `wait: true` is incompatible with this component

`ceph/restore.yaml` is a standing `Restore` in passive populator mode, and it
reports **`Ready=False` by design** (`AwaitingPvcDataSourceRef`). Stage 5 does
**not** repoint an already-bound claim at it: `spec.dataSourceRef` is immutable
on a bound PVC, so a live claim keeps its now-inert VolSync ref forever and the
standing Restore stays Pending until a **rebuilt** claim is created fresh from
`components/kopiur/pvc` (see "Retiring a volume" above and `pvc/pvc.yaml` for the
measured API error). A passive populator therefore never becomes Ready on its
own for an existing claim, and it still cannot sit in a Kustomization that
assesses its whole inventory.

Flux with `wait: true` assesses **every** object in a Kustomization's inventory,
so adding this component inline to such a Kustomization makes Flux block on an
object that can never become Ready, and the Kustomization times out.

Two apps hit this in Stage 3 and ship the kopiur half as their own `wait: false`
Kustomization with `path: ./kubernetes/components/kopiur/backup`:
`database/pgadmin` (in `kubernetes/apps/main/database/cloudnative-pg.yaml`) and
`media/calibre-web-automated`. An overlay with `wait: false` plus explicit
workload `healthChecks` is unaffected, because Flux then assesses only the
objects that are listed. **`flate` does not catch this** - it validates that the
Kustomization builds, not Flux's runtime health assessment.

## Timezone: kopiur vs VolSync

**Both engines run behind the cluster-wide `k8tz` mutating webhook
(`system-controller/k8tz`, `failurePolicy: Fail`), which injects
`TZ=America/New_York` plus `/etc/localtime`/`/usr/share/zoneinfo` into
essentially every pod - but they do not agree on what to do with it.**
VolSync's Go scheduler honours the process `TZ`, so a VolSync cron hour is a
**America/New_York local hour**. kopiur's operator resolves its own timezone
and defaults to UTC **regardless of the injected `TZ`** - it silently ignores
`k8tz` unless `spec.schedule.timezone` (IANA name) is set on the
`SnapshotSchedule`, which is why both files in `ceph/` and `r2/` now pin it via
`KOPIUR_SCHEDULE_TIMEZONE` (default `America/New_York`, matching VolSync).

This is not cosmetic. The ceph and r2 hour offsets documented below exist
specifically so the two engines never fire on the same claim in the same UTC
hour, and that stagger is arithmetic, not just "different numbers":

- Measured live 2026-08-31 (EDT, UTC-4): `database/pgadmin-r2` kopiur cron `H 7
  * * *` reports `status.nextSchedule.timezone: UTC` and fires at
  `07:42:18Z`; VolSync's sibling `pgadmin-r2` (cron `0 1 * * *`, same injected
  `TZ`) fires at `05:00:00Z` - honouring `America/New_York`. All 31 single-hour
  VolSync `ReplicationSource`s in the fleet fire exactly 4 hours later in UTC
  than their written hour (the EDT offset); every kopiur `SnapshotSchedule`
  reports `status.nextSchedule.timezone: UTC` regardless of season.
- Before this change, the 4-hourly ceph schedules relied on kopiur's literal
  UTC hours `1,5,9,13,17,21` happening to sit one hour after VolSync's
  DST-shifted `0,4,8,12,16,20` (EDT). Because `4 mod 4 == 0`, that held by
  coincidence all summer. At the 2026-11-01 DST transition (EST, UTC-5),
  VolSync's UTC hours shift to `1,5,9,13,17,21` - landing on **every one** of
  kopiur's hours, on **all 29 claims that were dual-engine when this was
  measured on 2026-08-31 (22 today, after Stage 5 retired eight across two
  waves)** on the 4-hourly cadence. Verified with a live collision check across
  every namespace: 0 collisions pre-fix in summer, 29 claims colliding
  pre-fix in winter (same 2026-08-31 measurement), 0
  collisions in **either** season once `spec.schedule.timezone` is set to the
  same zone VolSync already runs in (the fix keeps the hour *values* the same
  and lets them shift together with DST, instead of only one engine
  shifting).
- Aligning kopiur to VolSync's zone (rather than pinning both engines to UTC)
  was the chosen fix: it is the smaller, safer change, and it matches the
  cluster's existing `k8tz` convention. Pinning to UTC would mean changing
  VolSync's actual run times and touching the cluster-wide,
  `failurePolicy: Fail` `k8tz` webhook - a much larger blast radius for the
  same outcome.

**When editing any `KOPIUR_SCHEDULE_*` or `VOLSYNC_SCHEDULE_*` value, re-check
the hour tables below in both DST seasons** - the two engines are only
guaranteed to line up because they now evaluate cron in the same zone, not
because the written hour numbers alone keep them apart.

## Schedules

Cadence matches VolSync per destination; the **hour** is offset so the two
systems cannot collide on the same claim. Both engines now evaluate these
hours in `America/New_York` (see above), so the table below is written in
local time and shifts together with DST - the UTC instant moves, but the
1-hour kopiur/VolSync stagger does not:

| Destination | VolSync (autobrr) | kopiur | Retention |
|---|---|---|---|
| ceph | `45 */4 * * *` -> local 00,04,08,12,16,20 at :45 | `H 1-23/4 * * *` -> local **01,05,09,13,17,21** at a hashed minute | hourly 6, daily 14, weekly 10, monthly 6 |
| minio | `30 */6 * * *` -> local 00,06,12,18 at :30 | *(no kopiur destination)* | - |
| r2 | `45 3 * * *` -> local 03:45 | `H 4 * * *` -> local **04:xx** | daily 30, weekly 12, monthly 12 |

Resulting UTC hours for the ceph cadence, both seasons (identical across every
namespace - `database/pgadmin` shown; the 22 dual-engine claims match, and the
eight Stage 5 kopiur-only claims keep the same kopiur hours with no VolSync peer):

| Season | VolSync ceph (UTC) | kopiur ceph (UTC) | Collision? |
|---|---|---|---|
| EDT (summer, UTC-4) | 0, 4, 8, 12, 16, 20 | 1, 5, 9, 13, 17, 21 | No |
| EST (winter, UTC-5) | 1, 5, 9, 13, 17, 21 | 2, 6, 10, 14, 18, 22 | No |

Retention is copied field-for-field from each destination's VolSync `retain`
block so the two systems stay directly comparable through the parallel run.

**`KOPIUR_SCHEDULE_R2` must be overridden per namespace - the `H 4 * * *`
default above does not scale past the pilot.** It puts *every* kopiur r2 policy
into a single hour, and 04:00 already carries VolSync's entire ceph slot plus 13
VolSync r2 runs; at full Stage 3 that is 83 mover runs, the busiest hour of the
day. Worse, for every `selfhosted` claim and for `media/calibre-web-automated`
VolSync's own r2 job is *also* in the 04:00 hour, so the default stacks both
engines onto the same claim - exactly what the hour offset exists to prevent.
Stage 3 assigns one free hour per namespace (free of every VolSync destination
and of kopiur's own ceph slots):

| namespace | `KOPIUR_SCHEDULE_R2` |
|---|---|
| `database` | `H 7 * * *` |
| `home-automation` | `H 10 * * *` |
| `downloads` | `H 11 * * *` |
| `selfhosted` | `H 14 * * *` |
| `media` | `H 15 * * *` |
| `ai` | `H 19 * * *` |

These hours are now `America/New_York` local (see "Timezone: kopiur vs
VolSync" above), so their UTC instant moves with DST - verified to stay clear
of every VolSync destination in the same namespace in both seasons:

| namespace | local hour | UTC (EDT, summer) | UTC (EST, winter) |
|---|---|---|---|
| `database` | 07 | 11 | 12 |
| `home-automation` | 10 | 14 | 15 |
| `downloads` | 11 | 15 | 16 |
| `selfhosted` | 14 | 18 | 19 |
| `media` | 15 | 19 | 20 |
| `ai` | 19 | 23 | 00 |

`KOPIUR_SCHEDULE_CEPH` stays at the component default: it is already
structurally disjoint from VolSync's even 4-hour slots and needs no per-app
override.

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

It is **passive until a rebuilt claim claims it**: `target.populator` means
"wait to be claimed by a PVC's `spec.dataSourceRef`". Bound claims never
repoint - `spec.dataSourceRef` is immutable - so a live claim keeps its VolSync
`${APP}-dst` ref (inert once VolSync is retired) and the standing Restore stays
`Pending` / `AwaitingPvcDataSourceRef` indefinitely. It is claimed only by a
**rebuilt** claim created fresh from `components/kopiur/pvc`; on the eight Stage 5
retired volumes that is already the contract today.

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

On a **dual-engine** volume either of these leaves VolSync completely untouched
and loses nothing, because VolSync is still backing that claim up. On a **Stage 5
kopiur-only** volume (the eight in `RETIRED_CLAIMS`) there is no VolSync peer left
- suspending or removing this component is a real protection gap until VolSync is
restored or another engine is added. Do not treat the dual-engine rollback as
safe on a retired claim:

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

# Did credential projection actually happen for a run? (per-run evidence)
kubectl -n system logs -l app.kubernetes.io/name=kopiur -c controller --since=1h \
  | grep 'projected credentials'

# Projected copies live RIGHT NOW. Expect none between runs; during a run you
# see one `<snapshot>-creds-N` per repository Secret (ceph has two - auth and
# encryption; r2 has one serving both).
kubectl -n downloads get secret | grep -- -creds-
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
* Fleet restore-proof authority (all 30 claims, both destinations, 2026-09-01): [`docs/backups/kopiur-restore-proof-2026-09-01.md`](../../../docs/backups/kopiur-restore-proof-2026-09-01.md)
* Stage 5 pilot retirement (four kopiur-only volumes, 2026-09-01): [`docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md`](../../../docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md)
* Stage 5 wave two retirement (four more, 2026-09-02 - eight kopiur-only in total): [`docs/backups/kopiur-wave-two-retirement-2026-09-02.md`](../../../docs/backups/kopiur-wave-two-retirement-2026-09-02.md)
* Stage 2 restore gate (both destinations, both findings; durable procedure): [`docs/backups/kopiur-restore-drill-2026-08-30.md`](../../../docs/backups/kopiur-restore-drill-2026-08-30.md)
* `downloads/recyclarr-config` readability probe (CronJob claim; not restore-fidelity - restore proof is the fleet table above): [`docs/backups/recyclarr-config-readable-check-2026-08-31.md`](../../../docs/backups/recyclarr-config-readable-check-2026-08-31.md)
* Stage 4 root-mover onboarding (`matter-server`): this Readme's "Root movers" section + `scripts/ci/kopiur-stage4-test.py`
* Upstream docs: <https://kopiur.home-operations.com>
