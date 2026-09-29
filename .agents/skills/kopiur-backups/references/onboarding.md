# Onboarding a kopiur claim

## Two shapes, not interchangeable

1. **Born-kopiur** (never had VolSync). Include `components/kopiur` only. The
   chart or app base owns the PVC. Do **not** add `components/kopiur/pvc`: that
   component stamps a `dataSourceRef` at the standing populator and is only for
   `RETIRED_CLAIMS` in `scripts/ci/kopiur-stage5-test.py`. Record the claim in
   `NEVER_VOLSYNC` in `scripts/ci/kopiur-stage3-test.py`. Live:
   `database/falkordb`, `database/surrealdb`.
2. **VolSync retirement takeover.** Include both `components/kopiur` and
   `components/kopiur/pvc`. See [retirement-and-removal.md](retirement-and-removal.md).

`dependsOn: kopiur-repository` (namespace `system`) is required. The admission
webhook is `failurePolicy: Fail`, so a missing operator rejects the CRs.
There is no per-namespace credential to create first.

## One volume per Kustomization

Every object is named from `${APP}`, and Flux allows one `postBuild.substitute`
map. A second PVC is a second Kustomization with `path:
./kubernetes/components/kopiur/backup` (no pvc include; the claim already
exists) and `APP` set to the claim name. Same rule as VolSync. Live second
volumes: `syncthing-data`, `paperless-ngx-media`.

`syncthing` (config) and `syncthing-data` (files) share one overlay file and
are different claims. Grep both names before editing either.

## Identity

Measure file ownership on the claim. Do not copy `runAsUser`.

- `media/plex`, `media/tdarr-config` and `media/calibre-web-automated` run as
  uid 0 with `fsGroup: 2000` and own files `2000:2000`.
- `ai/hermes` sets no `runAsUser` (only `fsGroup: 10000`) and owns its tree
  `10000:10000`.
- `downloads/prowlarr-config` is `3002:3000` (gid is not the uid).
- `selfhosted/obsidian-livesync` is CouchDB `5984:5984` on both engines.
- `downloads/recyclarr-config` is the one identity taken from the pod spec
  rather than a live file census: the CronJob pod exists about 18 seconds a
  day. Its manifest says so.
- A declared `APP_UID` / `APP_GID` that no template reads is a liability.
  Only `KOPIUR_PUID` / `KOPIUR_PGID` and `VOLSYNC_PUID` / `VOLSYNC_PGID` drive
  a mover. Delete the dead pair.

`SecurityContextCompatible` requires the mover uid to match **every** container
of every pod that mounts the claim, init containers included, even containers
that mount nothing. It is not reliably emitted. Absence is not a failure.
`changedetection`'s browserless sidecar is pinned to uid 999 because Chrome
does not launch as 1000. Root `fix-permissions` init containers
(`pgadmin`, `falkordb`) also suppress the condition. Proof the identity works:
a `Succeeded` snapshot whose `.status.stats` covers the volume, plus a
read-only mount as the mover uid
(`find <mount> -type f ! -readable`).

### Root movers

Set `KOPIUR_PUID` and `KOPIUR_PGID` to `"0"`. Do not use
`inheritSecurityContextFrom`: it does not flow to the standing `Restore`.

The policy is admitted. The `Snapshot` then sits `Pending` with
`MoverPermitted=False` / `PrivilegedMoverNotPermitted` until the namespace
carries `kopiur.home-operations.com/privileged-movers=true`. Patch that
annotation onto the overlay that actually emits the Namespace
(`kubernetes/apps/main/<ns>/kustomization.yaml`, target `name: not-used`).
`kubernetes/apps/base/home-automation/namespace.yaml` is not in any Flux
inventory. The annotation is a gate for the whole namespace, not an identity
change. Live root mover: `home-automation/matter-server`. A root restore
preserves modes and rewrites mixed uids to `0:0`.

## `wait: true` deadlocks the component

`ceph/restore.yaml` reports `Ready=False` / `AwaitingPvcDataSourceRef` until a
**rebuilt** claim claims it. `dataSourceRef` is immutable on a bound PVC, so a
live claim never repoints. `wait: true` assesses every inventory object and
blocks forever. `flate` does not catch this.

`database/pgadmin` and `media/calibre-web-automated` keep the backup half in
its own `wait: false` Kustomization (`path:
./kubernetes/components/kopiur/backup`) and only `components/kopiur/pvc` on the
claim Kustomization. The backup Kustomization must **not** `dependsOn` the
claim Kustomization. On a greenfield apply that edge cycles: backup waits for
the Deployment, the Deployment mounts the PVC, the PVC waits for the `Restore`,
the `Restore` is created by the backup Kustomization. Workload `healthChecks`
do not break the cycle, because the Deployment is still blocked on the PVC.
Both backup Kustomizations still `dependsOn` `kopiur-repository`. The
greenfield apply of these two overlays has not been exercised; the fix is the
dependency graph, not a demonstrated namespace rebuild.

## Hooks

`SnapshotPolicy.spec.hooks` (`beforeSnapshot` / `afterSnapshot`:
`workloadExec`, `runJob`, `httpRequest`) is per-app. The component templates
do not substitute it. Apply with the owning Kustomization's `spec.patches`
(JSON6902, `target: {kind: SnapshotPolicy}`, no `name`, so both destinations
match). Live example: `surrealdb-kopiur` in
`kubernetes/apps/main/database/surrealdb.yaml`.

`spec.repositories` fan-out cannot combine with hooks. This fleet already uses
one policy per repository. Before believing a feature is missing, read the
installed CRD
(`snapshotpolicies.kopiur.home-operations.com`) against the chart tag in
`ocirepository.yaml`.

## Dangling VolSync aliases

`existingClaim: ${VOLSYNC_CLAIM:-*app}` is a bare token, not a YAML anchor.
Dropping `VOLSYNC_CLAIM` renders `existingClaim: *app` and `flate` fails the
Kustomization on an unknown anchor. `${VOLSYNC_CLAIM}` with no default renders
an **empty** claim name. Only `selfhosted/paperless-ngx` still carries
`${VOLSYNC_CLAIM:-*app}`, because it stays dual-engine. Grep the HelmRelease
for `VOLSYNC_` before assuming the overlay is the only reader.

## Variables the component actually reads

| Variable | Role |
|---|---|
| `APP` | Object name prefix |
| `KOPIUR_CLAIM` | PVC name when it differs from `APP` (`sonarr-config`). Also the name a rebuild provisions |
| `KOPIUR_CAPACITY` | Create-time size. Must match the live claim |
| `KOPIUR_PUID` / `KOPIUR_PGID` | Mover identity. Default 1000 |
| `KOPIUR_CACHE_CAPACITY` | Restore cache. Default `2Gi`. See [restore-and-cache.md](restore-and-cache.md) |
| `KOPIUR_SCHEDULE_CEPH` | Default `H 1-23/4 * * *` (odd hours). Leave it |
| `KOPIUR_SCHEDULE_R2` | Required per namespace. See [schedules-timezone.md](schedules-timezone.md) |
| `KOPIUR_SCHEDULE_TIMEZONE` | Default `America/New_York`. Do not unset |
| `KOPIUR_ACCESSMODES` | Default `ReadWriteOnce` |

`obsidian-livesync`: claim capacity and cache move as a pair. The cache is
safe only while it is at least the claim size (a 2Gi claim cannot outgrow a
2Gi cache). Do not raise one without the other.

`calibre-web-automated` and `cloudnative-pg` (`pgadmin`): no `dependsOn` from
the backup Kustomization back to the claim, and no PVC object in
`healthChecks`.

## Why the component pins these values

- `copyMethod: Snapshot` (also the CRD default): the mover reads a temporary PVC
  restored from a CSI VolumeSnapshot and never mounts the live claim, which is
  what makes running kopiur and VolSync against one PVC safe.
- `mover.cache.mode: Ephemeral` (also the default, pinned because it is
  load-bearing): the kopia cache is discarded after each run, so kopiur adds no
  standing cache PVCs. VolSync alone held 105 cache PVCs / 324 GiB of
  `ceph-block` when this was chosen; `Persistent` would start a second set.
- `podSecurityContext` mirrors VolSync's `moverSecurityContext`, so both engines
  read the claim as one identity and a kopiur restore reproduces VolSync's
  ownership.
- `retention` copies the matching VolSync `retain` block so the engines stay
  comparable. r2 has no `keepHourly`: it is the billed offsite copy and takes
  one snapshot a day, so an hourly tier would retain nothing.
- Ceph runs on odd hours (`H 1-23/4`), r2 at hour 4, against VolSync's even
  hours and hour 3. The hour, not the minute, separates the engines, so never
  hand-assign the `H` minute. `timezone` defaults to `America/New_York` because
  kopiur reads cron as UTC otherwise and the stagger breaks at the next DST
  change. `jitter` is derived from (scheduleUID, slot) on top of the hashed minute.
