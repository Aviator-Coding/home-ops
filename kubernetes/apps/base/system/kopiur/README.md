# kopiur - Stage 0

kopiur is the primary PVC backup engine (Stage 5 complete 2026-09-04; VolSync
remains only on three dual-engine carve-outs). This directory is **Stage 0
only** - operator, repositories and credentials. What to back up lives in
[`kubernetes/components/kopiur/`](../../../../components/kopiur/Readme.md).

Stage 0 installs the operator and declares *where* backups could go. It creates
no `SnapshotPolicy` and no `SnapshotSchedule` of its own. **Migration status,
onboarded claim list, schedules, retirement procedure and rollback live on the
component, not here** -
[`kubernetes/components/kopiur/Readme.md`](../../../../components/kopiur/Readme.md)
(Stage 5 COMPLETE: 26 of 29 claims kopiur-only; 3 dual-engine carve-outs). This
directory is still Stage 0 only - operator, repos, credentials - and does not
own the onboarding or retirement overlays.

**All 30 claims are restore-proven on both destinations** (2026-09-01, the
Stage 5 prerequisite - it records the state *before* any retirement):
[`docs/backups/kopiur-restore-proof-2026-09-01.md`](../../../../../docs/backups/kopiur-restore-proof-2026-09-01.md).
Wave records:
[`docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md`](../../../../../docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md),
[`docs/backups/kopiur-wave-two-retirement-2026-09-02.md`](../../../../../docs/backups/kopiur-wave-two-retirement-2026-09-02.md),
[`docs/backups/kopiur-wave-three-retirement-2026-09-04.md`](../../../../../docs/backups/kopiur-wave-three-retirement-2026-09-04.md).
The earlier per-volume drills remain the procedural precedent that table is
built on. Stage 2's restore gate **passed** on 2026-08-30 -
[`docs/backups/kopiur-restore-drill-2026-08-30.md`](../../../../../docs/backups/kopiur-restore-drill-2026-08-30.md)
- sabnzbd-config restored byte-identically from both ceph and r2 (2062 files,
2.06 GiB, per-file sha256, modes and ownership included). Do not treat this
directory's presence alone as fleet-wide backup verification.
`KOPIUR_PUID`/`KOPIUR_PGID` must match the workload that owns the claim's files,
or the backup fails closed on any file lacking a world-read bit - a prerequisite
for every onboarding, not a sabnzbd quirk. Continuous fleet signal for an empty
successful backup is `KopiurBackupEmpty` in `app/prometheusrule.yaml`
(`kopiur_policy_last_backup_files == 0`).

**Stage 0 rollback** (operator/repos only): delete the `kopiur` and
`kopiur-repository` Flux Kustomizations. That removes the control plane; it does
not touch VolSync. The repositories themselves are `create.enabled: true` and
idempotent - re-applying reconnects rather than re-creating. For the pilot's
policies/schedules, use the component Readme's Rollback section instead.

## Layout

| Path | What |
|---|---|
| `app/` | operator: `OCIRepository`, `HelmRelease`, supplement `PrometheusRule` (operator/webhook absent, projected-credential leak, empty-backup; chart ships the rest of `kopiur.rules`), and the substitution `ExternalSecret` |
| `backend/` | the Ceph bucket: `ObjectBucketClaim`, `CephObjectStoreUser`, and the `PushSecret` that records its generated keys in 1Password (included from `app/`) |
| `repository/` | the two `ClusterRepository` objects (`ceph`, `r2`) and their credential `ExternalSecret`s, one 1Password item each |

Two Flux Kustomizations, not one (`kubernetes/apps/main/system/kopiur.yaml`).
`kopiur-repository` `dependsOn` `kopiur` because the admission webhook is
`failurePolicy: Fail`: with the operator down the API server **rejects** these
CRs outright rather than leaving them unreconciled - the same shape as
`ai/litellm` and its operator.

## Prerequisites that are NOT in Git

Both must exist before the repositories can reach `Ready`.

### 1. Buckets

kopiur does **not** create buckets. Each repository needs a bucket named
`kopiur`, new and empty, on its backend:

- **Ceph RGW** - already handled in Git: the `ObjectBucketClaim` in `backend/`
  creates the bucket and Rook generates its S3 credentials. No manual step, and
  nothing to add to 1Password by hand.
- **Cloudflare R2** - created by the captain in the R2 console, name confirmed
  as `kopiur`, and verified empty and read/write-able from in-cluster; see
  "R2" below.

There is deliberately **no MinIO repository**. MinIO changed its licensing terms
and the captain intends to retire it, so Stage 0 does not give kopiur a MinIO
destination. This does not touch MinIO in any way: the existing VolSync MinIO
backups keep running exactly as before, and retiring them is a separate future
job.

### 2. Three 1Password items (one per purpose)

Per-purpose items following the `litellm-consumer-*` convention rather than one
grab-bag - captain decision 2026-08-30, taken now precisely because nothing has
migrated yet, so the layout is free to change at zero risk. Two destinations
plus one record-only item for the OBC-generated Ceph S3 keys.

All three live in the **`Homelab`** vault. Everything writes and reads through
the shared `onepassword` ClusterSecretStore, whose vault priority (Homelab 1,
Automation 2, Services 3) resolves there; Connect cannot see the hyphenated
`Home-Lab` vault at all. Verified 2026-08-30 that the destination items exist
in `Homelab` and in neither other vault, so the priority-ordered read path has
nothing to shadow. `kopiur-ceph-bucket` is written by PushSecret after merge.

| Item | Fields | Who writes it |
|---|---|---|
| `kopiur-ceph` | `KOPIA_PASSWORD` | created 2026-08-30, round-trip verified |
| `kopiur-r2` | `KOPIA_PASSWORD`, `R2_ENDPOINT_URL`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | password/endpoint 2026-08-30 round-trip verified; R2 token captain 2026-08-30, access verified - see "R2" below |
| `kopiur-ceph-bucket` | `ACCESS_KEY_ID`, `SECRET_ACCESS_KEY` | `backend/`'s `PushSecret`, after merge. Never by hand |

Three notes on why the split falls where it does:

- `kopiur-ceph` holds the kopia password **only**. Ceph's S3 credentials are
  generated by the `ObjectBucketClaim` and consumed straight from the OBC's own
  Secret, so duplicating them into the item kopiur reads would add a second
  source of truth for no gain.
- `kopiur-ceph-bucket` is a **record**, not an input: nothing in the cluster
  reads it. It is separate from `kopiur-ceph` because the two have different
  lifetimes - Rook owns and can regenerate those S3 keys, whereas a lost kopia
  password is unrecoverable. Still true since credential projection: a mover in
  another namespace needs those same S3 keys, and the operator projects them
  from the OBC's generated `system/kopiur` Secret - the authoritative source -
  for the length of the run rather than reading this record back. See
  `kubernetes/components/kopiur/Readme.md` "Credentials".
- Nothing kopiur owns reads `volsync-template` any more. That item is still live
  for every fleet VolSync `ReplicationSource` (count and destinations:
  `kubernetes/components/volsync/Readme.md`) and is **not** ours to touch,
  restructure or migrate; its cleanup belongs to VolSync's retirement, not to
  Stage 0. The R2 endpoint was copied into `kopiur-r2` once, and the reused
  `R2_HOME_OPS_*` credentials were dropped entirely.

Nothing in the cluster reads the old flat `kopiur` item (which held the three
originally generated kopia passwords, including the MinIO one). It is
**superseded and safe to delete**, but deleting credentials is the captain's
call and is deliberately left undone here. The MinIO password was **not**
carried into the new layout: MinIO is out of Stage 0, so it has no home in it.

`Services/cloudflare-r2` is separate pre-existing cruft: it duplicates VolSync's
R2 token under `R2_K8S_*` field names (verified by hash 2026-08-30). Noted as
future cleanup, deliberately not acted on.

### R2 - verified

Bucket `kopiur` on the account host in `kopiur-r2` / `R2_ENDPOINT_URL`, reached
with the token the captain added to `kopiur-r2` on 2026-08-30.

Measured in-cluster that day, credentials never leaving the cluster, against a
credential proven fresh (a throwaway `ExternalSecret` created seconds earlier
fetched it from Connect by explicit property; its key hashes differ from
VolSync's token, so it is demonstrably a new credential and not a cached one):

| Operation | Result |
|---|---|
| `HeadBucket` | `BucketRegion: ENAM` |
| `ListObjectsV2` before writing | **empty** - no `Contents`, so nothing to collide with |
| `PutObject` | succeeded |
| `GetObject` | succeeded, byte-identical round trip |
| `DeleteObject` | succeeded; the prefix afterwards lists zero keys |

**The token is scoped to `kopiur` alone**, verified 2026-08-30 after the
captain narrowed it: `volsync` is refused (`HeadBucket` 403, `ListObjectsV2`
`AccessDenied`) and `ListBuckets` is denied, while the operations above keep
passing. So this credential reaches exactly one bucket, with full read and
write on it, and cannot touch VolSync's restic data at all. VolSync's own R2
credential is a different key pair and was re-checked against its bucket after
the narrowing - unaffected.

#### Why the VolSync token was not reused (history)

Stage 0 first tried reusing VolSync's `R2_HOME_OPS_*` credentials. Measured
in-cluster on 2026-08-30, credentials never leaving the cluster, from one pod
against one endpoint over one signing path:

| Operation | `volsync` (existing) | `kopiur` (new) |
|---|---|---|
| `HeadBucket` | `BucketRegion: ENAM` | `403 Forbidden` |
| `ListObjectsV2` | returns real keys | `AccessDenied` |
| `PutObject` / `GetObject` / `DeleteObject` | not attempted (never write to the restic bucket) | `AccessDenied` |

The `volsync` column is the control, so the denials are the token's scope and
not a broken test. `ListBuckets` is denied too, and a deliberately nonexistent
bucket returns `403` rather than `404` - which is how R2 answers a
**bucket-scoped** token, whereas an account-scoped one would have said
`NoSuchBucket`.

Re-probed at 05:21Z and 05:27Z the same day against a credential proven current
(a throwaway `ExternalSecret` fetched it from Connect at probe time; Connect
reported `volsync-template` at version 17889, `updatedAt 04:48:19Z`): same
result. A field-name sweep of all three Connect-visible vaults found no second
R2 credential under any name. Existing backups were unaffected throughout - all
105 `ReplicationSource`s present, the 35 `*-r2` ones with no failing conditions
and syncing normally.

A per-bucket token is the better end state anyway, so the reuse was abandoned
rather than unblocked.

> **Lose a `KOPIA_PASSWORD` and that repository is permanently unrecoverable.**
> kopia cannot decrypt without it and there is no recovery path.
>
> The two live passwords (`kopiur-ceph`, `kopiur-r2`) were generated in-cluster
> (`openssl rand -base64 48`, distinct, never printed or committed), pushed to
> 1Password, and verified by reading them back out and comparing hashes -
> including once more after every temporary object was deleted. A third was
> generated when Stage 0 still named a MinIO destination; it remains only in
> the superseded flat `kopiur` item and was not carried forward. **1Password
> now holds the only copy of the live passwords.** Recommended, and deliberately
> left to the captain: take an independent offline record, since a vault outage
> or mistake would otherwise be unrecoverable.

The R2 endpoint stays in 1Password rather than in this repo because home-ops is
**public** and the R2 account id is deliberately not committed anywhere in it -
VolSync keeps its whole endpoint in 1Password for the same reason.
`backend.s3.endpoint` is a plain CRD string with no Secret ref, so the value
cannot be read from a Secret and reaches the CR through Flux `postBuild`
substitution instead (`app/externalsecret.yaml` reads `kopiur-r2` /
`R2_ENDPOINT_URL` and trims it to a bare host -> the `kopiur-substitutions`
Secret -> `substituteFrom` on the `kopiur-repository` Kustomization).

If that substitution Secret is ever missing, `kopiur-repository` fails with
`envsubst error: variable not set (strict mode): "KOPIUR_R2_ENDPOINT"`. That
failure is contained to this one Kustomization and cannot affect any other.

## Deletion protection - why it is pinned

A kopiur `Snapshot` CR **owns** its kopia snapshot through a finalizer, so
deleting CRs can delete backup data. VolSync has no equivalent: deleting a
`ReplicationSource` never touches the restic repository. Upstream
[ADR-0006][adr6] records a real incident where a flapping `SnapshotSchedule` let
ownerReference GC fire 600-700 concurrent `kopia snapshot delete` Jobs at one
repository. **We run Flux with `prune: true`, which is exactly that shape.**

Pinned explicitly on both repositories (verified against the shipped
0.10.5 CRDs, not transcribed):

| Field | Set to | Axis it guards |
|---|---|---|
| `deletionProtection.threshold` | `10` | mass-deletion circuit breaker |
| `onNamespaceDelete` | `Orphan` | namespace teardown |

The breaker is deliberately **axis-independent**: it also holds a
schedule-deletion cascade and a namespace teardown that cross the threshold.
kopiur's own retention prunes are stamped `pruned-by` and are never held.

`SnapshotPolicy.spec.deletion.onPolicyDelete` and
`SnapshotSchedule.spec.deletion.onScheduleDelete` are **per-object** fields -
`ClusterRepository.spec.scheduleDefaults` carries only `timezone`, so Stage 0
cannot pin them here. Stage 1's backup component pins both to `Retain` on every
policy and schedule it creates (see that component's Readme). Both still default
to `Retain` in the 0.10.5 CRDs; the pin is so neither an upstream default change
nor a careless edit can quietly arm the cascade.

## Upgrades - treat like a Talos bump, not a dependency bump

- The chart tag is **exact** (`0.10.5`) and kopiur is excluded from unattended
  Renovate merging in `.renovate/autoMerge.json5` (as the **last** packageRule -
  Renovate lets a later rule override an earlier one). Renovate still proposes
  bumps as PRs; a human takes them.
- `crds: CreateReplace` is set on **both** `install` and `upgrade`. Helm's
  `crds/` directory is install-only, so without it a chart bump leaves the old
  CRD schema in place, the API server prunes the new fields, and policies are
  silently refused. 0.10.0 shipped exactly that shape.
- Read upstream `docs/upgrade.md` before every bump. `kopiur.home-operations.com`
  is `v1alpha1` and broke its user-visible API roughly once a fortnight through
  July 2026 (settling since 0.10.1). The alpha risk is captain-accepted.

## What Stage 1 decided (and what is still open)

Stage 1's backup component owns retention, schedules, mover cache mode,
`copyMethod`, and the per-object deletion pins - see
[`kubernetes/components/kopiur/Readme.md`](../../../../components/kopiur/Readme.md).
Do not re-decide those here.

**Decided since Stage 0: credential projection is ON.**
`features.credentialProjection.enabled: true` in `app/helmrelease.yaml` and
`credentialProjection.allowed: true` on **both** `ClusterRepository` objects in
`repository/clusterrepository.yaml`. Captain decision 2026-08-30, key
`credential-scope`. The operator mints a repository credential into the mover's
namespace for the length of a run and reaps it, so no repository credential sits
at rest in any app namespace - which is why there is no longer a per-namespace
credential copy to extend. The accepted cost is that this flag adds unscoped
`create`/`patch`/`delete` on `secrets` to the operator's ClusterRole
(`create` cannot be scoped to a name), making a compromised kopiur operator a
cluster-wide credential-rewrite primitive. Full rationale, the third required
leg (the consumer opt-in, which lives in the component), and the observability
and rollback story: the component Readme's
[Credentials](../../../../components/kopiur/Readme.md#credentials---operator-minted-per-run-nothing-at-rest)
section. **Do not widen the operator's Secret permissions further on top of
this** - in particular `features.kopiaUi` asks for the same unscoped verbs for a
different purpose and is not covered by that decision.

Still open beyond Stage 0:

- Repository `maintenance` and `verification` schedules remain at their managed
  defaults (quick every 6h, full daily) until there is enough data to justify
  retuning.

[adr6]: https://github.com/home-operations/kopiur/blob/main/docs/adr/0006-mass-deletion-protection.md
