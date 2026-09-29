# Credential projection

A `ClusterRepository` mover Job runs in the **workload** namespace and loads
credentials with `envFrom`, which is namespace-local. A repository whose
Secrets live only in `system` backs up nothing outside `system`. The CRs
reconcile clean and the mover fails at run time.

`credentialProjection` mints the credential into the mover's namespace for the
run and reaps it. No repository credential sits at rest in an app namespace.
Do not reintroduce per-namespace ExternalSecret copies or a restricted
`ClusterSecretStore` for this.

## Three legs, all required

| Leg | Where | What a miss looks like |
|---|---|---|
| `features.credentialProjection.enabled` on the HelmRelease | operator RBAC | 403 in status (the one miss that surfaces) |
| `credentialProjection.allowed` on **both** `ClusterRepository` objects | owner gate | CR green, mover fails |
| `credentialProjection.enabled` on each `SnapshotPolicy` and `Restore` | consumer opt-in, in the component | CR green, mover fails |

Every `secretRef` in the repository spec must set `namespace:` explicitly, or
the operator does not know what to copy.

The standing `Restore` carries leg 3, but `ssa: IfNotPresent` means a `Restore`
that already exists never receives a later edit. A hand-written drill `Restore`
must set `credentialProjection: {enabled: true}` in its own spec.

## Accepted cost

Leg 1 adds `create` / `patch` / `delete` on `secrets` to the operator
ClusterRole, and `create` cannot be scoped to a name. A compromised kopiur
operator is a cluster-wide credential-rewrite primitive. Do not widen it.
`features.kopiaUi` wants the same verbs and is not enabled.

## What to watch

`kopiur_projected_secrets_live` is a leader-only census written once per work
sweep (default 6h, `KOPIUR_WORK_SPEC_SWEEP_INTERVAL_SECS` unset), not a
per-run gauge. The fast path does not update it. A flat 0 during a backup is
normal: projected Secrets live for about a minute and the 30s scrape often
misses them.

Per-run evidence is the operator log line
`reaped projected credentials copy secret=<snapshot>-creds-N` and the counter
`kopiur_secrets_projected_total`. `Snapshot.status.cleanup.credsReapedAt` is
set on every run whether projection is on or off.

The leak alert is `min_over_time` over 13h of `max without (pod, instance)`,
not a bare `> 0` and not `deriv`. See [alerts.md](alerts.md).

## Items

1Password items `kopiur-ceph`, `kopiur-r2`, `kopiur-ceph-bucket` live in the
Connect-visible Homelab vault (skill `secrets-1password`). The Ceph S3 keys
the operator projects come from the ObjectBucketClaim's generated Secret.
`kopiur-ceph-bucket` is a record nothing reads. Nothing kopiur owns reads the
`volsync-template` item.

The Ceph bucket is an `ObjectBucketClaim` on `ceph-bucket`
(`reclaimPolicy: Delete`), the same class as the long-lived `volsync` bucket.
Do not hand-make a one-off bucket to "fix" that reclaim policy unless the
volsync bucket is in the same change.
