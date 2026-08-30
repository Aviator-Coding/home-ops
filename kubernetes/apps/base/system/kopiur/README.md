# kopiur - Stage 0

kopiur is being introduced as the eventual replacement for VolSync, in a staged
migration whose governing constraint is **no window in which any PVC is
unprotected**. This directory is **Stage 0 only**.

Stage 0 installs the operator and declares *where* backups could go. It creates
no `SnapshotPolicy` and no `SnapshotSchedule`, so **nothing is backed up by
kopiur yet** and every one of the 105 VolSync `ReplicationSource`s keeps running
exactly as before. The change is purely additive.

**Rollback:** delete the `kopiur` and `kopiur-repository` Flux Kustomizations.
Because no policy or schedule exists, no kopia snapshot exists either, so there
is nothing to lose and zero backup impact. The repositories themselves are
`create.enabled: true` and idempotent - re-applying reconnects rather than
re-creating.

## Layout

| Path | What |
|---|---|
| `app/` | operator: `OCIRepository`, `HelmRelease`, our absent-alert `PrometheusRule`, and the substitution `ExternalSecret` |
| `backend/` | the Ceph bucket: `ObjectBucketClaim`, `CephObjectStoreUser`, and the `PushSecret` that records its generated keys in 1Password (included from `app/`) |
| `repository/` | the two `ClusterRepository` objects (`ceph`, `r2`) and their credential `ExternalSecret`s |

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
- **Cloudflare R2** - created by the captain in the R2 console, and the name is
  confirmed as `kopiur`. **The reused token cannot reach it**; see "R2" below.

There is deliberately **no MinIO repository**. MinIO changed its licensing terms
and the captain intends to retire it, so Stage 0 does not give kopiur a MinIO
destination. This does not touch MinIO in any way: the existing VolSync MinIO
backups keep running exactly as before, and retiring them is a separate future
job. The `MINIO_KOPIA_PASSWORD` already in the `kopiur` item is therefore unused
- it is left in place rather than deleted, because casually deleting credentials
is not worth the risk and it costs nothing to keep.

### 2. The `kopiur` 1Password item

Item `kopiur` in the **`Homelab`** vault. Everything writes and reads through
the shared `onepassword` ClusterSecretStore, whose vault priority (Homelab 1,
Automation 2, Services 3) resolves there. Keep all fields in this one item:
splitting them across vaults would let the priority-ordered read path silently
shadow one. Connect cannot see the hyphenated `Home-Lab` vault at all.

**Already created and verified** (2026-08-30) - do not regenerate, and note that
overwriting one after its repository is initialized loses that repository:

```
CEPH_KOPIA_PASSWORD    R2_KOPIA_PASSWORD      MINIO_KOPIA_PASSWORD  # unused, see above
```

Written automatically by `backend/`'s `PushSecret`, never by hand:

```
CEPH_ACCESS_KEY_ID     CEPH_SECRET_ACCESS_KEY
```

**No further 1Password fields are needed.** R2's S3 credentials and endpoint are
reused from the existing `volsync-template` item (`R2_HOME_OPS_ACCESS_KEY`,
`R2_HOME_OPS_SECRET_KEY`, `R2_HOME_OPS_ENDPOINT_URL`), read directly rather than
copied. Note that `R2_VOLSYNC_RESTIC_REPOSITORY` names the EXISTING restic
bucket and must never be used here, and `R2_VOLSYNC_RESTIC_PASSWORD` is restic's
own and is not reusable by kopia.

### R2 - the reused token is bucket-scoped and cannot reach `kopiur`

The bucket name and the account endpoint are both settled. The **credential is
not**, and that is what blocks the `r2` repository.

Confirmed:

- Bucket name is `kopiur`, given by the captain from the R2 console
  (`https://<accountid>.r2.cloudflarestorage.com/kopiur`).
- The endpoint already in 1Password (`volsync-template` /
  `R2_HOME_OPS_ENDPOINT_URL`, which `app/externalsecret.yaml` trims to a bare
  host) is the **same** account host as that URL - compared directly against a
  live VolSync R2 Secret, so no second endpoint value is hard-coded anywhere.

Measured in-cluster 2026-08-30, credentials never leaving the cluster, from one
pod against one endpoint over one signing path:

| Operation | `volsync` (existing) | `kopiur` (new) |
|---|---|---|
| `HeadBucket` | `BucketRegion: ENAM` | `403 Forbidden` |
| `ListObjectsV2` | returns real keys | `AccessDenied` |
| `PutObject` | not attempted (never write to the restic bucket) | `AccessDenied` |
| `GetObject` | - | `AccessDenied` |
| `DeleteObject` | - | `AccessDenied` |

The `volsync` column is the control: it proves the probe, the endpoint and the
signature are all correct, so the `kopiur` denials are the token's scope and not
a broken test. Two further readings point the same way: `ListBuckets` is denied,
and a deliberately nonexistent bucket returns `403` rather than `404` - which is
how R2 answers a **bucket-scoped** token, whereas an account-scoped one would
have said `NoSuchBucket`.

Needed from the captain: re-scope this R2 API token to include the `kopiur`
bucket, or issue one that covers it. Nothing in this repo changes when that
happens - `repository/externalsecret.yaml` reads the same
`R2_HOME_OPS_ACCESS_KEY` / `R2_HOME_OPS_SECRET_KEY` fields, so updating them in
1Password is the whole fix.

One caveat, stated plainly: a bucket-scoped token cannot tell "exists but
forbidden" apart from "does not exist", so this evidence does **not**
independently confirm the `kopiur` bucket exists. It rests on the captain's
console URL. Re-running the probe above after the token is re-scoped settles
both questions at once.

> **Lose a `KOPIA_PASSWORD` and that repository is permanently unrecoverable.**
> kopia cannot decrypt without it and there is no recovery path.
>
> The three were generated in-cluster (`openssl rand -base64 48`, distinct,
> never printed or committed), pushed to 1Password, and verified by reading them
> back out and comparing hashes - including once more after every temporary
> object was deleted. **1Password now holds the only copy.** Recommended, and
> deliberately left to the captain: take an independent offline record, since a
> vault outage or mistake would otherwise be unrecoverable.

The R2 endpoint stays in 1Password rather than in this repo because home-ops is
**public** and the R2 account id is deliberately not committed anywhere in it
today - VolSync keeps the whole endpoint in `volsync-template` for the same
reason. `backend.s3.endpoint` is a plain CRD string with no Secret ref, so the
value cannot be read from a Secret and reaches the CR through Flux `postBuild`
substitution instead (`app/externalsecret.yaml` reads
`R2_HOME_OPS_ENDPOINT_URL` and trims it to a bare host -> the
`kopiur-substitutions` Secret -> `substituteFrom` on the `kopiur-repository`
Kustomization).

If that substitution Secret is ever missing, `kopiur-repository` fails with
`envsubst error: variable not set (strict mode): "KOPIUR_R2_ENDPOINT"`. That
failure is contained to this one Kustomization and cannot affect any other.

## Deletion protection - why it is pinned, and what could not be pinned yet

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
schedule-deletion cascade and a namespace teardown that cross the threshold. So
this one field is a real backstop today, before any policy exists. kopiur's own
retention prunes are stamped `pruned-by` and are never held.

**Not pinnable at Stage 0, and this is a genuine finding.**
`SnapshotPolicy.spec.deletion.onPolicyDelete` and
`SnapshotSchedule.spec.deletion.onScheduleDelete` are **per-object** fields.
`ClusterRepository.spec.scheduleDefaults` carries only `timezone`, so there is
no repository-level default to pin them through, and setting them would require
creating policy/schedule objects - which is Stage 1, not Stage 0. Both default
to `Retain` in the 0.10.5 CRDs (verified from the shipped schema, enum
`Retain|Delete`).

> **Stage 1 acceptance criterion:** the backup component must set
> `deletion.onPolicyDelete: Retain` and `deletion.onScheduleDelete: Retain`
> explicitly rather than inheriting the defaults.

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

## Deferred to Stage 1 (deliberately not decided here)

- **`features.credentialProjection` stays `false`** (chart default, least
  privilege - enabling it grants the operator `create`/`patch`/`delete` on
  Secrets in *every* namespace it manages, and `create` cannot be scoped to a
  name). Movers running outside `system` therefore need the repository Secret in
  their own namespace. onedr0p solves this with a per-namespace `ExternalSecret`
  component instead of the projection feature. Pick one in Stage 1.
- Retention, schedules, mover cache mode and `copyMethod` are all Stage 1.
- Repository `maintenance` and `verification` schedules are left at their
  managed defaults (quick every 6h, full daily) until there is data to maintain.

[adr6]: https://github.com/home-operations/kopiur/blob/main/docs/adr/0006-mass-deletion-protection.md
