# Runbook: OSD stuck after restart/reboot (device-path drift)

When a `rook-ceph-osd-N` pod won't come back after a restart or node reboot — most
dangerously *while the cluster is already degraded*. This is a **known, `wontfix` Rook
limitation**, not a disk failure.

> Quick gate before any planned reboot / OSD restart:
> ```bash
> task rook:check-osd-device-paths
> ```
> It lists per-OSD path drift and **exits non-zero unless Ceph is HEALTH_OK** (the real
> risk condition). Don't reboot a node or restart an OSD unless it's green.

## Symptom

- An OSD pod sits in `Init:0/5` and never becomes Ready.
- Its `activate` init container logs loop:
  ```
  no disk found with OSD ID N
  + ceph-volume raw list
  ```
- `ceph status` shows `1 osds down` and (if it was a metadata-pool OSD) blocked MDS
  requests / inactive PGs.

## Root cause

Rook bug [rook/rook#17224](https://github.com/rook/rook/issues/17224) (`wontfix`, since
v1.6). For **raw-mode** OSDs Rook resolves the stable `/dev/disk/by-id/...` device we set
in the CephCluster CR down to a **kernel name** (`/dev/nvme0n1`) and bakes *that* into the
OSD deployment's `ROOK_BLOCK_PATH`. NVMe kernel names reshuffle across reboots/re-probes
(`nvme0n1` ↔ `nvme1n1`), so the stored path drifts and points at the **wrong disk**.

Rook's safety net is the activate script's "relocate" fallback: if the stored path holds
the wrong OSD, it runs a full `ceph-volume raw list` to find the OSD on any disk. That
fallback **works under normal load** — but it returns empty when the cluster is heavily
loaded/wedged, which is exactly when the 2026-06-14 incident restarted an OSD. See also
[rook/rook discussion #7796](https://github.com/rook/rook/discussions/7796).

> Why we don't "just fix the config": the CephCluster CR **already** uses by-id device
> names, and **raw mode is the recommended Rook default** — this layout *is* best practice.
> Rook simply doesn't use the by-id path for `ROOK_BLOCK_PATH`; no config toggle changes
> that. There is **no best-practice "permanent fix"** — the clean fix is the upstream code
> change (#17224, `wontfix`). Migrating raw→LVM would dodge the naming issue but trades
> *against* best practice (LVM is legacy, reserved for encryption + `metadataDevice`, and
> carries its own LVM-tag/metadata-corruption risk), so it's a last resort, not a fix.

## Diagnosis

1. Audit which OSDs are drifted and whether it's safe to act:
   ```bash
   task rook:check-osd-device-paths
   ```
   `STALE` = deploy `ROOK_BLOCK_PATH` ≠ the disk the running OSD uses. Stale-but-running
   is harmless; it only bites on that OSD's *next* restart.

2. Confirm which physical disk actually holds the OSD (ground truth = the BlueStore
   label). Run from any healthy OSD pod on the same node (git-bash needs
   `MSYS_NO_PATHCONV=1` or `/dev/...` gets mangled to a Windows path):
   ```bash
   MSYS_NO_PATHCONV=1 kubectl -n rook-ceph exec <healthy-osd-pod> -c osd -- \
     ceph-bluestore-tool show-label --dev /dev/nvmeXn1 | grep whoami
   ```
   The disk whose label `whoami` == the stuck OSD id is the correct device.

3. Read the stuck pod's activate log to confirm the loop:
   ```bash
   kubectl -n rook-ceph logs <stuck-osd-pod> -c activate --tail=20
   ```

## Recovery

### Case A — one OSD stuck, the others up (the common case)

Point the deployment at the **correct current kernel name**, then bounce the stuck pod.

1. Find the correct device (the disk whose BlueStore `whoami` == N, from Diagnosis #2),
   e.g. `/dev/nvme0n1`.
2. Patch `ROOK_BLOCK_PATH` (strategic merge → index-independent):
   ```bash
   kubectl -n rook-ceph patch deploy rook-ceph-osd-N --type=strategic -p \
     '{"spec":{"template":{"spec":{"initContainers":[{"name":"activate","env":[{"name":"ROOK_BLOCK_PATH","value":"/dev/nvme0n1"}]}]}}}}'
   ```
3. OSD deployments use `strategy: Recreate`, so the new pod only spawns once the old one
   is gone. If the old pod is stuck `Terminating`, force-delete it:
   ```bash
   kubectl -n rook-ceph delete pod <stuck-osd-pod> --grace-period=0 --force
   ```
4. Watch it activate: `kubectl -n rook-ceph logs <new-pod> -c activate` should print
   `ceph-volume raw activate successful for osd ID: N`, then `ceph osd tree` shows it
   `up`. It auto-marks `in` if it was auto-marked `out`.

> The operator may revert the patched path on a later reconcile — that's fine, the pod
> has already activated. The path will be stale again after the next reboot (expected).

### Case B — all OSDs on a node down after a reboot

Fix **one** OSD's path as in Case A and let it start. Rook can then re-detect and update
the sibling OSD deployments on that node automatically (per discussion #7796). If it
doesn't, repeat Case A per OSD.

### Co-symptom — stuck peering / blocked MDS requests

If PGs are stuck `peering`/`activating` and a `ceph pg <pgid> query` hangs, the primary
OSD's PG state machine is wedged. Force a clean re-peer **without** a pod restart or disk
re-read (so device-path drift is irrelevant):
```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd down osd.X
```
The OSD re-asserts itself `up` within seconds and re-peers. Safer than a pod restart when
paths are stale.

## Verify

```bash
# relocate fallback works again (returns this node's OSDs, not empty):
MSYS_NO_PATHCONV=1 kubectl -n rook-ceph exec <healthy-osd-pod> -c osd -- ceph-volume raw list
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd tree     # target OSD up/in
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status       # HEALTH_OK
```

## Prevention

- **Before** `just talos upgrade-node` / `reboot-node` / `reset-node` (each reboots that
  node's OSDs): confirm `task rook:check-osd-device-paths` is green (HEALTH_OK). Reboot
  one node at a time and wait for HEALTH_OK before the next — a healthy cluster lets the
  relocate fallback self-heal each OSD on boot.
- Don't restart an OSD or reboot a node while the cluster is degraded. If you must, expect
  to apply Case A.
- **Ceph/Rook upgrades** (Renovate bumps) restart OSDs via the *operator*, not the kubelet.
  `cephClusterSpec.osdMaxUpdatesInParallel: 1` is set in `cluster/helmrelease.yaml` so the
  operator rolls OSDs **one at a time** (default `20` ≈ all 6 at once) — each restart then
  self-heals against an otherwise-healthy cluster.
- **Keep these Rook settings OFF** (they're at safe defaults): `upgradeOSDRequiresHealthyPGs`
  (can deadlock with #17224 — a stuck OSD keeps PGs unhealthy, which blocks the update that
  would fix it), `removeOSDsIfOutAndSafeToRemove` (could auto-**purge** a recoverable
  stale-path OSD), `skipUpgradeChecks` / `continueUpgradeAfterChecksEvenIfNotHealthy`.

## Related

- Incident + the trigger we removed: SABnzbd tiny-file flood moved off CephFS to
  ceph-block (PR #983) → see `docs/ceph-cluster-changelog.md`.
- Proper fix is upstream (Rook #17224, `wontfix`); raw + by-id is already the
  best-practice layout. raw→LVM is a last-resort workaround only (see the root-cause note).
