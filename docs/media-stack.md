# Media Stack

Architecture and operational reference for the downloads, management, and media server pipeline. Covers Usenet acquisition, automated library management, quality control, subtitle management, and GPU transcoding.

---

## Overview

The media stack is split across two Kubernetes namespaces (`default` is empty):

| Namespace | Purpose | Key Apps |
|-----------|---------|----------|
| `downloads` | Acquisition + library management | SABnzbd, Sonarr, Radarr, Lidarr, Readarr, Prowlarr, Bazarr, Recyclarr, Autobrr, reading-glasses |
| `media` | Media servers + post-processing | Jellyfin, Plex, Seerr, Immich, Tdarr, Calibre-Web-Automated, Calibre-Downloader |

`default/kustomization.yaml` has `resources: []`. CWA + Calibre-Downloader live under `media/calibre/` and Flux-target `media`.

`media/calibre-web/` was removed; CWA is the only Calibre app under `media/calibre/`.

FlareSolverr (Cloudflare bypass proxy) lives in the `network` namespace (`kubernetes/apps/base/network/flaresolverr/`), not `downloads`/`media` - consumed by Prowlarr (IndexerProxy, UI-configured, not GitOps) and Calibre-Downloader (`EXT_BYPASSER_URL`).

In `downloads`, **autobrr is live**. `cross-seed` and `qbittorrent` were removed (dead, unreferenced directories) and are no longer present in `kubernetes/apps/main/downloads/kustomization.yaml`.

Flow: **Indexers** -> Prowlarr -> *arr apps -> SABnzbd -> imports to NAS -> Jellyfin/Plex serve -> Tdarr transcodes in place to AV1.

---

## Storage Architecture

Storage is split between CephFS (complete downloads), Ceph block (SAB incomplete scratch), and NFS (permanent media):

| Volume | Backing | Access | Size | Purpose |
|--------|---------|--------|------|---------|
| `shared-downloads` | `ceph-filesystem-rwx` (RWX) | RWX | 2 Ti | `usenet/complete/` and other download scratch |
| `sabnzbd-incomplete` | `ceph-block` (RWO) | RWO | 1500 Gi | SAB article-assembly (`download_dir`); bind-mounted **over** `/data/downloads/usenet/incomplete` |
| `{app}-config` | Ceph block (RWO) | RWO | 1-10 Gi | Per-app config PVCs with Volsync backup |
| NFS `/mnt/storage/Media` | NAS (NFS) | RWX | ~40+ TiB | Permanent media library |
| `immich-library` | CephFS (RWX) | RWX | 100 Gi | Photos |
| `jellyfin-metadata` | Ceph block | RWO | 50 Gi | Jellyfin thumbnails/metadata |

Manifests: `kubernetes/apps/base/downloads/pvc/app/shared-downloads.yaml` and `sabnzbd-incomplete.yaml`. There is no `shared-downloads-pvc.yaml`.

Incomplete is **not** on the shared PVC. Since PR #983 it is a separate RBD volume overlaying that path in the sab pod. `du` inside sab will miss a ghost tree on CephFS under the mount. Full incident notes: [`downloads/sabnzbd-disk-space-runbook.md`](downloads/sabnzbd-disk-space-runbook.md). Do not duplicate incomplete-path settings here.

### Hardlink limitation

Downloads live on CephFS, media lives on NFS -- **hardlinks are impossible across filesystems**. Imports from Sonarr/Radarr copy rather than hardlink, which uses temporary double disk space during import. This is an accepted tradeoff for NAS-backed media.

### Mount conventions

| Container path | Source |
|----------------|--------|
| `/data/downloads` | `shared-downloads` (all download + *arr apps) |
| `/data/downloads/usenet/incomplete` | `sabnzbd-incomplete` overlay, **sab pod only** |
| `/data/nas-media` | NFS mount `nas.${SECRET_DOMAIN}:/mnt/storage/Media` (read-write for *arr imports; read-only on Jellyfin) |
| `/media` | Same NFS mount, alternative path used by Tdarr and Jellyfin |

---

## Application Reference

### SABnzbd - Usenet download client

**Namespace:** `downloads` | **Image:** `ghcr.io/home-operations/sabnzbd` | **Hostname:** `sabnzbd.${SECRET_DOMAIN}`

Categories map to the *arr apps. The init container also creates audiobooks, comics, magazines, and other:

| Category | Folder | Consumed by |
|----------|--------|-------------|
| `movies` | `movies` | Radarr |
| `tv` | `tv` | Sonarr |
| `music` | `music` | Lidarr |
| `books` | `books` | Readarr |

**Critical UI settings (not in GitOps):**
- Article Cache: **1 GB**
- Direct Unpack: **ON** (`direct_unpack=1`). Live-confirmed 2026-08-21 via SAB API; set 2026-06-24 with `complete_free=100G` / `download_free=100G`. TRaSH still recommends OFF; this cluster keeps ON as runtime state on the config PVC. See the [disk-space runbook](downloads/sabnzbd-disk-space-runbook.md).
- Disable **ALL Sorting** (the *arr apps handle renaming)
- Abort jobs that cannot be completed: **ON**
- Action on encrypted RAR: **Abort**
- Incomplete: `/data/downloads/usenet/incomplete` (RBD overlay)
- Complete: `/data/downloads/usenet/complete` (`shared-downloads`)

The init container pre-creates the category subdirectories on the shared PVC.

### Prowlarr - Indexer manager

Central proxy for all indexers. All *arr apps should be registered in `Settings > Apps` and pull indexer config from Prowlarr. Do **not** add indexers directly to Sonarr/Radarr/Lidarr.

Service DNS registered in Prowlarr:
- `http://sonarr.downloads.svc.cluster.local:8989`
- `http://radarr.downloads.svc.cluster.local:7878`
- `http://lidarr.downloads.svc.cluster.local:8080`
- `http://readarr.downloads.svc.cluster.local:8787`

### Sonarr / Radarr / Lidarr / Readarr - Library managers

All four follow the same pattern. Quality profiles and custom formats are managed declaratively by **Recyclarr** (see below).

**Naming schemes (TRaSH recommended):**

Sonarr standard episode:
```
{Series TitleYear} - S{season:00}E{episode:00} - {Episode CleanTitle} [{Custom Formats}]{[Quality Full]}{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}
```

Radarr standard movie:
```
{Movie CleanTitle} {(Release Year)} {imdb-{ImdbId}} {edition-{Edition Tags}} [{Custom Formats}]{[Quality Full]}{[MediaInfo AudioCodec}{ MediaInfo AudioChannels]}{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}
```

Lidarr track format:
```
{Album Title} ({Release Year})/{Artist Name} - {Album Title} - {track:00} - {Track Title}
```

**Root folders:**
- Sonarr: `/data/nas-media/TV-Shows`
- Radarr: `/data/nas-media/Movies`
- Lidarr: `/data/nas-media/Music`
- Readarr: `/data/nas-media/Books`

**Common settings:**
- Analyze video files: **ON** (required for MediaInfo tokens in filenames)
- Propers and Repacks: **Do Not Prefer** (custom formats handle scoring)
- Download clients point to `sabnzbd.downloads.svc.cluster.local:8080` with the matching category

### Recyclarr - Declarative quality config

**File:** `kubernetes/apps/base/downloads/recyclarr/app/config/recyclarr.yml`
**Schedule:** Daily CronJob (`@daily`)

Syncs TRaSH Guide quality profiles, quality definitions, and custom formats to Sonarr and Radarr. Changes to `recyclarr.yml` require a commit+push, then either wait for the next daily run or trigger manually:

```sh
kubectl -n downloads create job --from=cronjob/recyclarr recyclarr-manual-$(date +%s)
```

**Configured profiles:**

| App | Profile | Target |
|-----|---------|--------|
| Sonarr | WEB-1080p | 720p/1080p WEB content |
| Sonarr | WEB-2160p | 2160p WEB + HDR |
| Radarr | SQP-1 (2160p) | Streaming quality 2160p with `min_format_score: 2000` |

**Custom format categories applied:**
- **Unwanted (Sonarr)**: AV1, BR-DISK, LQ, x265 (HD), Bad Dual Groups, No-RlsGroup, Obfuscated, Retags, Scene
- **Unwanted (Radarr)**: the Sonarr set plus **3D** (Radarr-only; do not toggle 3D on Sonarr from this table)
- **Repacks**: Repack/Proper, Repack2, Repack3
- **Streaming services** (Sonarr): AMZN, ATVP, DCU, DSNP, HBO, HMAX, HULU, iT, MAX, NF, PCOK, PMTP, SHO, STAN
- **Movie versions** (Radarr): Criterion Collection, Hybrid, Remaster, IMAX, IMAX Enhanced
- **HDR** (WEB-2160p): DV (WEBDL)
- **Trusted groups** (Radarr): hallowed
- **Language/naming risk** (Radarr, SQP-1, score -25 each): Language: Not English, MULTi -- deprioritizes (does not block) releases whose filename is more likely to defeat Radarr's import parser and get stuck `importBlocked` in the queue; see "Finding which *arr app is failing imports" below

### Bazarr - Subtitle management

**TRaSH-recommended scoring:**

| Setting | Value |
|---------|-------|
| Series minimum score | 90 |
| Movies minimum score | 80 |

Connects to Sonarr and Radarr for series/movie metadata. Provider priority: OpenSubtitles.com > Podnapisi > Supersubtitles > Addic7ed.

### Autobrr

Autobrr is deployed (`kubernetes/apps/main/downloads/kustomization.yaml`). Cross-Seed and qBittorrent were removed; Autobrr's torrent-side integrations are unused until those are (re-)added.

---

## Tdarr - GPU transcoding

**Namespace:** `media` | **Hostname:** `tdarr.${SECRET_DOMAIN}`

Not a DaemonSet. After the i915 -> xe migration, only talos-3 advertises GPU
(`gpu.intel.com/xe: 99` on the B70; talos-1/2 report 0). See
[`ai-gpu-changelog.md`](ai-gpu-changelog.md).

| Controller | Type | Purpose | GPU |
|-----------|------|---------|-----|
| `tdarr` | Deployment (1 pod) | Web UI, orchestrator, database | No |
| `tdarr-node` | Deployment (`replicas: 1`) | Transcode worker on the xe node (talos-3 / B70) | `gpu.intel.com/xe: 1` |

Optional external Windows node via Service `tdarr-node-lb` (`10.50.0.54:8266`,
`tdarr/app/externalservice.yaml`). Live UI (2026-08-21): k8s node `talos-3`
(`transcodegpuWorkers=1`) plus `desktop-aviator` (`transcodegpuWorkers=2`).
Stale registrations for talos-1/2 may still appear with GPU workers at 0.

The server has `internalNode=false` so it doesn't compete for GPU. Expect
1 `tdarr-*` server pod + 1 `tdarr-tdarr-node-*` worker on talos-3, not three
workers.

### Worker environment

The k8s worker pod gets:
```
serverIP=tdarr.media.svc.cluster.local
serverPort=8266
nodeType=mapped            # All workers see /media at the same path
transcodegpuWorkers=1      # One QSV transcode at a time
transcodecpuWorkers=0      # Force GPU usage
healthcheckgpuWorkers=1
healthcheckcpuWorkers=1
nodeName=<k8s pod nodeName> # Registers with pod's scheduling node
ffmpegVersion=7
```

### Transcode flow (configured in Tdarr UI, not Git)

Classic Boosh HEVC plugin stack is **not** in use (`pluginIDs` empty). Both
libraries use Tdarr Flow `movies_av1_nvenc_v1`, named
**Movies AV1 (QSV/B70 xe) - DV-safe v4**.

Libraries (2026-08-21):

| Library | Folder | Flow |
|---------|--------|------|
| Series | `/media/TV-Shows` | `movies_av1_nvenc_v1` |
| Movies AV1 | `/media/Movies` | `movies_av1_nvenc_v1` |

Flow encoder plugins (Community `ffmpegCommandSetVideoEncoder`):

| Setting | Value |
|---------|-------|
| `outputCodec` | `av1` |
| `hardwareType` | `qsv` |
| `hardwareEncoding` / `hardwareDecoding` | `true` |
| container | `mkv` |
| quality ladders | cq24 / cq26 / cq28 (`ffmpegQuality` 22/23/24 with quality flag off; CQ via custom args) |

The flow skips files that are already AV1 and has DV-detect / HDR-survival
guards. The flow **id** still says `nvenc`; the live plugins are QSV on the B70.

### Library file type filter

Tdarr cannot process disc images (`.iso`). Live `containerFilter` on both
libraries:
```
mkv,mp4,avi,ts,mov,m4v,wmv,flv,webm
```

### ISO file policy

BR-DISK and ISO files are blocked going forward:
- Recyclarr adds `BR-DISK` custom format with negative score
- `min_format_score: 2000` on SQP-1 rejects anything with BR-DISK penalties
- Existing ISO files should be deleted and re-downloaded via Radarr's "Cutoff Unmet" view

---

## Jellyfin - Media server

**Namespace:** `media` | **LoadBalancer IP:** `10.50.0.50` | **GPU:** `gpu.intel.com/xe: 1`

Single-pod deployment reading the NFS media mount read-only at `/data/nas-media`. Intel GPU handles hardware transcoding for any clients that can't direct play (rare with the SQP-1 profile since streaming-quality content is widely compatible).

Plex is also deployed in `media` and claims `gpu.intel.com/xe: 1` (`plex/app/helmrelease.yaml`). Seerr is deployed in `media`.

---

## Data flow

```
User adds movie to Radarr
  │
  ├─> Radarr queries Prowlarr-managed indexers
  │   └─> Custom format scoring applied (Recyclarr-synced)
  │
  ├─> Best release sent to SABnzbd with category=movies
  │   └─> Downloaded to /data/downloads/usenet/complete/movies/
  │
  ├─> SABnzbd post-processes (par2 repair, unrar; Direct Unpack ON)
  │
  ├─> Radarr imports to /data/nas-media/Movies/
  │   └─> File is copied (not hardlinked) due to CephFS -> NFS boundary
  │   └─> Old release removed if upgrade
  │
  ├─> Bazarr detects new file, downloads subtitles
  │
  ├─> Jellyfin / Plex library scan picks up the new file
  │
  └─> Tdarr library scan queues for health check / transcode
      └─> If not already AV1: transcode to AV1 via Intel QSV (B70 on talos-3)
          and/or the external Windows node
      └─> Replaces file in place (/media = NAS Media)
```

---

## Operations

### Triggering a Recyclarr sync

Changes to `recyclarr.yml` apply on the next `@daily` run. To apply immediately:

```sh
kubectl -n downloads create job --from=cronjob/recyclarr recyclarr-manual-$(date +%s)
kubectl -n downloads logs -l app.kubernetes.io/name=recyclarr -f
```

### Re-downloading movies below cutoff

In Radarr UI:
1. **Movies** menu > **Cutoff Unmet**
2. Select all > **Search**

Radarr queues searches for everything below the SQP-1 cutoff score. Useful after expanding custom formats or deleting low-quality files.

### Deleting files directly from NAS

Not recommended -- go through the *arr UI when possible. For bulk cleanup (e.g., removing ISOs), exec into a pod that has the NFS mount:

```sh
kubectl -n downloads exec deployment/radarr -- find /data/nas-media/Movies -name "*.iso" -type f
```

After direct deletion, trigger a rescan in Radarr so it marks the movies as missing:
- Radarr > System > Tasks > **Rescan Movie**

### Checking Tdarr worker health

```sh
kubectl -n media get pods -l app.kubernetes.io/name=tdarr -o wide
```

Expect 1 `tdarr-*` server pod + 1 `tdarr-tdarr-node-*` worker on talos-3. The Tdarr UI shows worker stats under Nodes Overview.

### Monitoring GPU usage

```sh
kubectl get nodes -o json | jq -r '.items[] | "\(.metadata.name): gpu.intel.com/xe=\(.status.allocatable."gpu.intel.com/xe" // "0")"'
```

Only the B70 node (talos-3) reports `99`. `gpu.intel.com/i915` is 0 on every node
after the xe migration.

### Finding which *arr app is failing imports

Check Sonarr/Radarr Activity > History for failed imports. Common causes:
- File already exists at target path (unmonitored duplicate)
- Permission issue on NFS (should not happen -- fsGroup=2000 across all apps)
- Quality profile rejection (check custom format score in release details)
- Radarr matched the grab by movie ID but can't re-parse the downloaded filename (`trackedDownloadState: importBlocked`) -- hardcoded parser behavior, not a settings toggle; common on non-English/MULTi releases. Requires manual **Activity > Queue > Manual Import**. Recyclarr deprioritizes (does not block) parse-risky releases (see Custom format categories above); a stuck item pages via the `RadarrImportQueueBlocked` Gatus alert (`kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml`) instead of accumulating silently

### Backup and recovery

All config PVCs have Volsync with triple backup (Ceph snapshots every 4h, NAS MinIO every 6h, Cloudflare R2 daily). The `shared-downloads` PVC is **not** backed up (transient data). `sabnzbd-incomplete` is disposable scratch with no Volsync. The NAS media library is backed up at the NAS level, separately from cluster Volsync.

---

## Key files

| Purpose | Path |
|---------|------|
| SABnzbd | `kubernetes/apps/base/downloads/sabnzbd/` |
| Sonarr | `kubernetes/apps/base/downloads/sonarr/` |
| Radarr | `kubernetes/apps/base/downloads/radarr/` |
| Lidarr | `kubernetes/apps/base/downloads/lidarr/` |
| Readarr | `kubernetes/apps/base/downloads/readarr/` |
| Prowlarr | `kubernetes/apps/base/downloads/prowlarr/` |
| Bazarr | `kubernetes/apps/base/downloads/bazarr/` |
| Recyclarr config | `kubernetes/apps/base/downloads/recyclarr/app/config/recyclarr.yml` |
| Shared downloads PVC | `kubernetes/apps/base/downloads/pvc/app/shared-downloads.yaml` |
| SAB incomplete PVC | `kubernetes/apps/base/downloads/pvc/app/sabnzbd-incomplete.yaml` |
| SAB disk-space runbook | `docs/downloads/sabnzbd-disk-space-runbook.md` |
| Jellyfin | `kubernetes/apps/media/jellyfin/` |
| Plex | `kubernetes/apps/media/plex/` |
| Tdarr | `kubernetes/apps/media/tdarr/` |
| GPU changelog | `docs/ai-gpu-changelog.md` |
| Volsync component | `kubernetes/components/volsync/` |

---

## References

- [TRaSH Guides](https://trash-guides.info/) - quality profile recommendations, naming schemes, custom formats
- [Servarr Wiki](https://wiki.servarr.com/) - official *arr documentation
- [Recyclarr Docs](https://recyclarr.dev/) - declarative *arr config
- [Tdarr Docs](https://docs.tdarr.io/) - transcoding and distributed setup
- [bjw-s app-template](https://github.com/bjw-s-labs/helm-charts) - Helm chart used for most deployments
