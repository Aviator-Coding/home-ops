# Fleet restore proof

Overlays that say "row N" mean this table. The row numbers are stable. Do not
renumber them. The drill restored every claim from both `ceph` and `r2` into a
scratch PVC and compared per-file sha256. Live PVCs were not replaced.

`live files` is the count on the claim at measurement. `snapshot filesNew` is
the on-demand verification snapshot. `restored` is the scratch PVC.
`restore manifest sha256` is the first 12 hex digits of the sorted
`sha256  path` manifest.

Markers:

- `‡` cache-excluded. kopia honours `CACHEDIR.TAG` and omits those directories'
  contents. The directory itself returns. `filesNew` equals the restored count.
  Affected: `ai/hermes` (uv caches, venvs, pytest cache), `home-assistant`
  (`.venv`), `calibre-web-automated` (`.cache/fontconfig`). No user-authored
  file lived inside those trees. VolSync restic does not set
  `--exclude-caches`, so the restic copies may still hold them. Not a fidelity
  defect.
- `*` too small to prove much (1-8 files). The mechanism worked. The data path
  was barely exercised. Classification in the next section.
- `†` `ai/hermes` had two volatile files (`logs/errors.log`, `kanban.db-shm`)
  that differed from live and matched **between** the ceph and r2 restores.
  Write activity during the window, not loss.

| # | claim | mover | ceph | r2 | live | filesNew ceph/r2 | restored ceph/r2 | manifest sha256 (12) | verdict |
|--:|---|---|:-:|:-:|--:|--:|--:|---|---|
| 1 | `ai/hermes` | 10000:10000 | Succeeded | Succeeded | 89388 | 65921 / 65921 | 65921 / 65921 | `f37eb3217b51` / `f37eb3217b51` | **PASS** † ‡ |
| 2 | `ai/opencode` | 1000:1000 | Succeeded | Succeeded | 4749 | 4749 / 4749 | 4749 / 4749 | `d9954a835a98` / `d9954a835a98` | **PASS** |
| 3 | `ai/repo-wiki` | 1000:1000 | Succeeded | Succeeded | 165 | 165 / 165 | 165 / 165 | `0e059f1e6400` / `0e059f1e6400` | **PASS** |
| 4 | `database/pgadmin` | 5050:5050 | Succeeded | Succeeded | 3 | 3 / 3 | 3 / 3 | `81937622ac41` / `81937622ac41` | **PASS** |
| 5 | `downloads/autobrr` | 2000:2000 | Succeeded | Succeeded | 1 | 1 / 1 | 1 / 1 | `fa14f3e480fb` / `fa14f3e480fb` | **PASS** \* |
| 6 | `downloads/bazarr-config` | 2000:2000 | Succeeded | Succeeded | 17 | 17 / 17 | 17 / 17 | `42417889da0c` / `42417889da0c` | **PASS** |
| 7 | `downloads/lidarr-config` | 2000:2000 | Succeeded | Succeeded | 478 | 478 / 478 | 478 / 478 | `7b727e6495b0` / `7b727e6495b0` | **PASS** |
| 8 | `downloads/prowlarr-config` | 3002:3000 | Succeeded | Succeeded | 710 | 710 / 710 | 710 / 710 | content-matched stable set, not destination-identical | **PASS** |
| 9 | `downloads/radarr-config` | 2000:2000 | Succeeded | Succeeded | 6086 | 6086 / 6086 | 6086 / 6086 | `6fb28da0d1cf` / `6fb28da0d1cf` | **PASS** |
| 10 | `downloads/readarr-config` | 2000:2000 | Succeeded | Succeeded | 4197 | 4197 / 4197 | 4197 / 4197 | `30b6a67c5a35` / `30b6a67c5a35` | **PASS** |
| 11 | `downloads/recyclarr-config` | 2000:2000 | Succeeded | Succeeded | 2913 | 2913 / 2913 | 2913 / 2913 | `05e8116fc921` / `05e8116fc921` | **PASS** |
| 12 | `downloads/sabnzbd-config` | 2000:2000 | Succeeded | Succeeded | 2064 | 2064 / 2064 | 2064 / 2064 | `e8a05b354ce7` / `e8a05b354ce7` | **PASS** |
| 13 | `downloads/sonarr-config` | 2000:2000 | Succeeded | Succeeded | 124 | 124 / 124 | 124 / 124 | `7077025cb0fd` / `7077025cb0fd` | **PASS** |
| 14 | `home-automation/esphome-config` | 2000:2000 | Succeeded | Succeeded | 46 | 46 / 46 | 46 / 46 | `baa6b55032b5` / `baa6b55032b5` | **PASS** |
| 15 | `home-automation/home-assistant` | 1000:1000 | Succeeded | Succeeded | 96 | 79 / 79 | 79 / 79 | `46c7155751e6` / `46c7155751e6` | **PASS** ‡ |
| 16 | `home-automation/matter-server` | 0:0 | Succeeded | Succeeded | 161 | 161 / 161 | 161 / 161 | `364f08cdd3bb` / `364f08cdd3bb` | **PASS** |
| 17 | `home-automation/zigbee2mqtt-data` | 2000:2000 | Succeeded | Succeeded | 37 | 37 / 37 | 37 / 37 | `1a3f40e9218d` / `1a3f40e9218d` | **PASS** |
| 18 | `media/calibre-web-automated` | 2000:2000 | Succeeded | Succeeded | 37 | 23 / 23 | 23 / 23 | `d06f7d879f11` / `d06f7d879f11` | **PASS** ‡ |
| 19 | `media/plex` | 2000:2000 | Succeeded | Succeeded | 21251 | 21251 / 21251 | 21251 / 21251 | `9b8b4a197241` / `9b8b4a197241` | **PASS** |
| 20 | `media/seerr` | 2000:2000 | Succeeded | Succeeded | 75 | 75 / 75 | 75 / 75 | `6a95dbce8b79` / `6a95dbce8b79` | **PASS** |
| 21 | `media/tdarr-config` | 2000:2000 | Succeeded | Succeeded | 17278 | 17278 / 17278 | 17278 / 17278 | `b29eb20847d8` / `b29eb20847d8` | **PASS** |
| 22 | `selfhosted/changedetection-config` | 1000:1000 | Succeeded | Succeeded | 3069 | 3069 / 3069 | 3069 / 3069 | `ab4e633a8227` / `ab4e633a8227` | **PASS** |
| 23 | `selfhosted/linkwarden` | 1000:1000 | Succeeded | Succeeded | 67 | 67 / 67 | 67 / 67 | `b42975d15de3` / `b42975d15de3` | **PASS** |
| 24 | `selfhosted/n8n` | 1000:1000 | Succeeded | Succeeded | 7725 | 7725 / 7725 | 7725 / 7725 | `cd0fae1c2aff` / `cd0fae1c2aff` | **PASS** |
| 25 | `selfhosted/ntfy` | 1000:1000 | Succeeded | Succeeded | 2 | 2 / 2 | 2 / 2 | `027268e78c5f` / `027268e78c5f` | **PASS** \* |
| 26 | `selfhosted/obsidian-livesync` | 5984:5984 | Succeeded | Succeeded | 8 | 8 / 8 | 8 / 8 | `88858eb0b4e1` / `88858eb0b4e1` | **PASS** \* |
| 27 | `selfhosted/paperless-ngx` | 1000:1000 | Succeeded | Succeeded | 32 | 32 / 32 | 32 / 32 | `b7d4afdbcc1e` / `b7d4afdbcc1e` | **PASS** |
| 28 | `selfhosted/paperless-ngx-media` | 1000:1000 | Succeeded | Succeeded | 1 | 1 / 1 | 1 / 1 | `f6a6a1ecf1b4` / `f6a6a1ecf1b4` | **PASS** \* |
| 29 | `selfhosted/syncthing` | 1000:1000 | Succeeded | Succeeded | 21 | 21 / 21 | 21 / 21 | `05d36861232d` / `05d36861232d` | **PASS** |
| 30 | `selfhosted/syncthing-data` | 1000:1000 | Succeeded | Succeeded | 5 | 5 / 5 | 5 / 5 | `c525096cb0b4` / `c525096cb0b4` | **PASS** \* |

29 of 30 rows were byte-identical across destinations. Row 8 was not, for the
reason below. All 30 met: zero entries unreadable at the mover identity;
`filesNew` equal to the restored count (and to live, minus `CACHEDIR.TAG` on
the three `‡` rows); a real scratch restore rather than a status field.
`SecurityContextCompatible` was not a gate.

## Corrections after the table

- **Row 5** `downloads/autobrr`: the app was removed. The kopia snapshots were
  kept (`Retain`). There is no live claim. Do not treat the row as a current
  volume.
- **Row 8** `downloads/prowlarr-config`: the first transcription said mover
  `3002:3002`. Live policy, overlay, `EXPECTED_IDENTITY` and the Deployment
  all say `3002:3000`. The re-drill at that identity restored 710/710 with no
  unexplained stable-set gaps, but the two destinations resolved **different
  snapshots** (`offset: 0` picked up a newer scheduled ceph snapshot), so the
  trees were not destination-identical, and restored ownership is the mover
  (`3002:3000`) rather than live `1000:3000`. A later matched-pair drill
  (`prowlarr-ceph-w2ver` / `prowlarr-r2-w2ver`) closed that gap: both sides
  713 files / 56,777,475 bytes, content digest `576321fa…3578`, mode+uid+gid
  digest `31d7ffd5…9530`. The trick was a churn probe, then both snapshots
  inside the gap between scheduled ceph bursts, then checking
  `.status.resolved.kopiaSnapshotID` is the verification snapshot.
- **Row 19** `media/plex`: this row is the fleet drill, not the later
  standing-10Gi r2 proof. That proof, and what it does not cover, is in
  [restore-and-cache.md](restore-and-cache.md). The standing populator path
  was not exercised.
- **Hermes cache** after this table was raised 5Gi (frozen populator) to 16Gi
  (r2-proven) to 48Gi (sized above the proof). The table's file counts are the
  drill, not the current tree size.

## Near-empty claims (the `*` set, plus two that are not thin)

Five claims were called too small. They split into two disjoint sets. Do not
collapse them into one count.

**Cannot be given a deeper proof** (the volume holds essentially nothing):

| Claim | At the later re-measure |
|---|---|
| `downloads/autobrr` | 1 file / 2,179 B. App since removed |
| `selfhosted/paperless-ngx-media` | 1 file / 0 B. Paperless holds no documents |
| `selfhosted/syncthing-data` | 5 files / 531 B. `.stfolder` markers |

**Already complete** (small, and the proof covers 100% of real content):

| Claim | At the later re-measure |
|---|---|
| `selfhosted/ntfy` | 2 files / 188,416 B, including `auth.db` |
| `selfhosted/obsidian-livesync` | 8 files / 574,930 B. A real CouchDB vault |

Each of those five restored identically from both destinations in content and
in mode/uid/gid, and had not grown between the fleet proof and the
re-measure. The forward risk on the two dual-engine leftovers is cache
capacity, not fidelity. `pgadmin` (row 4) is the opposite edge: 3 files and
979 MiB, so its restore did move bulk data.

## How to read a later backup

- kopia omits `CACHEDIR.TAG` contents. Fewer files than live can be correct
  when `filesNew` equals the restored count.
- Do not infer VolSync completeness from a kopiur failure, or the reverse.
  VolSync stages writable and `fsGroup` adds group-read before restic opens
  the file, so a uid mismatch can still produce a complete restic backup
  (restored modes come back relaxed: `600` to `660`). kopiur stages read-only
  and fails closed. Measured complete on `changedetection-config`: 2292
  mode-`0600` root-owned files, restic restore byte-identical.
- A VolSync restore is not a tool for reproducing a permissions bug. kopiur
  restores reproduce original modes.
- Reading live bytes through the app pod can include mounts that are not on
  the claim. See [restore-and-cache.md](restore-and-cache.md).
