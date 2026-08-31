# Media Stack

Architecture and operational reference for the downloads, management, and media server pipeline. Covers Usenet acquisition, automated library management, quality control, subtitle management, and GPU transcoding.

---

## Overview

The media stack is split across two Kubernetes namespaces (`default` is empty):

| Namespace | Purpose | Key Apps |
|-----------|---------|----------|
| `downloads` | Acquisition + library management | SABnzbd, Sonarr, Radarr, Lidarr, Readarr, Prowlarr, Bazarr, Recyclarr, Autobrr, reading-glasses |
| `media` | Media servers + post-processing | Plex, Seerr, Tdarr, Calibre-Web-Automated, Calibre-Downloader |

`default/kustomization.yaml` has `resources: []`. CWA + Calibre-Downloader live under `media/calibre/` and Flux-target `media`.

`media/calibre-web/` was removed; CWA is the only Calibre app under `media/calibre/`.

`media/immich/` was retired 2026-08-30 - it was never initialized (0 users, 0 assets in 152 days)
and its 100 Gi `immich-library` claim held only nightly dumps of an empty database. Its Ceph,
MinIO and R2 restic repositories are now orphaned and are deleted by a separate task.

`media/jellyfin/` was retired the same day: the captain watches on Plex, and Jellyfin logged zero
playback, session or authentication activity in 24 h. Its `10.50.0.50` LoadBalancer IP is now
free, and it was the third `gpu.intel.com/xe` consumer alongside plex and rsshub-playwright.

FlareSolverr (Cloudflare bypass proxy) lives in the `network` namespace (`kubernetes/apps/base/network/flaresolverr/`), not `downloads`/`media` - consumed by Prowlarr (IndexerProxy, UI-configured, not GitOps) and Calibre-Downloader (`EXT_BYPASSER_URL`).

In `downloads`, **autobrr is live**. `cross-seed` and `qbittorrent` were removed (dead, unreferenced directories) and are no longer present in `kubernetes/apps/main/downloads/kustomization.yaml`. That app-directory removal missed a leftover wiring: sabnzbd's `helmrelease.yaml` still set `XSEED_HOST`/`XSEED_PORT`, shipped a `xseed.sh` post-processing script, and pulled a `cross-seed` 1Password item into its `ExternalSecret`. Confirmed dead on 2026-08-31 (`script_dir` was unset in the live `sabnzbd.ini` and no category named the script, so it never ran - zero matches for `xseed`/`cross-seed` across 5+ months of rotated sabnzbd logs) and removed for good.

**The stack is usenet-only by deliberate captain decision (2026-08-30), not by accident.** No torrent client is deployed, and Radarr's `qBittorrent` download client entry (pointed at a nonexistent `qbittorrent.downloads.svc.cluster.local`, always `enable: false`) plus Prowlarr's four definition-less torrent indexers (`BitSearch`, `TorrentGalaxyClone`, `Isohunt2`, `iDope` - flagged by Prowlarr's own `IndexerNoDefinitionCheck` health check as broken and unusable) were deleted as leftover state implying a torrent path that did not exist. **Both deletions were live API calls against Radarr's and Prowlarr's own databases, not GitOps** - qBittorrent's download-client config and Prowlarr's indexer list live in each app's Postgres backend, not in a committed manifest, so there is nothing in Git to change beyond this note. Re-adding torrent support later (a qBittorrent deployment plus indexer definitions) remains straightforward; this just removes the illusion that it already half-works. Full record: `data/decisions-2026-08-30/downloads-usenet-only.md`.

Flow: **Indexers** -> Prowlarr -> *arr apps -> SABnzbd -> imports to NAS -> Plex serves -> Tdarr transcodes in place to AV1.

---

## Storage Architecture

Storage is split between CephFS (complete downloads), Ceph block (SAB incomplete scratch), and NFS (permanent media):

| Volume | Backing | Access | Size | Purpose |
|--------|---------|--------|------|---------|
| `shared-downloads` | `ceph-filesystem-rwx` (RWX) | RWX | 2 Ti | `usenet/complete/` and other download scratch |
| `sabnzbd-incomplete` | `ceph-block` (RWO) | RWO | 1500 Gi | SAB article-assembly (`download_dir`); bind-mounted **over** `/data/downloads/usenet/incomplete` |
| `{app}-config` | Ceph block (RWO) | RWO | 1-10 Gi | Per-app config PVCs with Volsync backup |
| NFS `/mnt/storage/Media` | NAS (NFS) | RWX | ~40+ TiB | Permanent media library |

Manifests: `kubernetes/apps/base/downloads/pvc/app/shared-downloads.yaml` and `sabnzbd-incomplete.yaml`. There is no `shared-downloads-pvc.yaml`.

Incomplete is **not** on the shared PVC. Since PR #983 it is a separate RBD volume overlaying that path in the sab pod. `du` inside sab will miss a ghost tree on CephFS under the mount. Full incident notes: [`downloads/sabnzbd-disk-space-runbook.md`](downloads/sabnzbd-disk-space-runbook.md). Do not duplicate incomplete-path settings here.

### Hardlink limitation

Downloads live on CephFS, media lives on NFS -- **hardlinks are impossible across filesystems**. Imports from Sonarr/Radarr copy rather than hardlink, which uses temporary double disk space during import. This is an accepted tradeoff for NAS-backed media.

### Mount conventions

| Container path | Source |
|----------------|--------|
| `/data/downloads` | `shared-downloads` (all download + *arr apps) |
| `/data/downloads/usenet/incomplete` | `sabnzbd-incomplete` overlay, **sab pod only** |
| `/data/nas-media` | NFS mount `nas.${SECRET_DOMAIN}:/mnt/storage/Media` (read-write for *arr imports) |
| `/media` | Same NFS mount, alternative path used by Tdarr |

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
| Radarr | SQP-1 (2160p) | Streaming quality 2160p, guide default `min_format_score: 1000` |
| Radarr | SQP-1 (1080p) | Streaming quality 1080p, guide default `min_format_score: 1000` - for titles unlikely to ever get a UHD release |

`assign_scores_to` in `recyclarr.yml` matches by `trash_id`, not profile `name`. A 2026-04-09 guide rewrite renamed the synced profile from `SQP-1 (2160p)` to `[SQP] SQP-1 (2160p)`, which silently orphaned every name-matched custom-format score onto a stale 2-movie profile for over four months; a compounding `min_format_score: 2000` override on the *new* profile (double the guide's 1000) then rejected every remaining release, so the 2026-08-29 outage looked like "no grabs" rather than a rename drift. `trash_id` matching survives a guide rename; `name` matching does not.

**Which movies land on which SQP-1 profile is Radarr database state, not GitOps.** Recyclarr only creates/scores the two profiles above - it never assigns a movie to one. The 2026-08-29 fix classified the existing library with one rule, applied once via the Radarr API (`PUT /api/v3/movie/editor`): a movie moves to **SQP-1 (1080p)** if `year < 2010` **or** its genres include `Documentary`; everything else stays on **SQP-1 (2160p)**. New movies keep whatever profile their import list assigns (mostly `Any`, profile 1) or default to 2160p unless re-classified by the same rule. There is no scheduled job re-applying this - a future large cohort of pre-2010/documentary titles added by list will need the same one-off API pass.

**Custom format categories applied:**
- **Unwanted (Sonarr)**: AV1, BR-DISK, LQ, x265 (HD), Bad Dual Groups, No-RlsGroup, Obfuscated, Retags, Scene
- **Unwanted (Radarr)**: the Sonarr set plus **3D** (Radarr-only; do not toggle 3D on Sonarr from this table)
- **Repacks**: Repack/Proper, Repack2, Repack3
- **Streaming services** (Sonarr): AMZN, ATVP, DCU, DSNP, HBO, HMAX, HULU, iT, MAX, NF, PCOK, PMTP, SHO, STAN
- **Movie versions** (Radarr): Criterion Collection, Hybrid, Remaster, IMAX, IMAX Enhanced
- **HDR** (WEB-2160p): DV (WEBDL)
- **Trusted groups** (Radarr): hallowed
- **Language/naming risk** (Radarr, SQP-1 2160p only, score -25 each): Language: Not English, MULTi -- deprioritizes (does not block) releases whose filename is more likely to defeat Radarr's import parser and get stuck `importBlocked` in the queue; see "Finding which *arr app is failing imports" below

### Bazarr - Subtitle management

**TRaSH-recommended scoring:**

| Setting | Value |
|---------|-------|
| Series minimum score | 90 |
| Movies minimum score | 80 |

Connects to Sonarr and Radarr for series/movie metadata. Provider priority: OpenSubtitles.com > Podnapisi > Supersubtitles > Addic7ed.

### Autobrr

Autobrr is deployed (`kubernetes/apps/main/downloads/kustomization.yaml`). Cross-Seed and qBittorrent were removed with the stack committed usenet-only; Autobrr's torrent-side integrations stay unused unless torrent support is deliberately re-added (Overview above; full record `data/decisions-2026-08-30/downloads-usenet-only.md`).

---

## Tdarr - GPU transcoding

**Namespace:** `media` | **Hostname:** `tdarr.${SECRET_DOMAIN}`

Not a DaemonSet. The worker requests `devic.es/b70-vaapi` (generic-device-plugin on
the discrete Arc Pro B70 at talos-3 PCI `0000:03:00.0`) because the AV1 QSV codec
path needs that card; iGPUs restored by `xe.force_probe=a7a0` are not enough. Still
a single replica (server `internalNode=false`), not a DaemonSet - a DaemonSet would
strand Pending pods on nodes without the B70. Resource split and force_probe
detail: [`ai-gpu-changelog.md`](ai-gpu-changelog.md).

`b70-vaapi` is the same physical card as `devic.es/b70`, exposed under the device
names the kernel gives it (`card1`/`renderD129`) instead of the `card0`/`renderD128`
rename the `b70` group applies. **VA-API cannot use a renamed DRM node** - see
[Verifying VA-API after a GPU change](#verifying-va-api-after-a-gpu-change).

| Controller | Type | Purpose | GPU |
|-----------|------|---------|-----|
| `tdarr` | Deployment (1 pod) | Web UI, orchestrator, database | No |
| `tdarr-node` | Deployment (`replicas: 1`) | Transcode worker on the B70 (talos-3) | `devic.es/b70-vaapi: 1` |

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
transcodecpuWorkers=1      # Fallback worker. Never set this to 0: a GPU-only
                           # node turns any VA-API regression into a silent,
                           # total transcoding outage (2026-08-26, below).
healthcheckgpuWorkers=1
healthcheckcpuWorkers=1
nodeName=<k8s pod nodeName> # Registers with pod's scheduling node
ffmpegVersion=7
```

### Node library scoping (Tdarr server state, not Git)

Which libraries a node will accept work from is per-node Tdarr state
(`librariesToNotProcess`, persisted in the server's `NodeJSONDB`, keyed by node
name). It is **not** GitOps and cannot ship in a PR - it survives pod restarts
because the node re-reads it from the server when it registers.

Current scoping for `talos-3` (the k8s worker):

| Library | Id | talos-3 | Why |
|---------|----|---------|-----|
| Movies AV1 | `gEUZf7Nx6` | **processed** | Restored 2026-08-29 with the B70 VA-API fix |
| Series | `j5g_Es7sD` | **excluded (deliberate)** | See below |

**The Series exclusion is retained on purpose.** All three Talos nodes were
excluded from both libraries as a mitigation after the 2026-08-26 VA-API break
(the job report from that day shows talos-3 still accepting Movies work at
19:19, so the exclusions post-date it). Restoring one library at a time keeps
the blast radius small while the B70 VA-API fix is newly landed and while the
4K remux failures below are still unexplained. Revisit the Series exclusion
once Movies has run clean for a while. `desktop-aviator` (the external Windows
node) has never carried either exclusion.

**Follow-up: 8 errored 4K remuxes, root cause unknown.** Eight files sit in
Tdarr's error table (`table3`), most of them 21-67 GB 2160p DV/HDR10 remux
masters. Their failure is **not** explained by the VA-API break alone and has
not been investigated. Tdarr rewrites in place and the AV1 result is lossy and
irreversible, so **do not bulk-requeue them to clear the error table** - the
failure needs to be understood on one file first, ideally against a copy.
Re-enabling a library does not re-queue them: `librariesToNotProcess` is a
node-side accept filter, and errored files stay parked until explicitly
requeued (verified 2026-08-29 - clearing the Movies exclusion left all 8 in
`table3` with the transcode queue at 0).

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
- Both TRaSH SQP-1 score sets include `BR-DISK` with a large negative score (and our Unwanted `assign_scores_to` reinforces it on SQP-1 2160p)
- That penalty alone drives the release below the guide's `min_format_score: 1000` floor
- Existing ISO files should be deleted and re-downloaded via Radarr's "Cutoff Unmet" view

---

## Plex - Media server

**Namespace:** `media` | **GPU:** `gpu.intel.com/xe: 1` (`plex/app/helmrelease.yaml`). Seerr is also deployed in `media`.

### Library scan triggers (application settings, not GitOps)

Plex ships with **every** scan trigger disabled by default, and this cluster ran that way
from first boot until 2026-08-29: the library was built entirely by hand-run "Scan Library
Files" calls, the last one 2026-06-19T00:44:21Z. Because Seerr polls Plex's "recently added"
every 5 minutes and reports success, a frozen library still looks green on every dashboard --
it took 71 days and 96 Radarr-imported movies going invisible before anyone noticed.

Two triggers are configured now, and both are UI/API-only state that lives in each app's own
database -- **a config-volume rebuild of Plex, Radarr, or Sonarr loses this** the same way it
would lose an Authentik Proxy Provider (see the root `CLAUDE.md` NOTES). Nothing in this repo
re-applies it, so re-check it after any restore-from-scratch:

1. **Radarr and Sonarr -> Settings -> Connect -> "Plex Media Server"**, host
   `plex.media.svc.cluster.local:32400`, `On Import` + `On Upgrade` + `On Rename` (+
   `On Movie Delete` in Radarr, `On Episode File Delete` in Sonarr) enabled. This is the
   primary trigger: it refreshes only the one changed folder, seconds after every import.
2. **Plex -> Settings -> Library -> "Update my library periodically"**
   (`ScheduledLibraryUpdatesEnabled`), hourly. Belt-and-braces for hand-placed files that never
   go through Radarr/Sonarr (NAS files owned by `uid 3000` with bare names, vs. `uid 2000` +
   `{imdb-...}` for Radarr imports) -- those get no notification from either app.
3. **Do not enable** Plex's "scan my library automatically" (`FSEventLibraryUpdatesEnabled`,
   filesystem-event scanning). Media arrives over NFS from another host, so inotify-style
   events never fire there; this setting would look correct and silently do nothing.

`autoEmptyTrash` is already on, so a manual or scheduled scan also clears out Plex entries
whose backing file was deleted or upgraded away (11 such entries existed on 2026-08-29, all
cleared by the scan that landed this section).

**Counterfactual check, if this is ever suspected to have regressed:** compare Plex's on-disk
movie folder count against its library size, and check how old `scannedAt` is.

```sh
export KUBECONFIG=/path/to/kubeconfig   # never the mise-shim-overridden one, see NOTES
PLEX_TOKEN=$(kubectl --kubeconfig=$KUBECONFIG exec -n media deploy/plex -c app -- sh -c \
  'grep -oE "PlexOnlineToken=\"[^\"]+\"" "/config/Library/Application Support/Plex Media Server/Preferences.xml" | cut -d\" -f2')

# disk truth (movie folder count)
kubectl --kubeconfig=$KUBECONFIG exec -n media deploy/plex -c app -- \
  find /data/nas-media/Movies -mindepth 1 -maxdepth 1 -type d | wc -l

# Plex library size (MediaContainer totalSize= on /all; Size=0 returns size=\"0\")
# + section title/scannedAt age
kubectl --kubeconfig=$KUBECONFIG exec -n media deploy/plex -c app -- sh -c \
  "curl -s -H 'X-Plex-Token: ${PLEX_TOKEN}' 'http://localhost:32400/library/sections/1/all?X-Plex-Container-Start=0&X-Plex-Container-Size=0'" \
  | grep -oE 'MediaContainer[^>]*totalSize=\"[^\"]*\"'
kubectl --kubeconfig=$KUBECONFIG exec -n media deploy/plex -c app -- sh -c \
  "curl -s -H 'X-Plex-Token: ${PLEX_TOKEN}' 'http://localhost:32400/library/sections'" \
  | grep -oE '(title|scannedAt)=\"[^\"]*\"'
```

The folder count and MediaContainer `totalSize` should match (within a file or two -- Plex does not
scan every container it can't parse, e.g. a bare `.VOB` DVD rip has no Plex entry and is not a
scan-trigger fault). With `X-Plex-Container-Size=0`, page `size` is always 0; use `totalSize` for
the real library count. If they diverge by dozens and `scannedAt` is more than a day old, the
Connect notifications or the periodic-scan setting were lost -- reapply steps 1-2 above, then
run one manual "Scan Library Files" to clear the backlog.

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
  ├─> Plex library scan picks up the new file
  │   └─> Plex does not watch NFS; needs Radarr/Sonarr Connect + hourly scheduled update
  │       (see "Library scan triggers" under Plex above). Lost on config-volume rebuild.
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

### Backlog missing-movie search

**Nothing in this stack searches for a monitored-but-missing movie on its own.** Radarr's own
task list has no scheduled "missing movie search", and every import list runs `searchOnAdd:
false` - a movie a list adds just sits `monitored: true` / no file until Radarr happens to see a
matching *new* RSS release, or a human triggers a search. This is invisible day to day (RSS
`Reports grabbed: 0` looks identical to "everything is already downloaded"), so a backlog only
becomes visible when someone checks `GET /api/v3/movie` for `monitored && !hasFile` - it was 347
movies on 2026-08-29, discovered while investigating the outage documented above.

Run a backlog search deliberately, after any event that could have left movies stuck missing: the
initial cause here (a `min_format_score` override rejecting every release), a big import-list
addition, or a long Radarr outage. **Always size it against free disk first** - a few hundred
movies at even moderate quality can be multiple TB, and nothing else in this stack checks that
before grabbing:

1. Count monitored-and-missing movies **per quality profile** (`GET /api/v3/movie`, filter
   `monitored && !hasFile`, group by `qualityProfileId`) - different profiles have very different
   expected sizes.
2. For a small sample per profile, run an interactive search (`GET /api/v3/release?movieId=<id>`)
   and read the *accepted* releases' real sizes and hit rate - do not guess a flat average. Genre
   matters: documentary/TV-movie titles on this stack's profiles have a much lower accept rate
   than mainstream theatrical titles, because no release group produces an HD-Bluray-tier release
   for them (same defect that caused the original outage, just below the size floor for scoped
   profiles rather than above it).
3. Multiply sampled hit-rate x sampled size x population per profile, sum, and compare against
   the root folder's live free space (`GET /api/v3/rootfolder`). If the estimate is more than
   roughly half of free space, do not run the full search - stage a subset, or escalate for a
   decision (a size cap on the profile, accepting the full run anyway, etc).
4. If it fits, trigger the search in **batches**, not one 300+ movie burst: `POST /api/v3/command`
   with `{"name": "MoviesSearch", "movieIds": [...]}`, a bounded batch size (used 20), and a pause
   between batches (used 120s). This stack's enabled indexers are proxied through Prowlarr and
   include several usenet providers with their own daily/per-window API quotas
   (`GET /api/v3/indexer` for the current list) - a single unpaced burst across 300+ movies can
   trip those limits and get the whole indexer set rate-limited or banned, which is worse than a
   slower backlog clear.
5. Confirm end to end: releases grabbed (`GET /api/v3/queue`), handed to the download client, and
   actually downloading - not just that the search command returned. Movies that still find
   nothing after a real profile fix are an expected outcome, not a bug; group them by what they
   have in common (language mismatch, no HD-Bluray-tier release group, not yet released) since
   that tells you whether another profile gap remains.

**Candidate fix, not yet applied:** flipping `searchOnAdd: true` on the import lists
(`kubernetes/apps/base/downloads/recyclarr/app/config/recyclarr.yml` does not manage import
lists; they are Radarr-side, `Settings > Lists`) would make a newly-added movie search
immediately instead of waiting on RSS, closing the root cause of this class of silent backlog.
It was deliberately **not** enabled as part of the 2026-08-29 fix because it changes ongoing
behavior (an immediate indexer hit on every future list addition) rather than clearing the
existing backlog, and needs its own rate-limit/quota consideration for list-driven bulk adds.

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
kubectl get nodes -o json | jq -r '.items[] | "\(.metadata.name): xe=\(.status.allocatable."gpu.intel.com/xe" // "0") b70=\(.status.allocatable."devic.es/b70" // "0") b70-vaapi=\(.status.allocatable."devic.es/b70-vaapi" // "0")"'
```

`devic.es/b70` and `devic.es/b70-vaapi` are `99` only on talos-3. Both are the
same physical card: `b70` for Level Zero consumers (vllm, comfyui),
`b70-vaapi` for VA-API consumers (tdarr-node).
`gpu.intel.com/xe` is the Intel plugin pool for plex/playwright,
scoped to the iGPU (`allowIDs: "0xa7a0"`) and present as `99` on all three
nodes. `gpu.intel.com/i915` stays 0 after the xe migration.

### Verifying VA-API after a GPU change

**Allocatable capacity is not proof that transcoding works.** Run this after ANY
change to `generic-device-plugin`'s config, the Talos GPU kernel args, a node's
GPU hardware, or the `tdarr_node` image. It takes seconds and it is the only
check that exercises the path Tdarr actually uses:

```sh
# 1. Names must match minors. card1 must be minor 1, renderD129 must be minor 129.
kubectl -n media exec deploy/tdarr-tdarr-node -c app -- ls -l /dev/dri/

# 2. VA-API must open a display and list AV1 encode.
kubectl -n media exec deploy/tdarr-tdarr-node -c app -- \
  vainfo --display drm --device /dev/dri/renderD129 | grep -E 'Driver version|AV1.*Enc'

# 3. The encoder Tdarr invokes must actually run.
kubectl -n media exec deploy/tdarr-tdarr-node -c app -- \
  tdarr-ffmpeg -y -f lavfi -i testsrc=size=1920x1080:rate=30 -frames:v 120 \
  -c:v av1_qsv -b:v 5M /tmp/vaapi-check.mp4
```

Healthy output is `Driver version: Intel iHD driver ... - 26.2.2`,
`VAProfileAV1Profile0 : VAEntrypointEncSlice`, and an ffmpeg run ending in a
`frame= 120 ... Lsize=` summary. The failure signature is
`Failed to a DRM display for the given device` from `vainfo` and
`Cannot open a VA display from DRM device` / `Device creation failed: -542398533`
from ffmpeg.

**Why this check exists.** `generic-device-plugin` can rename a device node via
`mountPath`, and for DRM nodes that rename is fatal to VA-API. libdrm does not
trust the path you hand it: it `fstat()`s the fd, reads
`/sys/dev/char/<major>:<minor>/uevent`, and re-derives the canonical `DEVNAME`.
If the container does not also have the device at that canonical name,
`vaGetDisplayDRM()` fails before any driver loads. Level Zero (vllm, comfyui)
opens whatever `/dev/dri/render*` it finds and is unaffected, so **the AI stack
stays green while transcoding is completely dead**. That is exactly what happened
on 2026-08-26: PR #1443 renamed the B70 to `card0`/`renderD128`, and because
`transcodecpuWorkers` was `0` there was no fallback, so every job failed for three
days with the Tdarr UI showing an idle, healthy server. See
[`ai-gpu-changelog.md`](ai-gpu-changelog.md).

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
| Plex | `kubernetes/apps/base/media/plex/` |
| Tdarr | `kubernetes/apps/base/media/tdarr/` |
| GPU changelog | `docs/ai-gpu-changelog.md` |
| Volsync component | `kubernetes/components/volsync/` |

---

## References

- [TRaSH Guides](https://trash-guides.info/) - quality profile recommendations, naming schemes, custom formats
- [Servarr Wiki](https://wiki.servarr.com/) - official *arr documentation
- [Recyclarr Docs](https://recyclarr.dev/) - declarative *arr config
- [Tdarr Docs](https://docs.tdarr.io/) - transcoding and distributed setup
- [bjw-s app-template](https://github.com/bjw-s-labs/helm-charts) - Helm chart used for most deployments
