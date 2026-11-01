# Syncthing

Folder shares and device names live in Syncthing's config on the `syncthing`
claim, not in Git. This tree owns the HelmRelease and the `syncthing-data`
claim.

| Claim | Size | Purpose |
|---|---|---|
| `syncthing` | 1Gi | Config, device identity, pairing. Do not resize or delete. |
| `syncthing-data` | 15Gi | Synced folder roots under `/var/syncthing/data/`. |

Both claims are backed up by kopiur only; `syncthing-data` left VolSync on
2026-11-01 (issue #2202). Its cache is claim-sized (`KOPIUR_CACHE_CAPACITY`
15Gi in the `syncthing-data-kopiur` Kustomization) and must stay at least the
claim size. Do not delete a kopiur Snapshot CR. Skill `kopiur-backups`.

Folder types last recorded in the app database (confirm with
`GET /rest/config/folders` before changing them): `documents` and `projects`
are sendreceive; `screenshots` and `camera-roll` are receiveonly so a
cluster-side delete cannot destroy the Mac originals. An empty `id` with
path `~` is Syncthing's defaults block, not a folder.

Accepting a share is a click in the Mac Syncthing UI. Pairing a phone for
`camera-roll` is a separate step. Kubernetes cannot shrink a PVC.
