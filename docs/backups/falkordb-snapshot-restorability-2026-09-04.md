# Is a volume-level snapshot of FalkorDB restorable? (2026-09-04)

Measured while onboarding `database/falkordb`. The question had to be settled **before** the app
shipped, not during a restore: kopiur takes a read-only CSI `VolumeSnapshot` with **no application
hook** (`copyMethod: Snapshot`), so whatever FalkorDB has not already fsynced to the block device
is simply not in the backup - and Redis-lineage engines report a successful start either way.

**Answer: yes, and only because `appendonly yes` + `appendfsync everysec` is set on the
deployment.** With the image's stock durability the same snapshot loses 91% of recent writes and
still starts clean. The setting is therefore load-bearing for the backup, not a tuning preference.

## Result

| Restore of the *same* crash-consistent snapshot | Nodes recovered | Loss |
|---|---|---|
| live graph at the instant the snapshot was taken | 2199 | - |
| `appendonly yes`, `appendfsync everysec` (what ships) | **2174** | 25 nodes (~1%), the unfsynced tail |
| `appendonly no` - RDB only (the image's stock behaviour) | **200** | 1999 nodes (**91%**) |

Both restores reported a completely healthy startup. Nothing in the pod status, the probes, or the
Redis log distinguishes the second row from the first - which is exactly why this is written down.

## Why the two rows differ

`REDIS_ARGS` is spliced verbatim into the image entrypoint's `exec redis-server` line
(`/var/lib/falkordb/bin/run.sh`, v4.20.4), which is the supported way to configure the server.

- **RDB only.** The newest durable state is the last completed `save` point. At restore time the
  standalone `dump.rdb` was **158 seconds old** and held the graph as it stood at 200 nodes. A
  block snapshot cannot be more current than the last completed save, no matter when it is taken.
  RDB is written to a temp file and renamed, so the result is *stale*, never corrupt - which is
  the dangerous shape: it restores silently and looks fine.
- **AOF at `everysec`.** Writes reach the device within ~1s, so the snapshot holds all but the
  sub-second tail. A torn final record is expected and handled by Redis's own
  `aof-load-truncated yes` (confirmed live as the effective default). The restore log shows the
  normal two-stage load with no truncation warning and no repair:

```
* Reading RDB base file on AOF loading...
* RDB is base AOF
* DB loaded from base file appendonly.aof.1.base.rdb: 0.000 seconds
* DB loaded from incr file appendonly.aof.1.incr.aof:  0.008 seconds
* Ready to accept connections tcp
```

The `save` points are kept as a second line of defence: they bound AOF replay time at start and
leave a self-contained `dump.rdb` inside the snapshot.

## How it was measured

Scratch namespace `falkordb-probe`, torn down afterwards (verified: no namespace, no leftover PVs).
The pod spec was the **rendered output of this app's own HelmRelease** - same image digest, same
`securityContext`, same 20Gi `ceph-block` claim - so the measurement describes what ships.

1. Wrote a graph, forced one `BGSAVE` (the 200-node RDB point above).
2. Started a continuous writer, and took a `csi-ceph-blockpool` `VolumeSnapshot`
   **mid-write** - the same snapshot class kopiur uses. Live count at that instant: 2199.
3. Restored the snapshot to a PVC, then **copied the files out into a fresh empty volume** rather
   than starting from the block clone. That is what a kopia restore actually does, and it proves
   the recovered *file set* is self-sufficient. All five entries came back with identical size,
   ownership and mode.
4. Started FalkorDB on the copied volume and queried it. Then repeated with `--appendonly no`
   against the identical snapshot to get the counterfactual row.

## Mover identity (the other thing this run settled)

kopiur stages its source **read-only** and gets no kubelet `fsGroup` fixup, so an identity mismatch
fails the backup **closed** - it does not degrade. Measured on the volume FalkorDB actually wrote,
not inferred from the pod spec:

```
/var/lib/falkordb/data                                   0:1000  2775   <- CSI volume root
/var/lib/falkordb/data/dump.rdb                       1000:1000   644
/var/lib/falkordb/data/appendonlydir                  1000:1000  2755
  .../appendonly.aof.1.base.rdb                       1000:1000   644
  .../appendonly.aof.1.incr.aof                       1000:1000   644
  .../appendonly.aof.manifest                         1000:1000   644
/var/lib/falkordb/data/lost+found                        0:1000  2770   <- ext4's own
```

`0 unreadable` and `0 non-traversable directories` at uid 1000. Redis runs single-process with a
`0022` umask and never drops privileges, so ownership simply follows the pod's
`runAsUser`/`fsGroup`. The component default of `1000:1000` is correct here, so the overlay
declares **no** `KOPIUR_PUID`/`KOPIUR_PGID` override - a redundant one would be a second,
unenforced identity declaration (`AGENTS.md`, the `APP_UID`/`APP_GID` liability).

The two `0:1000` entries are the CSI-provisioned volume root and ext4's `lost+found`; both are
group-readable and group-traversable, and neither is a FalkorDB file. `selfhosted/paperless-ngx`
records the same pair for the same reason.

## Writability, proven rather than inferred

The repo has twice shipped an app that could not write its own claim while every probe stayed
green (`downloads/autobrr`, ~11 months). Checked directly on the running pod:

- `id` -> `uid=1000 gid=1000 groups=1000`
- `test -w /var/lib/falkordb/data` -> writable
- an actual file created and removed at the mount root
- and, conclusively, six real data files written by FalkorDB itself

The mechanism is the `fsGroup: 1000` in `defaultPodOptions`: both Ceph CSI drivers are
`fsGroupPolicy: File`, so the kubelet applies the ownership walk and the volume root becomes
`0:1000` mode `2775`. Without a matching `fsGroup` this claim would be root-owned and the app would
fail exactly the way autobrr did.

## What would change this answer

- Removing `appendonly yes` from `REDIS_ARGS`, or moving `appendfsync` to `no`. Either turns the
  91% row back on, silently.
- Adding a FalkorDB replica or any second writer. This is a single-writer `ReadWriteOnce` claim and
  the deployment is `strategy: Recreate` for that reason.
- kopiur growing an application hook (a `BGSAVE`/`FLUSHALL`-style pre-snapshot step). It has none
  today, which is the whole premise above.
