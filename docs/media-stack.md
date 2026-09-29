# Media stack

Operator notes for the downloads and media namespaces. Agent tripwires live in
skill `media-stack`. Tdarr flow behaviour lives in skill `tdarr-transcoding`.

The stack is usenet-only. No torrent client is deployed. Radarr's disabled
qBittorrent client and Prowlarr's four definition-less torrent indexers were
removed through each app's API. That state is in the app database, not Git.
Re-adding a torrent path is a new decision.

## Storage

Imports copy. A hardlink cannot cross CephFS (`shared-downloads`) and NFS
(`nas-media`). Budget double disk for the copy window.

Do not `du` or delete `shared-downloads` from the SABnzbd pod. Since PR #983
the incomplete tree is an RBD volume that shadows that path inside the pod.
Use the Radarr pod. Alerts and the ghost-tree incident:
`docs/downloads/sabnzbd-disk-space-runbook.md`.

| Path | Volume |
|---|---|
| `/data/downloads/usenet/complete` | `shared-downloads` (CephFS, 2Ti) |
| `/data/downloads/usenet/incomplete` | `sabnzbd-incomplete` (RBD) |
| `/data/nas-media` | NFS |

SABnzbd pauses when complete free space hits 100G. Alerts fire at 15% and 7%
free, before that pause.

## Recyclarr - Declarative quality config

`assign_scores_to` matches `trash_id`, never profile `name`. A TRaSH rename
orphans name-matched scores onto the old profile and creates an empty new one
(PR #1369). Which movie sits on which profile is Radarr database state.
Recyclarr does not assign movies. A profile change needs a Radarr API pass:
`GET /api/v3/qualityprofile`, then `PUT /api/v3/movie/editor`.

`min_format_score` above what any release can reach looks like "indexers are
down". Config: `kubernetes/apps/base/downloads/recyclarr/app/config/recyclarr.yml`.

```sh
kubectl -n downloads create job --from=cronjob/recyclarr recyclarr-manual-$(date +%s)
```

## Library scoping (Tdarr server state, not Git)

`librariesToNotProcess` is a Tdarr Pro feature and a no-op on this unlicensed
install. Scope is the library toggle `processTranscodes`.

| Library | Id | Transcodes |
|---|---|---|
| Movies AV1 | `gEUZf7Nx6` | `processTranscodes: true` |
| Series | `j5g_Es7sD` | `processTranscodes: false` |

Health checks and scanning stay on for Series. The flow, the parked masters,
and the open Dolby Vision choice are skill `tdarr-transcoding` and
`docs/tdarr-errored-remuxes.md`. Rebuild steps: `docs/tdarr/README.md`.
Keep `transcodecpuWorkers` at least 1.

## Library scan triggers (application settings, not GitOps)

Plex does not see NFS filesystem events. Leave
`FSEventLibraryUpdatesEnabled` off. Two settings live in the app databases
and are lost on a scratch restore:

1. Radarr and Sonarr Connect, host `plex.media.svc.cluster.local:32400`, on
   import, upgrade, and rename.
2. Plex "Update my library periodically"
   (`ScheduledLibraryUpdatesEnabled`), hourly.

Seerr stays green while the library is frozen. Compare the movie folder count
to Plex `totalSize` (not the page `size`) and to `scannedAt`. Pass
`--kubeconfig` explicitly. `autoEmptyTrash` is on. Skill `media-stack`,
`references/plex.md`.

## Backlog missing-movie search

Radarr has no scheduled missing-movie task. Import lists run
`searchOnAdd: false`. A monitored movie with no file sits until a new RSS
hit or a human search. Size the search against free disk first.

1. Count `monitored && !hasFile` per quality profile (`GET /api/v3/movie`).
2. Sample accepted release sizes (`GET /api/v3/release?movieId=`).
3. Compare the estimate to `GET /api/v3/rootfolder` free space. Stay under
   about half of free space, or stage a subset.
4. `POST /api/v3/command` `MoviesSearch` in batches of about 20, with a pause
   between batches, so indexer quotas survive.
5. Confirm the queue is downloading, not only that the command returned.

Cutoff Unmet is a different action: it searches movies that already have a
file below cutoff. `searchOnAdd: true` is not enabled.

## Verifying VA-API after a GPU change

Allocatable GPU capacity is not proof that transcoding works. Run this after
any change to `generic-device-plugin`, the Talos GPU kernel args, the GPU
hardware, or the `tdarr_node` image. The mechanism (libdrm reopens the
canonical `DEVNAME`) is skill `intel-gpu`.

```sh
kubectl -n media exec deploy/tdarr-tdarr-node -c app -- ls -l /dev/dri/
kubectl -n media exec deploy/tdarr-tdarr-node -c app -- \
  vainfo --display drm --device /dev/dri/renderD129 | grep -E 'Driver version|AV1.*Enc'
kubectl -n media exec deploy/tdarr-tdarr-node -c app -- \
  tdarr-ffmpeg -y -f lavfi -i testsrc=size=1920x1080:rate=30 -frames:v 120 \
  -c:v av1_qsv -b:v 5M /tmp/vaapi-check.mp4
```

Healthy: Intel iHD driver version, `VAProfileAV1Profile0 : VAEntrypointEncSlice`,
and ffmpeg ending in a `frame= 120` summary. Failure: `Failed to a DRM display`
from vainfo, or `Device creation failed: -542398533` from ffmpeg.
`card1` / `renderD129` are the kernel names. A renamed `mountPath` leaves
Level Zero green and VA-API dead.

## Key files

| App | Path |
|---|---|
| SABnzbd, Sonarr, Radarr, Lidarr, Readarr, Prowlarr, Bazarr, Recyclarr | `kubernetes/apps/base/downloads/` |
| Shared downloads and the disk janitor | `kubernetes/apps/base/downloads/pvc/`, `downloads/maintenance/` |
| Plex, Seerr, Calibre, Tdarr | `kubernetes/apps/base/media/` |
| Disk-full runbook | `docs/downloads/sabnzbd-disk-space-runbook.md` |
