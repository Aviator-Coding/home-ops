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
| `repository/` | the three `ClusterRepository` objects and their credential `ExternalSecret`s |

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
- **MinIO** (TrueNAS) and **Cloudflare R2** - external systems GitOps cannot
  reach, so these two must be created through their own consoles.

### 2. The `kopiur` 1Password item

In the **`Automation`** vault. It must be that vault specifically: the
`PushSecret` in `backend/` writes the Ceph keys into this same item through the
`onepassword-automation` store, which is pinned to `Automation` (the shared
`onepassword` store resolves writes across three vaults by priority, so its
target is not deterministic). Connect cannot see the hyphenated `Home-Lab` vault
at all.

Fields the captain must supply:

```
CEPH_KOPIA_PASSWORD
MINIO_KOPIA_PASSWORD   MINIO_ACCESS_KEY_ID   MINIO_SECRET_ACCESS_KEY
R2_KOPIA_PASSWORD      R2_ACCESS_KEY_ID      R2_SECRET_ACCESS_KEY
R2_ENDPOINT            # <accountid>.r2.cloudflarestorage.com - bare host, no scheme
```

Written automatically, do **not** enter these by hand:

```
CEPH_ACCESS_KEY_ID     CEPH_SECRET_ACCESS_KEY   # pushed from the OBC's Secret
```

> **Lose a `KOPIA_PASSWORD` and that repository is permanently unrecoverable.**
> kopia cannot decrypt without it and there is no recovery path. The three
> passwords must be long, random, distinct from each other and from every restic
> password, and **recorded outside the cluster** - 1Password alone is the same
> failure domain as the thing being restored.

`R2_ENDPOINT` lives in 1Password rather than in this repo because home-ops is
**public** and the R2 account id is deliberately not committed anywhere in it
today. `backend.s3.endpoint` is a plain CRD string with no Secret ref, so the
value reaches the CR through Flux `postBuild` substitution instead
(`app/externalsecret.yaml` -> the `kopiur-substitutions` Secret ->
`substituteFrom` on the `kopiur-repository` Kustomization).

Until that item exists, `kopiur-repository` fails with
`envsubst error: variable not set (strict mode): "KOPIUR_R2_ENDPOINT"`. That is
expected, and it is contained to that one Kustomization.

## Deletion protection - why it is pinned, and what could not be pinned yet

A kopiur `Snapshot` CR **owns** its kopia snapshot through a finalizer, so
deleting CRs can delete backup data. VolSync has no equivalent: deleting a
`ReplicationSource` never touches the restic repository. Upstream
[ADR-0006][adr6] records a real incident where a flapping `SnapshotSchedule` let
ownerReference GC fire 600-700 concurrent `kopia snapshot delete` Jobs at one
repository. **We run Flux with `prune: true`, which is exactly that shape.**

Pinned explicitly on all three repositories (verified against the shipped
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
