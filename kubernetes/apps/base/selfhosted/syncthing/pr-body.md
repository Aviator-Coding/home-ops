## Summary

Make Syncthing actually useful on the home-ops cluster (`selfhosted`): purpose-named folders shared with the Mac, stable self-device name, and a right-sized `syncthing-data` PVC (100Gi → 15Gi) with matching VolSync destination capacity. Folder/device state is runtime config on the 1Gi config claim (not GitOps); Git only carries the 15Gi data PVC and backup substitutes.

## Folder layout and types

| Folder | Path | Type | Reasoning |
| ------ | ---- | ---- | --------- |
| `documents` / Documents | `/var/syncthing/data/documents` | **sendreceive** | Two-way active editing. |
| `screenshots` / Screenshots | `/var/syncthing/data/screenshots` | **receiveonly** | Mac is source of truth; cluster-side deletions must never propagate back and destroy originals. |
| `camera-roll` / Camera Roll | `/var/syncthing/data/camera-roll` | **receiveonly** | Same as screenshots; no phone paired yet (pairing is a separate captain step). |
| `projects` / Projects / Code Scratch | `/var/syncthing/data/projects` | **sendreceive** | Active two-way working set. |

- The old **`default`** folder was removed via the Syncthing REST API.
- The empty-looking `id=''` / `path='~'` entry from `GET /rest/config/folders` is Syncthing's normal per-install `<defaults>` XML template block, **not** a second real folder — nothing to delete there beyond retiring `default`.
- Self device `JGKUDZA…` renamed from stale pod name `syncthing-7f9bb86b55-pj8b9` → stable **`syncthing-cluster`** so the label does not rot on pod replace.
- Mac pairing (`LCJDCP7…` / `Sascha-Mac-Air-M5`) was **preserved** throughout (never unpaired or re-keyed). The 1Gi **`syncthing` config PVC was not touched**.

## PVC right-size (100Gi → 15Gi)

Kubernetes cannot shrink a PVC, so the claim was **deleted and recreated** after suspending Flux, confirming no VolSync/kopiur mover was running, and scaling the Deployment to 0.

**Sizing (~15Gi, not a round guess):** ~2Gi Documents, ~2Gi Screenshots, ~4Gi Projects/code-scratch, ~5Gi camera-roll headroom (no phone yet — only real growth upside), ~2Gi buffer. `allowVolumeExpansion` can grow later; shrinking again is not supported.

**Important:** the live PVC had **no `dataSourceRef`**, so recreate produced an **empty** volume (not restore-populated). Content was only 28 KB of disposable marker data — stated plainly, not glossed over.

Git changes:

- `kubernetes/apps/base/selfhosted/syncthing/app/pvc.yaml` → `storage: 15Gi`
- `kubernetes/apps/main/selfhosted/syncthing.yaml` second Kustomization: `VOLSYNC_CAPACITY: 15Gi` (sizes the **ReplicationDestination** restic destination volume for future restore room — **not** the live app PVC). Caches stay **5Gi** (33% of 15Gi, within 20–50%).

## Live verification

Performed on the live cluster (real kubeconfig) before opening this PR. Device IDs truncated; **no API keys, GUI password hashes, or device secrets**.

### Folders and Mac shares

Verified via `GET /rest/config/folders` on the running pod:

- `documents` (sendreceive), `screenshots` (receiveonly), `camera-roll` (receiveonly), `projects` (sendreceive)
- Each shared with both the self device and the Mac device `LCJDCP7…` (`Sascha-Mac-Air-M5`)
- `default` removed via `DELETE /rest/config/folders/default` and confirmed absent afterward

### Self-device rename

Self device `JGKUDZA…` renamed from `syncthing-7f9bb86b55-pj8b9` to `syncthing-cluster` via `PATCH /rest/config/devices/{id}`, confirmed via `GET /rest/config/devices`.

### PVC recreate

- `syncthing-data` deleted at 100Gi (28 KB used, no `dataSourceRef` present so nothing was lost)
- Recreated and confirmed **Bound at 15Gi**
- Syncthing pod confirmed **Running/Ready** afterward on the new volume

### Backup proof on the recreated 15Gi claim

- VolSync `ReplicationSource` `syncthing-data-ceph` manually triggered via `spec.trigger.manual`; `status.lastManualSync` matched the token; `status.latestMoverStatus.result=Successful`
- Manual kopiur Snapshot CR `syncthing-data-ceph-resize-verify` (`policyRef: syncthing-data-ceph`) reached `status.phase=Succeeded` with `status.stats={filesNew:5, ...}` — **left in the cluster permanently** per the never-delete-a-kopiur-Snapshot-CR rule (deleting the CR deletes backup data via finalizer)

### Mac-offer proof

- Wrote a test file into the `documents` folder on the cluster side, triggered a rescan
- `GET /rest/db/completion?device=<mac>&folder=documents` showed `needBytes`/`needItems` reflecting the new file
- `GET /rest/system/connections` showed the Mac device `connected: true`
- i.e. the file is **actively offered** to the Mac; completing the sync requires the captain to accept the share in the Mac's own Syncthing UI (manual step)

Durable copy of this evidence also lives in-tree:
`kubernetes/apps/base/selfhosted/syncthing/app/README.md`.

## ⚠️ Required immediately after merge

### Resume the suspended Flux Kustomization

`selfhosted/syncthing` is **deliberately left suspended** until this PR merges. That is **not** an oversight: git `main` still declared the PVC at 100Gi, and resuming before merge would make Flux server-side-apply patch the live (correct) **15Gi** PVC storage back up to **100Gi** — a legal, silent PVC grow that undoes the resize.

While suspended, dependents `syncthing-data` and `syncthing-data-kopiur` show `Ready=False` with a `dependency revision not up to date` message. That is **expected, self-healing** Flux dependency behavior (no pruning, no data loss) and clears the moment `syncthing` is resumed and healthy again.

```bash
flux resume kustomization syncthing -n selfhosted
```

### Other captain-side manual steps (out of GitOps scope)

1. **Accept each folder share** on the Mac Syncthing UI (cluster already offers; Mac must accept).
2. **Pair a phone** when `camera-roll` should have a source (currently receiveonly with no source device).

## Constraints respected

- Config PVC (`syncthing`, 1Gi) size/identity untouched
- Mac pairing preserved (no unpair/re-key)
- No kopiur Snapshot CR deleted
- Touch only syncthing-related resources in `selfhosted`
- No Syncthing API key, GUI password hash, or device secret in the diff, history, or this body
