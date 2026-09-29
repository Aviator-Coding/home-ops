# Operator, repositories, and cluster loss

## What is installed

`kubernetes/apps/base/system/kopiur/` is the operator plus two cluster-scoped
`ClusterRepository` objects, `ceph` and `r2`. Each has its own `kopiur` bucket.
MinIO is not a kopiur destination.

HelmRelease invariants (do not relax):

- CRD install is `CreateReplace` (the cluster default for HelmReleases).
- `features.credentialProjection.enabled` is the RBAC leg in
  [credentials.md](credentials.md). The cluster-wide secret verbs are an
  accepted cost. Do not add `features.kopiaUi`.
- The chart's own four alerts are the right vendor rules. Extra rules live in
  `app/prometheusrule.yaml` ([alerts.md](alerts.md)).

`ClusterRepository` invariants:

- Every `secretRef` sets `namespace:` explicitly.
- `deletionProtection` threshold stays at 10, with `onNamespaceDelete` set, so
  a namespace teardown cannot mass-delete snapshots. Upstream recorded
  ownerReference GC firing hundreds of concurrent snapshot deletions at one
  repository. Flux `prune: true` is that shape on the claim side;
  `deletionProtection` is the repository side.
- `spec.parameters` is write-only from Git
  ([repository-maintenance.md](repository-maintenance.md)).
- r2 `create.enabled: false`. A wrongly addressed r2 with create turned on
  writes an empty repository and then treats it as the real one.
- ceph `onMissingSnapshot` is not a repository field. The standing `Restore`
  pins `onMissingSnapshot: Fail`. `Continue` is forbidden
  ([restore-and-cache.md](restore-and-cache.md)).

Both of those pins are asserted by `scripts/ci/kopiur-stage0-test.py` and
`kopiur-stage1-test.py`.

## Credentials and buckets

See [credentials.md](credentials.md). The Ceph bucket reclaim policy matches
the volsync bucket (`Delete`). Changing it is a two-bucket decision.

## Reading a repository

```sh
kubectl get clusterrepository -o wide
kubectl -n system get maintenance
# one app
kubectl -n <ns> get snapshotpolicy,snapshotschedule,restore
kubectl -n <ns> get snapshot -l kopiur.home-operations.com/policy=<app>-ceph
```

A green `SnapshotSchedule` with `filesNew: 0` backed up an empty tree. Read
`.status.stats`.

## Full-cluster loss

The rebuilt `ceph` repository is new and empty. Populators with `Fail` stay
failed instead of seeding empty PVCs. Restoring the fleet from r2 is a
DR-mode commit **before** bootstrap, then
[full-cluster-restore-r2.md](full-cluster-restore-r2.md).

Five claims have no populator path and are hand-restored. The runbook names
them: `database/falkordb`, `database/surrealdb`,
`selfhosted/paperless-ngx`, `paperless-ngx-media`, `syncthing-data`.

Do not flip `onMissingSnapshot` to `Continue` to "get the cluster up". That
writes emptiness into r2 as the newest snapshot of the same identity, and
`offset: 0` plus GFS then treats it as real.

## Postgres is not this operator

`database/cloudnative-pg` archives with barman to one destination. The off-site
mirror CronJob is `suspend: true` until its 1Password item exists. Until that
follow-up, postgres-17 has a single copy. Operating rules:
`kubernetes/apps/base/database/cloudnative-pg/offsite-mirror/README.md`.
