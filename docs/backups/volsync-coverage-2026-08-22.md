# VolSync coverage audit - 2026-08-22

> **Historical snapshot.** Every number below was measured live against the cluster on
> 2026-08-22 at repo HEAD `d4399e37`. The authoritative description of the backup
> component and its multi-volume pattern lives in
> [`kubernetes/components/volsync/Readme.md`](../../kubernetes/components/volsync/Readme.md);
> re-measure before relying on any figure here.

## 1. What was wrong

`kubernetes/components/volsync/` names every resource from `${APP}` and backs up exactly
one PVC, `${VOLSYNC_CLAIM:-${APP}}`. Flux allows one `postBuild.substitute` map per
Kustomization, so an app cannot include the component twice. Any app with more than one
volume therefore had its extra volumes silently unprotected, and every StorageClass in
the cluster is `reclaimPolicy: Delete`, so a deleted PVC takes the data with it.

## 2. Complete unbacked-volume inventory

75 PVCs exist cluster-wide once VolSync's own cache/temp PVCs are excluded. **34 were
backed, 41 were not.** Usage is `rbd du` allocated bytes (RBD volumes) or in-pod `df`
(CephFS / hostpath), not provisioned capacity.

### 2.1 Covered by this change (4)

The multi-claim defect, on volumes holding non-reconstructible state:

| Volume | Provisioned | Actually used | Why it matters |
|---|---|---|---|
| `selfhosted/syncthing-data` | 100Gi | 28 KiB (empty) | the synced-file target |
| `selfhosted/paperless-ngx-media` | 50Gi | 32 KiB (empty) | scanned-document originals + archived PDFs |
| `ai/perplexica-dbstore` | 1Gi | 64 KiB | `db.sqlite` - chat history and settings |
| `ai/perplexica-uploads` | 5Gi | 20 KiB | user-uploaded documents |

### 2.2 Deliberately not covered (37)

| Volume(s) | Prov. | Used | Why not |
|---|---|---|---|
| `monitoring/*` (7) | 170Gi | ~132Gi | **out of scope** - separate decision, see the cleanliness scout report |
| `database/postgres-17-{1,5,6}` | 300Gi | 62Gi | already protected by CloudNativePG `barmanObjectStore`; `ScheduledBackup postgres-17` completed 10 min before this audit, 6 consecutive daily backups `completed` |
| `downloads/sabnzbd-incomplete` | 1500Gi | 939Gi | in-flight downloads; transient by definition |
| `downloads/shared-downloads` | 2Ti | (CephFS) | completed media, re-acquirable |
| `media/tdarr-temp` | 200Gi | 0 | transcode scratch |
| `ai/comfyui-models` | 100Gi | 0.26Gi | model cache, re-downloadable; the `comfyui` Deployment is scaled to 0 |
| `ai/vllm-models` | 50Gi | 46.7Gi | model cache, re-downloadable from HuggingFace |
| `media/jellyfin-metadata` | 50Gi | 17.3Gi | artwork/metadata cache Jellyfin regenerates from the library. **The one judgement call here** - regenerating 17Gi is hours of API calls, so it is defensible to cover it later; it is a cache, not data. |
| `rook-ceph/ceph-backup-pvc`, `rook-ceph-mon-{h,i,m}` | 80Gi | hostpath | Ceph's own mon store / config backup; backing Ceph up onto Ceph is circular |
| `selfhosted/paperless-ngx-consume` | 10Gi | 20 KiB | watched inbox - paperless **deletes** files from it once ingested into `media`, so it only ever holds transient state |
| `ai/vllm-embed-models` | 20Gi | 4.1Gi | model cache |
| `ai/comfyui-{custom-nodes,input,output,user}` | 42Gi | 0.16Gi | re-installable / scratch; app is scaled to 0 |
| `database/nats-js-nats-{0,1,2}` | 30Gi | 0.74Gi | JetStream, 3-way replicated across the same StatefulSet |
| `database/emqx-core-data-*` (4) | 20Gi | 1.0Gi | MQTT session state, regenerated on reconnect. **2 of the 4 are orphans** from an old ReplicaSet (`597bbb9c7c`) with no pod - worth a separate cleanup. |
| `database/surrealdb` | 20Gi | 0.44Gi | **flagged, not fixed** - a live database with no backup and no CNPG-style equivalent. Not part of the multi-claim defect and not in this task's scope; recommend covering it. |
| `flux-system/headlamp` | 1Gi | 0.12Gi | plugin dir, re-installed by the chart |
| `actions-runner-system/*-work` (2) | 40Gi | 0.18Gi | ephemeral runner workdirs |

### 2.3 Correction to the premise

The task was framed as "two volumes holding real data have no backup". **Both volumes are
empty.** `syncthing-data` holds 28 KiB (a `lost+found` directory and nothing else) and
`paperless-ngx-media` holds 32 KiB (an empty `documents/` and a zero-byte `media.lock`);
`select count(*) from documents_document` in the `paperless` database returns **0**. The
"160 GiB unbacked" figure in the cleanliness scout report is *provisioned* capacity, not
stored data.

That makes the exposure a future one rather than a live one - and makes now the cheapest
possible moment to close it, since the first document scanned into paperless is protected
from its first backup window onward instead of after the next audit.

It also means VolSync will log `== Directory is empty skipping backup ===` and create **no
snapshot** for `syncthing-data`, `paperless-ngx-media` and `perplexica-uploads` until real
data arrives. That is correct mover behaviour, not a failure - `selfhosted/rsshub` and
`rsshub-playwright` already sit in exactly that state today (`Successful`, zero snapshot
objects in their restic repos). Only `perplexica-dbstore` has content, so it is the only
one of the four that will produce a snapshot immediately.

### 2.4 Separate finding - perplexica's existing coverage points at the wrong volume

`ai/perplexica` sets no `VOLSYNC_CLAIM`, so the component defaults to `${APP}` and creates
and backs up a PVC named `perplexica`. The HelmRelease never mounts it - it mounts
`perplexica-dbstore` and `perplexica-uploads`. So perplexica has had three
ReplicationSources faithfully backing up an empty, unmounted volume (`rbd du`: 0.00Gi)
while its real data was unprotected. The two Kustomizations added here cover the real
volumes; **the redundant `perplexica` source and its unused PVC are left alone** because
removing them means pruning a live PVC, which is the captain's call.

### 2.5 Separate finding - orphaned restic repositories

Roughly 41% of the local Ceph backup bucket belongs to apps that no longer exist:

| Repo | Bytes | Live app? |
|---|---|---|
| `my-claw` | 17.6 GB | no |
| `openclaw` | 4.0 GB | no |
| `homeops-claw` | 0.41 GB | no |
| `qbittorrent` | 0.21 GB | no |
| `cross-seed` | 8.0 MB | no |
| `open-notebook` | 4.1 MB | no |

~22.2 GB of 53.9 GB. Not touched here; reclaiming it is a deletion and needs a decision.

## 3. How coverage was extended

Three shapes were possible:

1. **A second instance of the component in the same Kustomization** - impossible. Flux
   permits one `postBuild.substitute` map per Kustomization and every object name derives
   from `${APP}`.
2. **A parameterised claim list inside the component** (`VOLSYNC_CLAIM_2`, ...) - rejected.
   Kustomize has no conditionals, so an unset second claim would still emit a broken
   ReplicationSource for all 34 apps already using the component.
3. **A dedicated Flux Kustomization per extra volume** - chosen.

The component was split so that the reusable half exists on its own:

```
kubernetes/components/volsync/
├── backup/            # NEW - ceph + minio + r2, no PVC. Also a valid Flux `path`.
├── pvc.yaml
└── kustomization.yaml # Component: ./backup + ./pvc.yaml
```

`backup/` is now the single definition of "back this PVC up to all three destinations".
The parent Component layers `pvc.yaml` on top for the normal single-claim case, and
`kustomize build` output for the 34 existing apps is **byte-identical to HEAD** (verified
by diffing a build of the component before and after).

An extra volume is then ~20 lines appended to the app's own `ks.yaml`, with `APP` set to
the claim name so it gets its own restic repository, ExternalSecrets and
ReplicationDestination:

```yaml
path: "./kubernetes/components/volsync/backup"
postBuild:
  substitute:
    APP: &app paperless-ngx-media
    VOLSYNC_CLAIM: *app
```

Multi-document `ks.yaml` is already an established pattern here (14 apps use it), so this
introduces no new convention. All three targets (`ceph`, `minio`, `r2`) are used, matching
existing coverage.

## 4. Capacity

Measured before committing to schedules and retention.

| Target | Currently stored | Headroom | Verdict |
|---|---|---|---|
| **Local Ceph S3** (`volsync` bucket, RGW) | 53.9 GB across 11,092 objects, **no bucket quota** | pool `ceph-objectstore.rgw.buckets.data` `MAX AVAIL` **3.6 TiB**; raw 17 TiB with 12 TiB free at 30.31% used | safe |
| **Cloudflare R2** | `paperless-ngx` repo alone: 1.577 GiB raw over 43 snapshots | usage-billed, no fixed ceiling | safe; the constraint is cost, not capacity |
| **NAS MinIO** | `paperless-ngx` repo: 5.472 MiB raw over 24 snapshots (13.93x compression) | **not measurable** - `https://nas.sklab.dev:9000/minio/v2/metrics/cluster` returns HTTP 403 and the VolSync credential is not a MinIO admin key | see below |

**What is actually being added today: ~144 KiB.** All four volumes are empty or near-empty,
so the first backups add essentially nothing.

**Worst case, if all four volumes fill to their provisioned size:** 156 GiB of source data.
On Ceph that is ~156 GiB stored / ~468 GiB raw at 3x replication - about 4% of the 3.6 TiB
usable headroom, and 2.7% of raw. Safe with a very wide margin.

**The MinIO gap is a real one and is stated rather than papered over.** NAS free space could
not be read without admin credentials that are deliberately not in the cluster. The bound is
the same +156 GiB worst case; if the NAS has less than ~200 GiB free, the `minio`
ReplicationSources for `syncthing-data` and `paperless-ngx-media` should be reconsidered
before those volumes are filled. **Recommend checking NAS free space manually before
syncthing is pointed at a large dataset.** No schedule or retention was tightened on the
strength of an unmeasured number.

Retention is the component default (ceph 6h/14d/10w/6m, minio 14d/8w/6m, r2 30d/12w/12m).
Schedule minutes were picked to avoid collisions with every existing ReplicationSource in
the same namespace, so no two movers snapshot the same Ceph pool in the same minute.

Cache sizing: 5Gi for `syncthing-data` and `paperless-ngx-media` (restic cache scales with
file count, not volume size, and both start empty), component default 2Gi for the two
perplexica claims.

## 5. Restore evidence

**A restore was performed and verified, with zero writes to the cluster and zero writes to
the backup repositories.**

The new ReplicationSources ship through Git and cannot exist until Flux applies them, and
three of the four source volumes are empty, so a restore of the *new* repos is not yet
possible. What is provable now is the exact machinery the new sources reuse: `backup/`
generates the same ReplicationSource/ReplicationDestination YAML with only `${APP}`
differing, against the same S3 endpoints, secrets and restic repositories. So the restore
was proved against the parent claims of two of the affected apps.

Method: `kubectl port-forward` to the RGW Service (creates no Kubernetes objects) plus a
local restic 0.18.0 using read-only subcommands with `--no-lock`, so not even a transient
lock object was written to the repositories.

### `selfhosted/paperless-ngx` (local Ceph repo)

```
$ restic snapshots --no-lock
4eaaaf10  2026-08-22 00:01:38  volsync  /data  20.852 MiB
30 snapshots

$ restic restore latest --no-lock --target <scratch>
restoring snapshot 4eaaaf10 of [/data] at 2026-08-22 00:01:38 by @volsync
Summary: Restored 34 files/dirs (20.852 MiB) in 0:00
```

32 files across 3 directories restored. Checksummed against the live PVC:

| File | Live sha256 | Restored | |
|---|---|---|---|
| `.index_version` | `2e6d31a5…` | `2e6d31a5…` | match |
| `index/.index_settings.json` | `4a5cadf3…` | `4a5cadf3…` | match |
| `index/.managed.json` | `0a4c0352…` | `0a4c0352…` | match |
| `index/meta.json` | `6f9d51a0…` | `6f9d51a0…` | match |
| `log/celery.log.20` | `6b5564e6…` | `6b5564e6…` | match |
| `log/paperless.log` | `6f27ab11…` | `9d9209ee…` | **differs - as it should** |

`paperless.log` is appended continuously, so a snapshot from five hours earlier must differ.
It was checked the falsifiable way instead: the restored file is 448,294 bytes, the live file
448,387. `sha256(head -c 448294 <live file>)` = `9d9209ee…` = the restored file's checksum
exactly. The restored log is the live log as of the snapshot instant, byte for byte, and has
only grown since. That one file differing is what shows the other five matches are real
rather than trivially equal.

### `selfhosted/syncthing` (local Ceph repo)

```
$ restic restore latest --no-lock --target <scratch>
restoring snapshot b2a677df of [/data] at 2026-08-22 00:10:44 by @volsync
Summary: Restored 14 files/dirs (2.870 MiB) in 0:00
```

12 files restored; `config.xml`, `cert.pem`, `key.pem` and `https-cert.pem` all
byte-identical to the live PVC.

### Repository integrity

```
$ restic check --no-lock          # paperless-ngx
check snapshots, trees and blobs
[0:00] 100.00%  30 / 30 snapshots
no errors were found

$ restic check --no-lock          # syncthing
[0:00] 100.00%  30 / 30 snapshots
no errors were found
```

### All three destinations confirmed live

`paperless-ngx` reachable and decryptable on all three, latest snapshot on each:

| Target | Snapshots | Latest | Raw repo size |
|---|---|---|---|
| Ceph | 30 | 2026-08-22 00:01 | (bucket total 53.9 GB) |
| MinIO | 24 | 2026-08-21 18:31 | 5.472 MiB (13.93x compression) |
| R2 | 43 | 2026-08-21 02:01 | 1.577 GiB |

### Cleanup

The scratch restore directories and the extracted credentials were deleted and the
port-forward stopped. No Kubernetes object was created at any point: `kubectl get
replicationdestination -A` and `kubectl get pvc -A` show no scratch resources, because none
was ever created.

### What is still unproven

A restore of `syncthing-data`, `paperless-ngx-media`, `perplexica-dbstore` and
`perplexica-uploads` from **their own** repositories. Those repositories do not exist until
this change merges and Flux applies it, and three of the four volumes are empty so they will
not produce a snapshot at all until real data lands. **After merge, `perplexica-dbstore` is
the one to check first** - it has real content and should show a snapshot within four hours
of its first `50 */4 * * *` window.

## 6. Recommended follow-ups (not done here)

1. Check NAS MinIO free space before syncthing is pointed at a large dataset (§4).
2. Cover `database/surrealdb` - a live database with no backup at all (§2.2).
3. Decide on `ai/perplexica`'s redundant source and unused PVC (§2.4).
4. Reclaim ~22 GB of orphaned restic repositories (§2.5).
5. Clean up the 2 orphaned `emqx-core-data-emqx-core-597bbb9c7c-*` PVCs (§2.2).
