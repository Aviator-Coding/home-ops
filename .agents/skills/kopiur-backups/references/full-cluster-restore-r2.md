# Full-cluster rebuild: restore every volume from r2

Skill `kopiur-backups`. Stage 0 background: [stage0-operator.md](stage0-operator.md).

Use this when the Ceph cluster's data is gone: disks wiped, hardware lost, or a new FSID
(`RECOVERY-PROCEDURES.md` section 3,
`kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md`).
If the OSD disks and FSID survive, use that document instead: Ceph comes back with its
buckets and the standing populators restore from `ceph` as designed.

To rebuild **one** claim on a healthy cluster, use
skill `volsync-carveouts`, `references/corrupt-claim-recreation.md`, not this.

## Why a plain rebuild is not enough

A rebuild renders Git. Every standing populator `Restore`
(`kubernetes/components/kopiur/ceph/restore.yaml`) reads the `ceph` repository, and a
rebuilt Ceph has a new, empty `kopiur` bucket. The `ceph` `ClusterRepository` keeps
`create.enabled: true`, so kopiur creates a new, empty repository there and reports it `Ready`.

- The standing populator is **fail-closed** (`onMissingSnapshot: Fail`). On a rebuild with no
  DR-mode commit, every populator claim fails: the `Restore` goes `Failed`, the PVC stays
  unbound, the app stays `Pending`, and `KopiurRestoreFailed` fires. That is the intended loud
  failure. It protects r2, because an unbound claim cannot be backed up. The fix is the
  DR-mode commit below, not a retry.
- The populator used to carry `onMissingSnapshot: Continue`, and a `Restore` created before the
  change keeps it until it is deleted and recreated (`ssa: IfNotPresent`). On a rebuild that
  brought every volume back **empty with every signal green**, and the next daily r2 backup then wrote the empty state into
  r2 as the newest snapshot of the same identity. GFS retention would later prune the real
  pre-disaster history. If a cluster was rebuilt on the old config, go to
  [Recovering from a silent empty rebuild](#recovering-from-a-silent-empty-rebuild) now.
- The `r2` `ClusterRepository` is **connect-only** (`create.enabled: false`). A wrong endpoint,
  bucket or credential shows as `RepositoryNotInitialized` and `KopiurRepositoryNotReady`,
  never as a new, empty "r2".
- The standing populators exist for the 26 claims on `components/kopiur/pvc` only. **5 more
  claims need a hand restore** (phase 4).

What survives a full loss, off-cluster:

| Store | Holds |
|---|---|
| Cloudflare R2 bucket `kopiur` | kopiur `r2` repository, all 31 backed-up claims (daily) |
| LAN TrueNAS MinIO | CNPG barman archive for `database/postgres-17`; VolSync `minio` restic for the 3 carve-outs |
| 1Password `Home-Lab` | bootstrap and Talos secrets (`vals`) |
| 1Password `Homelab` | `kopiur-r2`, `kopiur-ceph`, `cluster`, and every other ExternalSecret source |
| GitHub | this repo |

Not in any backup engine, by design, so empty after a rebuild is **not** a restore failure:
`ai/shared-files`, `ai/shared-xml`, `downloads/shared-downloads`, `downloads/sabnzbd-incomplete`,
`media/tdarr-temp`, `database/emqx-core-data-*`, and the vLLM model caches.

## Phase 0 - Before touching anything

1. **Make sure the old cluster is dead**, or at least that its kopiur controller is scaled to 0.
   Two clusters must never run `ReadWrite` maintenance against the same r2 repository: both
   claim the owner `kopiur/clusterrepository/r2`.
2. **Record `T_LOSS`** (UTC, RFC3339): the last moment the old cluster was healthy. It is the
   `asOf` anchor for every hand restore.
3. **Check off-cluster prerequisites:** GitHub; 1Password `Home-Lab` (bootstrap, Talos);
   `Homelab` items `kopiur-r2` (`KOPIA_PASSWORD`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
   `R2_ENDPOINT_URL`), `kopiur-ceph` (`KOPIA_PASSWORD`) and `cluster`; the TrueNAS MinIO. Check
   that each field has a **value**: an empty 1Password field syncs as a healthy, zero-length
   secret.
4. Optional but strong: from a workstation, connect kopia **read-only** to R2 bucket `kopiur`
   and run `kopia snapshot list --all`. Save each identity's newest snapshot at or before
   `T_LOSS`. Identities are `<app>-r2@<namespace>:/pvc/<claim>`. Never print credentials.

## Phase 1 - The DR-mode commit (to `main`, BEFORE bootstrapping)

A rebuilt cluster creates every `Restore` fresh from Git, so whatever `main` says when Flux
first reconciles is what they get. Land this commit first:

```diff
# kubernetes/components/kopiur/ceph/restore.yaml          (DR MODE - revert in phase 6)
   repository:
     kind: ClusterRepository
-    name: ceph
+    name: r2
   ...
     fromPolicy:
-      name: "${APP}-ceph"
+      name: "${APP}-r2"
       offset: 0
# onMissingSnapshot stays Fail.

# kubernetes/apps/base/system/kopiur/repository/clusterrepository.yaml   (r2 block, DR MODE)
-  mode: ReadWrite
+  mode: ReadOnly    # restores only: no backups, no maintenance on the last good copy

# kubernetes/components/kopiur/r2/snapshotpolicy.yaml     (DR MODE)
 spec:
+  suspend: true     # no r2 backups, no adoption prune, no retention until verified
```

A `Restore` must name both `r2` and `<app>-r2`: kopiur rejects a `repository` that is not in the
named policy's repository set. `fromPolicy` reads the policy's spec, not its status, so a
suspended r2 policy still resolves (source-read at kopiur 0.10.10; not yet drilled).

**Postgres, in the same commit (explicit step).** `database/postgres-17`
(`kubernetes/apps/base/database/cloudnative-pg/cluster-17/cluster-17.yaml`) uses the
`previousCluster` / `currentCluster` pattern: the `&previousCluster` anchor is
`bootstrap.recovery.source` and the recovery external cluster's `serverName`, the
`&currentCluster` anchor is `backup.barmanObjectStore.serverName`. Git still reads
`previousCluster: postgres17-v4` while the live cluster has archived to `postgres17-v5` since
2026-04-26. How Git should carry this permanently is still an open decision, so the DR commit
must bump **both** anchors at rebuild time:

```diff
# kubernetes/apps/base/database/cloudnative-pg/cluster-17/cluster-17.yaml   (DR commit)
-      source: &previousCluster postgres17-v4
+      source: &previousCluster postgres17-v5    # the archive the old cluster was writing
...
-      serverName: &currentCluster postgres17-v5
+      serverName: &currentCluster postgres17-v6  # a fresh, empty path
```

Why both: CNPG reads `spec.bootstrap` only when a `Cluster` is first created, so the rebuild is
the one moment `previousCluster` matters, and it must name the live archive or Postgres recovers
to roughly its 2026-04-26 state (or fails). A recovering cluster also refuses to archive into a
non-empty path, so `currentCluster` must move to an unused one. Bump only `previousCluster` and
the new cluster fails its WAL archive check with `WAL archive check failed ... Expected empty
archive`, because v5 already holds the old cluster's WALs. If `currentCluster` has moved past v5
by the time of the loss, apply the same rule to what the old cluster was actually running:
`previousCluster` = its last `currentCluster`, `currentCluster` = the next unused version. Confirm on the MinIO
bucket `home-ops-postgres-cluster` that the source path holds recent WALs. Do not work around
the archive check with an annotation; bump the path.

If the MinIO NAS is lost too, Postgres has no off-site copy (the offsite mirror is suspended,
`kubernetes/apps/base/database/cloudnative-pg/offsite-mirror/README.md`) and is not recoverable.

## Phase 2 - Rebuild

1. Follow `bootstrap/AGENTS.md`: `just bootstrap cluster`. Flux then
   reconciles `cluster-apps`.
2. Wait for Rook `HEALTH_OK`, then the `kopiur` and `kopiur-repository` Kustomizations.
3. **Gate:** `kubectl get clusterrepository r2 -o jsonpath='{.status.phase} {.status.uniqueId}'`
   must print `Ready` and the pre-disaster uniqueId:
   `caccc0cb2ab28d2d60e684eefc22ba4143ba3888ca78cb7d7f724460e6af6ae4` (read live 2026-09-28).
   A `RepositoryNotInitialized` means the endpoint, bucket or credential is wrong; fix the
   1Password item, never set `create.enabled: true`. A different uniqueId means it connected
   to the wrong repository: stop. While r2 is not `Ready`, the populator Restores wait in
   `Pending` with `RepositoryNotReady` rather than going `Failed`, and that time does not count
   against their wait window: stuck-`Pending` Restores plus `KopiurRepositoryNotReady` mean fix
   the repository, not the claims.
4. `ceph` goes `Ready` with a **new** uniqueId. That is expected.

## Phase 3 - The 26 populator claims restore from r2

`ai/{hermes,opencode,repo-wiki}`, `database/pgadmin`,
`downloads/{bazarr,lidarr,prowlarr,radarr,readarr,recyclarr,sabnzbd,sonarr}`,
`home-automation/{esphome,home-assistant,matter-server,zigbee2mqtt}`,
`media/{calibre-web-automated,plex,seerr,tdarr}`,
`selfhosted/{changedetection,linkwarden,n8n,ntfy,obsidian-livesync,syncthing}`.

1. Watch them:

   ```bash
   kubectl get restore -A -o 'custom-columns=NS:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase,REASON:.status.conditions[?(@.type=="Resolved")].reason,SNAP:.status.resolved.kopiaSnapshotID'
   ```

2. **Pass:** every `*-kopiur-dst` claimed by a PVC is `Completed` with reason `RestoreSucceeded`.
   A `Failed` one names the claim and cause in `status.claims`. Any `NoSnapshotContinue` means
   a populator was not fail-closed: treat that claim as empty and hand-restore it (phase 4).
3. Check each resolved snapshot against phase 0: `status.resolved.kopiaSnapshotID` and
   `pinnedAt` must be the snapshot expected at or before `T_LOSS`. Take evidence from
   `Restore.status` (`resolved`, `status.claims.<pvc>.logTail`), not mover logs: kopiur deletes a
   successful mover Job within about 20 s, and the mover reports no file or byte counts.
4. Time: `media/plex` restored 4.6 GiB from r2 in 7m36s (about 10 MiB/s). Restores are not
   concurrency-capped, so `ai/hermes` (~19.6 GiB) bounds the phase at roughly 30-40 min.
5. If a claim failed: keep its PVC unbound, fix the cause (usually cache capacity or mover
   identity, see the `kopiur-backups` skill), then delete the `Restore` **and** the Pending PVC.
   Both carry `ssa: IfNotPresent`, so `flux reconcile ks <app> -n <ns>` recreates them from the
   DR-mode Git state, and the new PVC starts the claim over. A failed `Restore` never retries.
   A failed claim also leaves up to 3 ephemeral cache PVCs (one per mover attempt, at the claim's
   cache size) until the Job TTL expires after 1 h.
6. **Once every claim passes steps 2-3, delete the completed populator Restores**, then
   `flux reconcile ks <app> -n <ns>` so they come back unclaimed:

   ```bash
   kubectl get restore -A --no-headers | awk '$2 ~ /-kopiur-dst$/ {print $1, $2}' |
     while read ns name; do kubectl -n "$ns" delete restore "$name"; done
   ```

   kopiur 0.10.10 never reaps a Restore's credential copy (`<restore>-restore-creds-N`) while
   the Restore exists, so the r2 keys would otherwise sit in 6 app namespaces and the critical
   `KopiurProjectedCredentialsLeaking` page (Pushover priority 2) fires about 13 h later. This is
   safe: the claims are bound (`TargetAlreadyBound`), and the Restores carry no finalizers and own
   no data.

## Phase 4 - Hand restores for the 5 claims without a working populator

These apps start on empty volumes after a rebuild and nothing restores them:

| Claim | Why | r2 policy | Mover uid:gid | Cache |
|---|---|---|---|---|
| `database/falkordb` | chart-owned PVC, no populator | `falkordb-r2` | 1000:1000 | 10Gi |
| `database/surrealdb` | chart-owned PVC, no populator | `surrealdb-r2` | 65532:65532 | 2Gi |
| `selfhosted/paperless-ngx` | VolSync populator reads an empty rebuilt ceph restic repo | `paperless-ngx-r2` | 1000:1000 | 2Gi |
| `selfhosted/paperless-ngx-media` | chart-owned PVC, no populator | `paperless-ngx-media-r2` | 1000:1000 | 5Gi |
| `selfhosted/syncthing-data` | plain PVC, no populator | `syncthing-data-r2` | 1000:1000 | 5Gi |

Identity and cache are the live r2 policy values (2026-09-28); re-read them from the overlay
(`KOPIUR_PUID`/`KOPIUR_PGID`/`KOPIUR_CACHE_CAPACITY`) if they may have changed. For each claim:

1. `flux suspend ks <app> -n <ns>` and `flux suspend hr <app> -n <ns>`, scale the workload to 0
   and wait until no pod mounts the claim.
2. Apply an in-place restore:

   ```yaml
   apiVersion: kopiur.home-operations.com/v1alpha1
   kind: Restore
   metadata:
     name: <claim>-dr-<yyyymmdd>
     namespace: <ns>
   spec:
     repository: {kind: ClusterRepository, name: r2}
     credentialProjection: {enabled: true}   # required on every hand-written Restore
     source:
       fromPolicy:
         name: <claim>-r2
         asOf: "<T_LOSS>"                     # never offset: 0 once apps have run
     target:
       pvcRef: {name: <claim>}
     options:
       enableFileDeletion: true               # exact mirror: drop what the fresh app wrote
     policy:
       onMissingSnapshot: Fail
     mover:
       podSecurityContext: {runAsUser: <uid>, runAsGroup: <gid>, fsGroup: <gid>}
       cache: {mode: Ephemeral, capacity: <cache>}
   ```

3. Wait for `Completed` and check `.status.resolved.kopiaSnapshotID`. Delete the Restore right
   away (same credential-copy reason as phase 3 step 6), then resume the HelmRelease and
   Kustomization and let the workload scale back up.

Do **not** use `just kube restore`: it is VolSync-only, clones `${APP}-dst`, and reads `ceph`.

## Phase 5 - Verify

1. Each of the 31 claims: the pinned snapshot ID from phase 3/4 is the one expected from phase 0,
   and the app works: log in, data present. A file-count check needs a read-only reader pod on
   the claim, compared with the snapshot's `Snapshot.status.stats` (kopia skips `CACHEDIR.TAG`
   directories, so fewer files than the old live volume can be correct).
2. Postgres: `kubectl -n database get cluster postgres-17` healthy, recovered to at or near
   `T_LOSS`, `ContinuousArchiving=True` against the new `serverName`.

## Phase 6 - Exit DR mode

1. Only after phase 5 passes: revert the phase 1 kopiur lines. r2 returns to `ReadWrite` under
   the same maintenance owner, and the r2 policies unsuspend (adoption, retention and daily
   backups resume). Keep the Postgres change: it now describes the live cluster.
2. Wait for one successful **ceph** backup per claim (`KopiurBackupStaleCeph` quiet).
3. The standing Restores are still r2-pointed (`IfNotPresent`). Delete every `*-kopiur-dst` and
   `flux reconcile ks` each app so they come back ceph-pointed. Deleting a `Restore` whose claim
   is bound does not touch the volume; they carry no finalizers and own no data
   ([restore-and-cache.md](restore-and-cache.md)).
4. Check adoption: each r2 policy's `status.adoption` covers its pre-disaster history.
5. Record the event, and the next rebuild's Postgres `serverName`, in the PR that reverts DR mode.

## Recovering from a silent empty rebuild

If a cluster was rebuilt while the populator still used `Continue` (or any claim shows
`NoSnapshotContinue`), its apps are running on empty volumes and r2 is at risk:

1. **Immediately** set the `r2` `ClusterRepository` to `mode: ReadOnly`, or suspend every r2
   `SnapshotPolicy`. This stops further blank snapshots and stops retention pruning. Once one
   blank r2 snapshot exists, `offset: 0` resolves to it.
2. Hand-restore **every** backed-up claim with the phase 4 manifest, always with
   `asOf: <T_LOSS>`, never `offset: 0`.
3. Then continue at phase 5.
