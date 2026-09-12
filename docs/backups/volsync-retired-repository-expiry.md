# Expiring retired VolSync restic repositories

> ## THE RETENTION DESCRIBED HERE IS NOT IN FORCE
>
> **Nothing in this repository applies these rules, and merging does not apply them.** No object
> store has been configured. No object has been deleted, and none is scheduled to be.
>
> This is not a caveat - it is the single most important fact on the page. The declarative path
> that *would* normally carry a change like this **silently ignores it**: Rook's OBC controller
> has no update path, so a `bucketLifecycle` on the 341-day-old `volsync` OBC is accepted into
> Git, renders clean, passes review, and never reaches the bucket. A reader concluding from this
> document that retired backups are expiring would be wrong in the most dangerous direction.
>
> The rules become real only when an operator runs
> [`volsync-retired-expiry-apply-plan.md`](volsync-retired-expiry-apply-plan.md) against each of
> the three object stores. Until then this is a decision on paper.
>
> When it *is* applied, the earliest deletion is **2027-03-31** - about 6.6 months after the
> decision - and the sole-copy tier is **2027-09-30**, about 12.6 months.

**Captain's decision, 2026-09-12: an expiry date. Not deletion now, not keeping it
indefinitely.** A recovery window is preserved, and unbounded growth stops for every future
app retirement. Scope decided the same day, on the corrected measurement below: **all 48
retired repositories across all three destinations**, not the 30 on ceph alone.

This document is the decision record. The apply runbook is
[`volsync-retired-expiry-apply-plan.md`](volsync-retired-expiry-apply-plan.md). The
machine-readable policy is
[`scripts/volsync-retired-expiry/ledger.yaml`](../../scripts/volsync-retired-expiry/ledger.yaml);
[`render_lifecycle.py`](../../scripts/volsync-retired-expiry/render_lifecycle.py) turns it into
S3 lifecycle rules; `scripts/ci/volsync-retired-expiry-test.py` is the gate.

## The decision

| tier | what it is | prefixes | size (all 3 destinations) | expires |
|---|---|---|---|---|
| `redundant` | Retired from VolSync in the kopiur Stage 5 migration. The claim still exists and kopiur backs it up **today on both ceph and r2**, and the fleet is restore-proven on both. | 26 | 96.04 GiB | **2027-03-31** |
| `sole_copy` | App and volume are gone from the cluster entirely. This restic repository is the **only** remaining copy of that data anywhere. | 22 | 30.00 GiB | **2027-09-30** |

**Total reclaimed over time: 126.03 GiB across 48 repositories.** Nothing is reclaimed before
2027-03-31; the recovery window is **~6.5 months** for data that already has two current
backups, and **~12.5 months** for data that has none.

Two dates rather than 48. Per-app precision would be false precision, and 48 individually
chosen dates would be 48 chances to fat-finger one.

### Why those numbers

**`redundant` - 2027-03-31 (~6.5 months).** These 26 claims are not losing their backups; they
are losing a *third* copy of data that kopiur already holds on two destinations, restore-proven
(`kopiur-restore-proof-2026-09-01.md`). Their only remaining value is pre-migration history, and
the migration's last wave landed 2026-09-04 - a defect it introduced surfaces in weeks, not
quarters. Six months clears two quarterly review cycles with room to spare.

**`sole_copy` - 2027-09-30 (~12.5 months).** Deliberately double, because for these there is no
second copy and no way to make one. 12 months is not a round number picked for tidiness: it is
the retention this repo *already* chose for data it cares about, in `retain.monthly: 12` on the
r2 `ReplicationSource` (`kubernetes/components/volsync/r2/replicationsource.yaml`). Applying the
captain's own existing answer to "how far back is worth keeping" is the defensible floor for
data with no fallback, and a year covers a full seasonal cycle of "actually, bring that app
back".

Both are one line each in the ledger. Overrule a number without touching the design.

## Measured state, 2026-09-12

Read-only listing of all three `volsync` buckets (`radosgw-admin bucket list` for ceph,
`ListObjectsV2` for r2 and minio). A VolSync restic repository lives at `<bucket>/<APP>`, so one
top-level prefix is one whole repository.

| destination | prefixes | retired | live | retired size | live size |
|---|---|---|---|---|---|
| ceph (`rook-ceph-rgw`) | 33 | 30 | 3 | **32.48 GiB** / 5,678 obj | 0.011 GiB |
| r2 (Cloudflare) | 31 | 28 | 3 | **34.25 GiB** / 5,390 obj | 1.649 GiB |
| minio (NAS) | 49 | 46 | 3 | **59.30 GiB** / 10,975 obj | 0.009 GiB |

### The scope was established here, not inherited

This work was commissioned as **30 retired prefixes / 32.48 GiB**. That figure is correct and was
confirmed to the byte - but it describes **one of three destinations**. The real quantity is
**48 retired repositories / 126.03 GiB**, and the captain was shown the corrected number and
chose it deliberately on 2026-09-12. Narrowing back to the original scope would have left
~93 GiB unmanaged and growing.

How it was measured, so a future reader can redo it rather than trust it:

1. Every VolSync restic repository is `<bucket>/<APP>`, from the `RESTIC_REPOSITORY` template in
   `kubernetes/components/volsync/{ceph,minio,r2}/externalsecret.yaml`. So the set of
   repositories on a destination is the set of top-level prefixes in its bucket.
2. **All three destinations were listed, not just ceph** - that is the whole correction. ceph via
   `radosgw-admin bucket list --bucket=volsync` in the `rook-ceph-tools` pod; r2 and minio via
   `ListObjectsV2` with the credentials already in the live `*-volsync-<dest>-secret` Secrets.
   All read-only.
3. Objects were aggregated by first path segment, giving per-repository object count, byte total
   and last-write date on each destination.
4. The live set was fixed independently, two ways that agree: 9 `ReplicationSource`s in the
   cluster (3 claims x 3 destinations), and the 3 overlays in Git still wiring up
   `components/volsync`. Everything else is retired by definition.

The gap is almost entirely minio, which carries **18 prefixes ceph no longer has at all** - apps
removed long before the kopiur migration, whose copies there are the only ones left: `my-claw`
16.3 GiB, `merge-wallet` 6.5 GiB, `openclaw` 3.5 GiB, `qbittorrent` 1.3 GiB, `mongodb` 1.0 GiB,
plus `jellyfin`, `immich`, `open-webui`, `qdrant`, `homeops-claw`, `cross-seed`, `open-notebook`,
`paperless-ai`, `stirling-pdf`, `calibre-web`, `perplexica`, `perplexica-dbstore` and `litellm`.
r2 also holds more than ceph for the repositories they share, because its retention tier is
deeper (`monthly: 12` against ceph's `monthly: 6`).

A second reason the ceph-only figure understated the problem: ceph's shorter retention has
already forgotten history the others still hold. `paperless-ngx` is 11.3 MiB on ceph and
**1,688 MiB on r2** - the same repository, 150x apart.

Only three repositories are live, confirmed two ways: 9 `ReplicationSource`s in the cluster
(3 claims x 3 destinations), and 3 overlays in Git still wiring up `components/volsync`
(`selfhosted/paperless-ngx.yaml`, `selfhosted/syncthing.yaml`). They are `paperless-ngx`,
`paperless-ngx-media` and `syncthing-data`.

## Why nothing ever expired these

Deleting a `ReplicationSource` never touches its restic repository. That is correct behaviour -
and it is the whole problem, because VolSync's `retain:` policy is the only thing that ever ran
`restic forget` against these repositories, and it ran as part of a backup. No backup, no
forget. The repositories have simply been frozen since their last write, the oldest since
2025-05.

**restic retention cannot express this expiry, so the house pattern is structurally unable to
help.** `restic forget --keep-within`, `--keep-last`, and every `--keep-*` tier are relative to
the *most recent snapshot in the repository*. On a frozen repository the most recent snapshot is
always within any window of itself, so every such policy keeps at least one snapshot forever and
can never reach zero. kopiur's GFS `SnapshotPolicy` has exactly the same shape and the same
limitation. **Expiring a retired repository is therefore a whole-prefix deletion on a date, not
a retention policy** - there is no per-snapshot aging left to do, because nothing will ever add
another snapshot.

## The mechanism: S3 object lifecycle with `Expiration.Date`

Every destination supports it: Ceph RGW (v20.2.4), MinIO, and Cloudflare R2 all implement
`PutBucketLifecycleConfiguration` with `Filter.Prefix` and an absolute `Expiration.Date`. That
is literally an expiry date, enforced by the object store, with no code of ours in the path.

**`Expiration.Days` would have been a bug, not a variant.** `Days` counts from each object's own
creation time. These repositories stopped being written between 2025-05 and 2026-09, so every
`Days` value small enough to be useful would delete most of this the moment it was installed -
violating the one thing the captain was explicit about. `scripts/ci/volsync-retired-expiry-test.py`
refuses any rule carrying a `Days` key.

### Exact-segment matching, and why it cannot widen

`volsync/syncthing` is **retired**. `volsync/syncthing-data` is **LIVE**. The live prefix is a
strict string extension of the retired one, and an S3 `Filter.Prefix` is a plain byte-wise
`startswith` on the object key. A rule of `syncthing` deletes the live repository.

The ledger stores **bare** names - `syncthing`, never `syncthing/`. `render_lifecycle.py` appends
the slash in exactly one function, unconditionally, and rejects any entry that already contains
one. The emitted match is `syncthing/`, and:

```
"syncthing-data/config".startswith("syncthing/")  ->  False
```

because the two strings first differ at index 9, where the live key has `-` and the rule has `/`.
That is a property of string comparison, not a convention anyone has to remember. There is no
ordering, escaping or globbing subtlety available to get it wrong, and the dangerous version is
not expressible: a ledger entry of `syncthing` cannot produce a rule reaching `syncthing-data`.

The slash is safe to rely on because it is measured, not assumed: **0 of the 6,123 keys in the
ceph bucket lack a `/`**, so `<name>/` selects exactly that repository and nothing else.

Verified against every real object key on 2026-09-12:

| check | result |
|---|---|
| live objects in the ceph bucket | 445 |
| live objects matched by any of the 48 rules | **0** |
| `syncthing/` vs the 103 retired `syncthing` objects | 103 matched (all) |
| `syncthing/` vs the 107 live `syncthing-data` objects | **0 matched** |
| rule match set vs the retired object set (5,678 of 6,123) | **exactly equal** |
| live `syncthing-data` objects a naive no-slash match would have destroyed | **107** |

That last row is the failure this design exists to prevent, and it would have looked correct in
review.

On top of the structural property, `render_lifecycle.py` re-derives the safety of every rule
against every protected prefix and raises `UnsafeLedger` rather than emitting anything at all if
one is even arguably in range. The structure is what makes it correct; the assertion is what
makes a future edit that breaks the structure fail loudly instead of quietly.

## Why nothing in this repo applies it

All three declarative routes were checked and all three are structurally unavailable. This is
the reason the change ships as policy plus a runbook rather than as a manifest.

1. **Rook `ObjectBucketClaim` `additionalConfig.bucketLifecycle` - blocked twice.** The running
   operator (Rook v1.20.7) does support the key, but the cluster's
   `ROOK_OBC_ALLOW_ADDITIONAL_CONFIG_FIELDS` is `maxObjects,maxSize`, so an OBC declaring it is
   rejected outright. More decisively, **Rook's OBC controller has no update path**: it watches
   only ConfigMaps and CephCluster, and lifecycle is applied in the provisioner's `Provision()`
   and `Grant()` paths. The `volsync` bucket was provisioned 341 days ago, so adding the field to
   a bound OBC is a **silent no-op** - the change would look correct in Git, in `flate`, and in
   review, and never reach the bucket. Widening the allow-list would not fix that.
2. **R2 and MinIO are not declared in this repo at all.** The r2 bucket is a Cloudflare resource
   and the minio bucket lives on the NAS. Neither has a Kubernetes object, a Terraform stack, or
   any other GitOps hook here.
3. **Building an applier would be the dangerous version.** A recurring in-cluster job holding
   write credentials for all three object stores, enumerating buckets and deleting what it
   decides is retired, is precisely the "recurring job that can widen its match" shape to avoid -
   and it would add a whole new failure class to replace a one-time configuration step.

### What runs automatically, and what does not - chosen deliberately

- **The expiry runs automatically and forever.** Once the rules are installed, each object store
  enforces its own dated deletion. No code of ours executes, nothing polls, nothing can drift.
- **Installing the rules is a one-time human action per bucket.** There is no automation that
  could do it, and the thing that would need automating - deciding what is retired - is exactly
  the decision that must never be automated.
- **Future retirements add a ledger line and a re-apply.** One-shot-and-never-again was the
  failure mode to avoid; the ledger plus the CI gate is what makes the next retirement a
  reviewed one-line change instead of a forgotten one. Add this to the retirement procedure in
  `kubernetes/components/kopiur/Readme.md` when a claim's VolSync engine is removed.

## Applying it

The full runbook - exact commands per destination, what each changes, mandatory read-back
verification, and undo - is [`volsync-retired-expiry-apply-plan.md`](volsync-retired-expiry-apply-plan.md).

Two things from it that belong in the decision record:

- **The three destinations do not have equal prerequisites.** ceph's credential is the OBC bucket
  owner and minio's is permitted, both measured. **r2's in-cluster token cannot manage lifecycle
  at all** (`AccessDenied` on `GetBucketLifecycleConfiguration`): R2 requires the
  `Workers R2 Storage Write` permission group, and the cluster's token is object-scoped by
  design. Applying the r2 third needs a scoped Cloudflare token minted for that one call and
  revoked afterwards - it must not be stored in the cluster or 1Password.
- **The write path is unexecuted.** Credential authority and the read path are measured; no
  `PutBucketLifecycleConfiguration` has been issued. That is why the read-back comparison is
  built into `apply_lifecycle.py` rather than being a runbook step someone could skip.

## Reopening / maintenance

- **Before either date arrives**, decide whether to extend or let it run. Nothing else will
  prompt you.
- **`protected` must track reality.** The CI gate derives the live set from the overlays that
  still use `components/volsync` and fails if the ledger disagrees, in either direction. If
  `paperless-ngx` is ever retired from VolSync, moving it into a tier is a deliberate edit that
  CI then permits - not something that happens by accident.
- **The expiry is all-or-nothing per repository, but not instantaneous.** Object stores process
  lifecycle asynchronously over hours, so a repository is briefly partially deleted and therefore
  unusable before it is fully gone. That window begins on the expiry date, which is past the
  recovery window by definition - but it does mean "the date" is the end of the window, not the
  start of a grace period.
