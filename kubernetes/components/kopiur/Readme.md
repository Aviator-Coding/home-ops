# kopiur Backup Configuration

Reusable Flux component that backs up one PVC to the cluster's two kopiur
repositories. It is the deliberate sibling of [`../volsync`](../volsync/Readme.md)
and, for now, runs **alongside** it rather than replacing it.

Operator, repositories and credentials are **not** here - they are Stage 0, in
[`kubernetes/apps/base/system/kopiur/`](../../apps/base/system/kopiur/README.md).
This component only declares what to back up.

> **Migration status: Stage 4 complete.** kopiur is live on **30 of the fleet's
> 31** VolSync-protected claims - 29 onboarded namespace by namespace in Stage 3
> (2026-08-30), plus `selfhosted/changedetection-config` in Stage 4
> (2026-08-31). **Both engines run on every one of those volumes** - every
> VolSync source is still live, nothing has been retired, and retirement is
> Stage 5, which needs a per-volume restore proof first. Exactly **one claim is
> deliberately NOT on kopiur** and must stay off until the component can express
> a root mover: `home-automation/matter-server` (runs as root by design). It
> remains fully VolSync-protected.
>
> Stage 4 is worth reading before assuming any other deferral needs a root
> mover: `changedetection` did not. It had **no `securityContext` at all**, so
> it ran as its image default (root) and wrote 2292 mode-`0600` root-owned
> files, while its Flux Kustomization declared an `APP_UID`/`APP_GID` of
> 2000:2000 that no manifest in this repo consumes. Giving the app the 1000:1000
> identity its data already carried (every file's group, every setgid directory)
> and re-owning the volume to match removed the need for a `0:1000` mover
> entirely - so it onboarded with no `KOPIUR_PUID`/`PGID` override, no component
> change, and no namespace-wide privileged-mover grant. **Being onboarded is not the same as being
> proven** - a per-volume restore has been demonstrated for exactly two claims,
> `sabnzbd-config` (Stage 2) and `changedetection-config` (Stage 4: kopia
> snapshot `c1127a61`, 3058 files / 36,993,597 B restored into a scratch PVC,
> per-file sha256 manifest identical to live, and modes reproduced exactly -
> 2292x`600`, 565x`644`, 197x`660`, 4x`664` - where the VolSync restore of the
> same volume returns `660`/`664` because it stages writable). Stage 2's restore gate **passed** on 2026-08-30:
> [`docs/backups/kopiur-restore-drill-2026-08-30.md`](../../../docs/backups/kopiur-restore-drill-2026-08-30.md)
> - sabnzbd-config restored byte-identically from both ceph and r2 (2062 files,
> 2.06 GiB, per-file sha256, modes and ownership included). Do not read the 29
> onboardings as fleet-wide backup verification. `KOPIUR_PUID`/`KOPIUR_PGID`
> must match the workload that owns the claim's files, or the backup fails
> outright on any file lacking a world-read bit (kopiur fails closed; its
> admission webhook warns at apply time) - a prerequisite for every onboarding,
> not a sabnzbd quirk (drill finding 2). The Stage 1 pilot `downloads/autobrr`
> held **zero** files at Stage 1 time (all state is in `postgres-17`), so it is
> a valid mechanism test but never could prove byte-level fidelity; a
> `Succeeded` snapshot with `.status.stats sizeBytes 0` is not evidence backup
> works. Plan of record: firstmate's `homeops-kopiur-vs-volsync-scout` report,
> section 6.

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
  therefore alerts on the **level** (`kopiur_projected_secrets_live > 0` for 1h):
  healthy runs reaped in ~90s stay silent, a permanent left-behind copy keeps
  firing for its whole life, and any accumulation deriv would have caught also
  holds the gauge above zero continuously. None of the chart's 12 shipped alerts
  watch the series, so without this rule the decision's "observable" mitigation
  would be a claim rather than a fact.

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
already exists in the cluster. The two live `*-kopiur-dst` objects in `downloads`
predate this change; they are inert (`target.populator`, and every claim's
`dataSourceRef` still points at VolSync's `${APP}-dst`), so nothing breaks today,
but they must be recreated or hand-patched before Stage 5 repoints anything at
them. A scratch drill `Restore` written today needs `credentialProjection.enabled:
true` in its own spec - see "Restore" below.

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
| `downloads/recyclarr-config` | no pod exists at snapshot time (CronJob, ~18 s/day) | no |
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
| `KOPIUR_SCHEDULE_TIMEZONE` | `America/New_York` | IANA zone the cron above is evaluated in - see "Timezone: kopiur vs VolSync" below |

## `wait: true` is incompatible with this component

`ceph/restore.yaml` is a standing `Restore` in passive populator mode, and it
reports **`Ready=False` for the whole parallel run by design**
(`AwaitingPvcDataSourceRef` - it is waiting for a PVC `dataSourceRef` that only
Stage 5 will point at it). Flux with `wait: true` assesses **every** object in a
Kustomization's inventory, so adding this component inline to such a
Kustomization makes Flux block on an object that can never become Ready, and the
Kustomization times out.

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
  kopiur's hours, on **all 29 claims running both engines on the 4-hourly
  cadence**. Verified with a live collision check across every namespace: 0
  collisions pre-fix in summer, 29 claims colliding pre-fix in winter, 0
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
namespace - `database/pgadmin` shown, all 29 dual-engine claims match):

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
* Stage 2 restore gate (both destinations, both findings): [`docs/backups/kopiur-restore-drill-2026-08-30.md`](../../../docs/backups/kopiur-restore-drill-2026-08-30.md)
* Upstream docs: <https://kopiur.home-operations.com>
