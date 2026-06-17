# Runbook: OSD crash-loops on startup (BlueStore OMAP / local-store corruption)

When a `rook-ceph-osd-N` pod crash-loops because the OSD process **aborts during startup**
while reading its own PGs — i.e. the local BlueStore store is logically corrupt. This is a
**different failure** from device-path drift (see
[`osd-device-path-recovery.md`](./osd-device-path-recovery.md)): there the pod is stuck
`Init:0/5` and never finds its disk; here the disk is found, RocksDB opens, and the OSD
*then* dies reading metadata.

> First triage: which failure is it?
> - **`Init:0/5`, activate log loops `no disk found with OSD ID N`** → device-path drift,
>   use the other runbook (non-destructive patch).
> - **Pod `CrashLoopBackOff`/`Error`, `osd` container starts then aborts in `load_pgs`** →
>   this runbook (local store is corrupt; rebuild from peers).

## Symptom

- `kubectl -n rook-ceph get pods -l app=rook-ceph-osd` shows the OSD pod `1/2`
  `CrashLoopBackOff`/`Error` with a high restart count; it dies every ~5 min.
- `ceph status` → `N osds: (N-1) up`, one OSD `down`+`out`, and `RECENT_CRASH` piling up
  for that one OSD.
- The OSD log (`kubectl -n rook-ceph logs <pod> -c osd`) shows RocksDB opening
  (`_open_db opened rocksdb`) and then `*** Caught signal (Aborted) **` with this stack:
  ```
  OSD::init() → OSD::load_pgs() → PG::read_state() → BlueStore::omap_iterate() → abort()
  ```
- `ceph crash info <id>` for that OSD shows the same `omap_iterate` / `read_state` /
  `load_pgs` backtrace, aborting via libstdc++ `terminate` (an **uncaught C++ exception**,
  not a `ceph_assert`).

## Root cause

A **decode failure of a corrupt pg_log/pg_info OMAP record** in that OSD's local RocksDB.
`BlueStore::omap_iterate` is Tentacle's lightweight OMAP iterator (Ceph PR #61363, default
in 20.2.x) — it is the *messenger* that reads the bad record during PG load, not the cause.
Because the OSD must load all its PGs to start, **no config flag can boot it past the bad
record**, and `ceph-bluestore-tool fsck/repair` fixes allocator/structural metadata but
**cannot fix an undecodable OMAP value**.

This is corruption local to one OSD's store (other replicas are independent and stay
`active+clean`). On this cluster the likely trigger is the **consumer no-PLP NVMe** taking
an unclean metadata write under a prior load spike — the standing fragility tracked as the
P0 PLP backlog item in [`ceph-cluster-changelog.md`](../ceph-cluster-changelog.md). The
disk itself is typically SMART-healthy (confirm with `ceph device ls` wear / SMART — a
healthy disk means rebuild-in-place is safe; a failing disk means replace the hardware).

## Pre-flight — confirm it's safe to rebuild

The fix throws away this OSD's local copy and rebuilds it from peers, so **redundancy must
be intact first**:

```bash
# All other PGs active+clean, disk healthy, and Ceph agrees it's safe:
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status            # only the 1 OSD down; PGs active+clean
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph device ls         # the OSD's disk wear/health
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd safe-to-destroy osd.N
```
Proceed only if `safe-to-destroy` says *"safe to destroy without reducing data durability"*.
If this is the **last** copy of any PG, STOP — that's a `ceph-objectstore-tool` PG-surgery
case, not this runbook. No node reboot is involved here (avoids device-path drift).

## Recovery — destroy + recreate the OSD, backfill from peers

Replace `N` and `<by-id-disk>` with the affected OSD and its disk
(`ceph osd metadata N | grep device_ids`; map to the by-id path in
`.taskfiles/rook/Taskfile.yaml` / the CephCluster HR).

1. **Pause the operator** so it doesn't fight the manual purge:
   ```bash
   kubectl -n rook-ceph scale deploy rook-ceph-operator --replicas=0
   ```
2. **Remove the crashing OSD** (deployment + cluster membership):
   ```bash
   kubectl -n rook-ceph scale deploy rook-ceph-osd-N --replicas=0
   kubectl -n rook-ceph delete deploy rook-ceph-osd-N
   kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd purge N --yes-i-really-mean-it
   ```
3. **Wipe the disk** so the operator sees it as blank (the corrupt BlueStore label survives
   until wiped; otherwise the operator re-activates the corrupt store). Wipe **only that one
   disk** — never `task rook:wipe-talos-X` (that hits every OSD on the node):
   ```bash
   task rook:reset-disk disk="<by-id-disk>" node="talos-X" --yes
   ```
   > On Windows the `reset-disk` task can choke on its `envsubst < <(...)` process
   > substitution + interactive prompt. Equivalent manual path: render
   > `.taskfiles/rook/templates/WipeDiskJob.tmpl.yaml` (substitute `${job}/${node}/${disk}`)
   > and `kubectl apply -f` it; it runs `ceph-volume lvm zap --destroy` + `wipefs -af` +
   > `sgdisk --zap-all` + `blkdiscard -f` + `dd`. Confirm the after-`wipefs` output is empty
   > (no `ceph_bluestore` signature).
4. **Resume the operator** — it runs the `osd-prepare-talos-X` job, `ceph-volume raw create`
   on the blank disk, and a fresh `rook-ceph-osd-N` (reusing the lowest free ID) joins:
   ```bash
   kubectl -n rook-ceph scale deploy rook-ceph-operator --replicas=1
   ```
5. **Backfill** then refills the new OSD from peers. Watch:
   ```bash
   kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd tree     # osd.N up/in
   kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph pg stat      # remapped+backfilling → active+clean
   ```
   PGs should be `active+remapped+backfilling`, **not** `degraded` (peers keep full
   redundancy throughout). If the no-PLP OSDs (e.g. osd.3/osd.6) wedge on slow ops during
   backfill, pace it: `ceph config set osd osd_max_backfills 1` (mClock per-OSD IOPS caps
   already apply).

## Verify

```bash
kubectl -n rook-ceph get pods -l app=rook-ceph-osd -o wide               # osd-N 2/2 Running, 0 restarts
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status          # HEALTH_OK, all active+clean, all OSDs up+in
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph crash ls-new    # no NEW crash for osd.N
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph crash archive-all  # clear the stale RECENT_CRASH warning
```
After it settles, run a `ceph osd deep-scrub` sweep and watch `ceph crash ls` for the same
`omap_iterate` signature on **any other** OSD — a repeat elsewhere upgrades this from
"isolated" to "systemic" (suspect a common substrate) and warrants an upstream tracker
report with the `ceph crash info` dump.

## Prevention

- This is store corruption, not a config bug — the durable fix is the underlying hardware:
  **power-loss-protection drives** (P0 backlog in
  [`ceph-cluster-changelog.md`](../ceph-cluster-changelog.md)). Until then, avoid driving
  the no-PLP OSDs into sustained slow-op territory (the SABnzbd tiny-file flood that caused
  metadata storms was already moved off CephFS in PR #983).
- Keep `removeOSDsIfOutAndSafeToRemove` **OFF** — it would auto-purge a `down`+`out` OSD,
  removing your chance to inspect it first.

## Related

- Device-path drift (the *other* OSD-won't-start failure): [`osd-device-path-recovery.md`](./osd-device-path-recovery.md)
- Change log + PLP backlog: [`../ceph-cluster-changelog.md`](../ceph-cluster-changelog.md)
- Hardware failures: [`../hardware-incidents.md`](../hardware-incidents.md)
