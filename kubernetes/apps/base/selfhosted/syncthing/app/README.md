# syncthing

Cluster Syncthing (`selfhosted`), paired with the captain's Mac
(`Sascha-Mac-Air-M5`). Folder share state and device names live in Syncthing's
config on the **1Gi `syncthing` claim** (device identity / pairing) — that is
runtime state on the config PVC, not GitOps. This tree only owns the
HelmRelease and the **`syncthing-data`** claim for synced files.

## Volumes

| Claim | Size | Purpose |
| ----- | ---- | ------- |
| `syncthing` | 1Gi | Config, device identity, pairing. **Do not resize or delete.** |
| `syncthing-data` | 15Gi | Synced folder roots under `/var/syncthing/data/`. |

`VOLSYNC_CAPACITY` on the `syncthing-data` Flux Kustomization sizes the
VolSync **ReplicationDestination** restic volume (restore room), not the live
PVC. The plain PVC in [`pvc.yaml`](pvc.yaml) is the live claim size. Both are
15Gi; caches stay 5Gi (≈33%, within the 20–50% convention).

### Why 15Gi (2026-08-31)

Right-sized from 100Gi that held only 28 KB. Per-folder budget: ~2Gi Documents,
~2Gi Screenshots, ~4Gi Projects/code-scratch, ~5Gi camera-roll headroom (no
phone paired yet — the only folder with real growth upside), ~2Gi buffer.
Kubernetes cannot shrink a PVC, so the claim was deleted and recreated (safe:
no `dataSourceRef`, trivial disposable content). Growing later is fine;
shrinking again is not.

## Folder types (runtime; not in Git)

| Folder id | Type | Why |
| --------- | ---- | --- |
| `documents` | sendreceive | Two-way active editing. |
| `screenshots` | receiveonly | Mac is source of truth; cluster-side deletes must not destroy originals. |
| `camera-roll` | receiveonly | Same as screenshots; no phone paired yet (separate captain step). |
| `projects` | sendreceive | Two-way working set / code scratch. |

The old `default` folder was removed. The empty-looking `id='' path='~'` entry
from GET `/rest/config/folders` is Syncthing's normal per-install `<defaults>`
template block, not a second real folder.

## Live verification (2026-08-31)

Recorded against the live cluster before this change landed in Git. Sandbox CI
has no kubeconfig; this section is the durable proof that outlives the PR.
Device IDs are truncated; no API keys, GUI password hashes, or device secrets.

### Folders and Mac shares

Verified via `GET /rest/config/folders` on the running pod:

- `documents` (sendreceive), `screenshots` (receiveonly), `camera-roll`
  (receiveonly), `projects` (sendreceive)
- Each shared with both the self device and the Mac device `LCJDCP7…`
  (`Sascha-Mac-Air-M5`)
- `default` removed via `DELETE /rest/config/folders/default` and confirmed
  absent afterward

### Self-device rename

Self device `JGKUDZA…` renamed from the stale pod name
`syncthing-7f9bb86b55-pj8b9` to stable `syncthing-cluster` via
`PATCH /rest/config/devices/{id}`, confirmed with `GET /rest/config/devices`.

### PVC recreate

- `syncthing-data` deleted at 100Gi (28 KB used, **no** `dataSourceRef` — recreate
  yields an empty volume, not a backup-populated one; nothing real was lost)
- Recreated and confirmed **Bound at 15Gi**
- Syncthing pod **Running/Ready** on the new volume afterward

### Backup proof on the recreated 15Gi claim

- VolSync `ReplicationSource` `syncthing-data-ceph`: manual
  `spec.trigger.manual`; `status.lastManualSync` matched the token;
  `status.latestMoverStatus.result=Successful`
- kopiur Snapshot CR `syncthing-data-ceph-resize-verify`
  (`policyRef: syncthing-data-ceph`): `status.phase=Succeeded` with
  `status.stats` including `filesNew: 5` (non-zero). **Left in the cluster
  permanently** — never delete a kopiur Snapshot CR (finalizer deletes backup
  data)

### Mac offer proof

- Test file written into `documents` on the cluster side; rescan triggered
- `GET /rest/db/completion?device=<mac>&folder=documents` showed
  `needBytes` / `needItems` for the new file
- `GET /rest/system/connections` showed the Mac device `connected: true`
- Completing the sync still requires the captain to **accept the share in the
  Mac Syncthing UI** (manual, out of scope for GitOps)

## Flux suspend during the resize window

While live `syncthing-data` was already 15Gi and git still declared 100Gi,
`selfhosted/syncthing` was **deliberately suspended** so Flux would not
server-side-apply a silent legal grow back to 100Gi. Dependents
`syncthing-data` and `syncthing-data-kopiur` may show `Ready=False` /
`dependency revision not up to date` in that window — expected, self-healing,
no prune or data loss; clears when `syncthing` is resumed after this change is
on `main`:

```bash
flux resume kustomization syncthing -n selfhosted
```

## Remaining captain-side manual steps

1. Accept each folder share on the Mac Syncthing UI (cluster already offers).
2. Pair a phone when `camera-roll` should have a source.
3. Resume the Flux Kustomization (command above) if it is still suspended.
