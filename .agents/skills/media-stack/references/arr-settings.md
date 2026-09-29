# Download paths and usenet-only

The downloads namespace acquires media by usenet. SABnzbd is the only download
client. Categories in the SABnzbd chart map to Sonarr, Radarr, Lidarr and
Readarr. There is no torrent client, and the old Radarr qBittorrent download
client and the four definition-less Prowlarr torrent indexers (BitSearch,
TorrentGalaxyClone, Isohunt2, iDope) were removed through those apps' APIs.
That state lives in each app's database. Nothing in Git re-creates it, and
nothing in Git deletes it if someone adds it back in the UI.

## Disk layout

| Path in the pod | Volume | Who sees it |
|---|---|---|
| `/data/downloads` | `shared-downloads` CephFS RWX, 2 Ti, class `ceph-filesystem-rwx` | download apps and the *arr apps |
| `/data/downloads/usenet/incomplete` | `sabnzbd-incomplete` RBD | SABnzbd pod only. The mount shadows that path on CephFS |
| `/data/nas-media` | NFS `nas` media export | *arr imports |

Hardlinks between CephFS and NFS cannot work. Sonarr and Radarr copy on
import, so a large import briefly uses the space twice.

SABnzbd auto-pauses when complete-dir free space drops to about 100G, which is
about 5% of 2 Ti. The capacity alerts at 15% (warning) and 7% (critical) are
there to fire first. Runbook, including why `du` inside the SABnzbd pod misses
a ghost tree: [sabnzbd-disk-space.md](sabnzbd-disk-space.md). Alert text in
`kubernetes/apps/base/downloads/maintenance/app/prometheusrule.yaml` links that
file. Keep the path.

Do not `kubectl exec` into the SABnzbd pod to delete `usenet/incomplete` on
the shared volume. The RBD mount hides it. Use the Radarr pod, which sees the
CephFS tree.

## Imports

A file already at the target, an NFS permission error, a quality-profile
rejection, or Radarr's `importBlocked` parse failure (common on MULTi names)
all show up in Activity, not in Git. `RadarrImportQueueBlocked` in the Gatus
config is the page for a stuck queue. Recyclarr deprioritises parse-risky
releases. It does not block them.
