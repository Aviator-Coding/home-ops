# Media Stack

Architecture and operational reference for the downloads, management, and media server pipeline. Covers Usenet acquisition, automated library management, quality control, subtitle management, and distributed GPU transcoding.

---

## Overview

The media stack is split across three Kubernetes namespaces:

| Namespace | Purpose | Key Apps |
|-----------|---------|----------|
| `downloads` | Acquisition + library management | SABnzbd, Sonarr, Radarr, Lidarr, Readarr, Prowlarr, Bazarr, Recyclarr, Autobrr, FlareSolverr |
| `media` | Media servers + post-processing | Jellyfin, Immich, Tdarr, Calibre-Web |
| `default` | Ebook ingestion pipeline | Calibre-Web-Automated, Calibre-Downloader |

Flow: **Indexers** -> Prowlarr -> *arr apps -> SABnzbd -> imports to NAS -> Jellyfin serves -> Tdarr transcodes in place.

---

## Storage Architecture

Storage is intentionally split between CephFS (transient downloads) and NFS (permanent media):

| Volume | Backing | Access | Size | Purpose |
|--------|---------|--------|------|---------|
| `shared-downloads-pvc` | CephFS (RWX) | RWX | 2 TiB | Active downloads, unpack workspace |
| `{app}-config` | Ceph block (RWO) | RWO | 1-10 Gi | Per-app config PVCs with Volsync backup |
| NFS `/mnt/storage/Media` | NAS (NFS) | RWX | ~40+ TiB | Permanent media library |
| `immich-library` | CephFS (RWX) | RWX | 100 Gi | Photos |
| `jellyfin-metadata` | Ceph block | RWO | 50 Gi | Jellyfin thumbnails/metadata |

### Hardlink limitation

Downloads live on CephFS, media lives on NFS -- **hardlinks are impossible across filesystems**. Imports from Sonarr/Radarr copy rather than hardlink, which uses temporary double disk space during import. This is an accepted tradeoff for NAS-backed media.

### Mount conventions

| Container path | Source |
|----------------|--------|
| `/data/downloads` | `shared-downloads-pvc` (all download + *arr apps) |
| `/data/nas-media` | NFS mount `nas.${SECRET_DOMAIN}:/mnt/storage/Media` (read-write for *arr imports) |
| `/media` | Same NFS mount, alternative path used by Tdarr and Jellyfin |

---

## Application Reference

### SABnzbd — Usenet download client

**Namespace:** `downloads` | **Image:** `ghcr.io/home-operations/sabnzbd` | **Hostname:** `sabnzbd.${SECRET_DOMAIN}`

Categories map to the *arr apps:

| Category | Folder | Consumed by |
|----------|--------|-------------|
| `movies` | `movies` | Radarr |
| `tv` | `tv` | Sonarr |
| `music` | `music` | Lidarr |
| `books` | `books` | Readarr |

**Critical UI settings (not in GitOps):**
- Article Cache: **1 GB**
- Direct Unpack: **OFF** (TRaSH recommendation)
- Disable **ALL Sorting** (the *arr apps handle renaming)
- Abort jobs that cannot be completed: **ON**
- Action on encrypted RAR: **Abort**
- Incomplete: `/data/downloads/usenet/incomplete`
- Complete: `/data/downloads/usenet/complete`

The init container pre-creates the category subdirectories on the shared PVC.

### Prowlarr — Indexer manager

Central proxy for all indexers. All *arr apps should be registered in `Settings > Apps` and pull indexer config from Prowlarr. Do **not** add indexers directly to Sonarr/Radarr/Lidarr.

Service DNS registered in Prowlarr:
- `http://sonarr.downloads.svc.cluster.local:8989`
- `http://radarr.downloads.svc.cluster.local:7878`
- `http://lidarr.downloads.svc.cluster.local:8080`
- `http://readarr.downloads.svc.cluster.local:8787`

### Sonarr / Radarr / Lidarr / Readarr — Library managers

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

### Recyclarr — Declarative quality config

**File:** `kubernetes/apps/downloads/recyclarr/app/config/recyclarr.yml`
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
- **Unwanted**: AV1, BR-DISK, LQ, x265 (HD), 3D, Bad Dual Groups, No-RlsGroup, Obfuscated, Retags, Scene
- **Repacks**: Repack/Proper, Repack2, Repack3
- **Streaming services** (Sonarr): AMZN, ATVP, DCU, DSNP, HBO, HMAX, HULU, iT, MAX, NF, PCOK, PMTP, SHO, STAN
- **Movie versions** (Radarr): Criterion Collection, Hybrid, Remaster, IMAX, IMAX Enhanced
- **HDR** (WEB-2160p): DV (WEBDL)
- **Trusted groups** (Radarr): hallowed

### Bazarr — Subtitle management

**TRaSH-recommended scoring:**

| Setting | Value |
|---------|-------|
| Series minimum score | 90 |
| Movies minimum score | 80 |

Connects to Sonarr and Radarr for series/movie metadata. Provider priority: OpenSubtitles.com > Podnapisi > Supersubtitles > Addic7ed.

### Cross-Seed + Autobrr

Cross-Seed matches completed downloads against torrent indexers for seeding without re-download. Autobrr monitors release feeds for instant snatches. Both integrate with qBittorrent and SABnzbd (commented out in current kustomization but the infrastructure exists).

---

## Tdarr — Distributed transcoding

**Namespace:** `media` | **Hostname:** `tdarr.${SECRET_DOMAIN}`

Deployed as a single HelmRelease with two controllers:

| Controller | Type | Purpose | GPU |
|-----------|------|---------|-----|
| `tdarr` | Deployment (1 pod) | Web UI, orchestrator, database | No |
| `tdarr-node` | DaemonSet (1 pod per K8s node) | Transcode workers | `gpu.intel.com/i915: 1` each |

This gives **3 parallel GPU transcodes** across talos-1, talos-2, talos-3. The server has `internalNode=false` so it doesn't compete for GPU.

### Worker environment

Each worker pod gets:
```
serverIP=tdarr.media.svc.cluster.local
serverPort=8266
nodeType=mapped            # All workers see /media at the same path
transcodegpuWorkers=1      # One QSV transcode at a time
transcodecpuWorkers=0      # Force GPU usage
healthcheckgpuWorkers=1
healthcheckcpuWorkers=1
nodeName=<k8s pod nodeName> # Registers with pod's scheduling node
```

### Plugin stack (configured in Tdarr UI)

Per library:

1. `Migz-Remove Image Formats From File` — strip embedded thumbnails
2. `Lmg1-Reorder Streams` — main audio/subtitle tracks first
3. `Boosh-Transcode Using QSV GPU & FFMPEG` — HEVC transcode
4. `New File Size Check` — verify output is smaller

Boosh plugin configuration:

| Setting | Value |
|---------|-------|
| `encoder` | `hevc` |
| `container` | `mkv` |
| `target_bitrate_modifier` | `0.5` |
| `encoder_speedpreset` | `slow` |
| `enable_10bit` | `false` |

### Library file type filter

Tdarr cannot process disc images (`.iso`). Set allowed container extensions to:
```
mkv,mp4,avi,ts,mov,m4v,wmv,flv,webm
```

### ISO file policy

BR-DISK and ISO files are blocked going forward:
- Recyclarr adds `BR-DISK` custom format with negative score
- `min_format_score: 2000` on SQP-1 rejects anything with BR-DISK penalties
- Existing ISO files should be deleted and re-downloaded via Radarr's "Cutoff Unmet" view

---

## Jellyfin — Media server

**Namespace:** `media` | **LoadBalancer IP:** `10.50.0.50` | **GPU:** `gpu.intel.com/i915: 1`

Single-pod deployment reading the NFS media mount read-only at `/data/nas-media`. Intel GPU handles hardware transcoding for any clients that can't direct play (rare with the SQP-1 profile since streaming-quality content is widely compatible).

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
  ├─> SABnzbd post-processes (par2 repair, unrar)
  │
  ├─> Radarr imports to /data/nas-media/Movies/
  │   └─> File is copied (not hardlinked) due to CephFS -> NFS boundary
  │   └─> Old release removed if upgrade
  │
  ├─> Bazarr detects new file, downloads subtitles
  │
  ├─> Jellyfin library scan picks up the new file
  │
  └─> Tdarr library scan queues for health check
      └─> If H.264: transcode to HEVC via Intel QSV on one of 3 GPU workers
      └─> Replaces file in place (/media = /data/nas-media)
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

Expect 1 `tdarr-*` server pod + 3 `tdarr-tdarr-node-*` worker pods (one on each node). The Tdarr UI shows worker stats under Nodes Overview.

### Monitoring GPU usage

```sh
kubectl get nodes -o json | jq -r '.items[] | "\(.metadata.name): gpu.intel.com/i915=\(.status.allocatable."gpu.intel.com/i915" // "0")"'
```

Each Meigao Venus node reports `99` (the Intel device plugin uses shared mode up to 99 concurrent pods).

### Finding which *arr app is failing imports

Check Sonarr/Radarr Activity > History for failed imports. Common causes:
- File already exists at target path (unmonitored duplicate)
- Permission issue on NFS (should not happen — fsGroup=2000 across all apps)
- Quality profile rejection (check custom format score in release details)

### Backup and recovery

All config PVCs have Volsync with triple backup (Ceph snapshots every 4h, NAS MinIO every 6h, Cloudflare R2 daily). The shared-downloads-pvc is **not** backed up (transient data). The NAS media library is backed up at the NAS level, separately from cluster Volsync.

---

## Key files

| Purpose | Path |
|---------|------|
| SABnzbd | `kubernetes/apps/downloads/sabnzbd/` |
| Sonarr | `kubernetes/apps/downloads/sonarr/` |
| Radarr | `kubernetes/apps/downloads/radarr/` |
| Lidarr | `kubernetes/apps/downloads/lidarr/` |
| Readarr | `kubernetes/apps/downloads/readarr/` |
| Prowlarr | `kubernetes/apps/downloads/prowlarr/` |
| Bazarr | `kubernetes/apps/downloads/bazarr/` |
| Recyclarr config | `kubernetes/apps/downloads/recyclarr/app/config/recyclarr.yml` |
| Shared downloads PVC | `kubernetes/apps/downloads/pvc/app/shared-downloads-pvc.yaml` |
| Jellyfin | `kubernetes/apps/media/jellyfin/` |
| Tdarr | `kubernetes/apps/media/tdarr/` |
| Volsync component | `kubernetes/components/volsync/` |

---

## References

- [TRaSH Guides](https://trash-guides.info/) — quality profile recommendations, naming schemes, custom formats
- [Servarr Wiki](https://wiki.servarr.com/) — official *arr documentation
- [Recyclarr Docs](https://recyclarr.dev/) — declarative *arr config
- [Tdarr Docs](https://docs.tdarr.io/) — transcoding and distributed setup
- [bjw-s app-template](https://github.com/bjw-s-labs/helm-charts) — Helm chart used for most deployments
