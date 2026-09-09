# FalkorDB memory: the 2026-09-09 outage, and how the ceiling is sized

`database/falkordb` crash-looped on 2026-09-09 with the **8Gi limit correctly
applied**. This is the record of why that limit was nonetheless the cause, how
service was restored, and how the three memory numbers are derived so the next
person can re-derive them rather than copy them.

The short version: **the limit was not misconfigured, it was outgrown.** An
ingestion quadrupled the graph, and the AOF-rewrite fork that runs shortly after
every boot then could not fit beside it.

---

## 1. What happened

All times UTC. Pod `falkordb-54949ff5b4-f994s`, container `app`, on `talos-2`.

| Time | Event |
|---|---|
| 2026-09-08 23:23:07 | pod starts; working set settles at **1.80 GiB** |
| 23:24 - 00:43 | flat at 1.80 GiB for **80 minutes** (8,195,288 edges) |
| 2026-09-09 00:44 | **3.62 GiB** (+1.82 in one scrape) - an ingestion begins |
| 00:45 | **5.34 GiB** (+1.72) |
| 00:46 | first OOMKill (`FalkorDBOOMKilled` replays as first firing 00:46:00Z) |
| 00:55 | 7.45 GiB - ratio **0.9456** of the 8Gi limit |
| 00:46 - 01:07 | crash loop, 7 restarts |
| 01:07 | limit raised 8Gi -> 16Gi; pod Ready, rewrite completes, loop ends |

Measured climb rate over the ingestion: **0.44 GiB/min** (1.80 -> 6.60 GiB).

### Why it could not recover on its own

This was a self-perpetuating loop, not a transient. Each boot:

1. loaded the graph from the AOF (22.0s: 9.8s base RDB + 12.2s incr);
2. logged `Ready to accept connections tcp`;
3. **~15 seconds later** auto-triggered an AOF rewrite -
   `Starting automatic rewriting of AOF on 102% growth` - because stock
   `auto-aof-rewrite-percentage 100` was trivially satisfied after an ingestion
   that size;
4. forked, and the fork's transient on top of a ~7 GiB resident set crossed
   8 GiB;
5. got OOMKilled ~5s after the fork - so the rewrite **never completed**, the AOF
   base was never refreshed, and step 3's trigger was armed again on the next
   boot.

The last termination ran `01:00:24Z -> 01:00:58Z` - **34 seconds**. That is the
tell that separates this from a query-driven kill: it died during startup work,
not under load.

### Falsified along the way

- *"It dies during AOF replay, never reaching ready."* No - the logs show
  `Ready to accept connections tcp` reached on every boot, ~13-22s in, well
  inside the 300s startup-probe budget.
- *"The `browser` sidecar is implicated."* No - `browser` has its own 1Gi limit
  and sat at 85-100 Mi throughout, never restarted, and does not mount the data
  volume.
- *"The fork's CoW is what fills the budget."* No, and this is the one that
  matters. `Fork CoW for AOF rewrite: peak 82 MB` for the rewrite that completed
  at 16Gi; an earlier one taken under active ingestion reported 547 MB. **The
  fork is the trigger, not the bulk.** The bulk is the resident set: the graph
  itself grew from 1.7 GiB to 6.6 GiB.

## 2. What the dataset actually is

Measured on the restored pod, once it was serving:

| Claim | Value | Instrument |
|---|---|---|
| Graph size | `total_graph_sz_mb` **6743** (6.58 GiB) | `GRAPH.MEMORY USAGE plc_code_graph` |
| Edges | **35,997,296** (was 8,195,288) | startup decode log |
| Redis accounting | `used_memory` 7,275,413,312 B | `INFO memory` |
| Real footprint | cgroup `anon` 7,474,188,288 B - **2.66% gap** | `/sys/fs/cgroup/memory.stat` |
| Resident | `used_memory_rss` 6.99 GiB, peak 6.91 GiB | `INFO memory` |
| Cgroup peak in the loop | **7.76 GiB** against an 8.0 GiB cap | `container_memory_max_usage_bytes` |
| AOF on disk | base == current == 466,630,947 B | `INFO persistence` |
| PVC | 1.23 GiB used of 19.52 GiB | `kubelet_volume_stats_*` |

Two things follow. First, **disk was never the constraint** - the in-memory
GraphBLAS representation is ~15x the serialized AOF, so PVC headroom says
nothing about memory headroom. Second, the 2.66% gap means Redis's own
accounting really does see the graph, which is what makes `maxmemory` a real
bound rather than a blind one.

## 3. How the three numbers are derived

They are **one design and must move together**. Changing the limit alone
silently relocates the point at which writes are refused.

```
requests.memory  7Gi    = measured resting RSS (6.99 GiB)
--maxmemory     12gb    = 0.75 of the limit, and above the live dataset
limits.memory   16Gi    = maxmemory + fork headroom
```

**Limit 16Gi.** Resting 7.0 GiB plus a worst-case fork. Redis bounds CoW above
by a full duplication of the resident set, so the documented worst case is
~14 GiB. Observed cost is far smaller (82 MB quiet, 547 MB under ingestion), so
this is deliberately sized against the documented bound, not the happy path.

**`--maxmemory 12gb`.** Without a ceiling there is no bound at all and the
process grows until the kernel kills it - the 2Gi (2026-09-08) and 8Gi
(2026-09-09) failures are the same shape one octave apart. With it, exceeding
the ceiling refuses **writes** and keeps serving **reads**. Re-measured on this
image with `COMMAND INFO`: `GRAPH.QUERY` carries `write denyoom`,
`GRAPH.RO_QUERY` carries `readonly` and does not.

> **The ceiling must stay ABOVE the live dataset.** `maxmemory` is compared
> against `used_memory`, which includes the loaded graph (6.78 GiB today). Set
> it below that and `noeviction` refuses **every write from the moment the AOF
> finishes loading**: the database comes up looking perfectly healthy, reports
> Ready, passes its probes, and silently rejects all ingestion - with no way to
> shed memory to recover. A `6gb` value was drafted on 2026-09-08 when this
> graph was 1.7 GiB; against today's 6.78 GiB it would do exactly that.

**Request 7Gi.** A hard, unshareable reservation, and it takes `talos-2` to ~79%
of memory requests - the top of this cluster's normal band. It is still right: a
2Gi request beside a 7 GiB working set is what lets the scheduler place this pod
somewhere it does not fit.

Headroom was checked **before** the raise, not assumed: `talos-2` holds
93,604Mi allocatable against 69,096Mi of requests (73%) and **25.8 GiB of actual
use (28%)** - about 65 GiB genuinely free. `talos-1` and `talos-3` hold 71% and
81% of requests, so all three retain >= 17 GiB unrequested and the 7Gi request is
schedulable anywhere this RWO claim can follow it.

`maxmemory-policy` stays `noeviction` and **must not** become an eviction policy.
The graph is a single Redis key, so eviction here does not shed cache - it
deletes the captain's edges.

## 4. Deliberately not changed

**`auto-aof-rewrite-percentage`.** Making rewrites rarer treats the fork as the
fault. It was only the trigger: given adequate headroom the same rewrite
completed in 10s for 82 MB of CoW and 423,950,104 bytes written. Raising it
would grow the AOF and lengthen an already 22s replay, for no gain against the
real constraint, which is dataset size. Disk is not under pressure (1.23 GiB of
19.52 GiB).

**`QUERY_MEM_CAPACITY`.** Still `0`. `maxmemory` bounds cumulative growth because
`denyoom` is checked *before* a command runs; it does not bound how much one
already-admitted query then allocates. No defensible value could be derived -
the only memory figure available is the aggregate ingestion transient, which
cannot be decomposed per query. Guessing low aborts a legitimate ingestion on a
graph holding millions of edges, which is the loss this whole arrangement exists
to prevent. What would settle it: sample `used_memory` at 1s through one
ingestion under the now-bounded pod and take the peak delta across a batch.

## 5. The alerts, and the one that survives a crash loop

`kubernetes/apps/base/database/falkordb/app/prometheusrule.yaml`. There was no
memory alert covering this app before it, which is why both OOMKill loops were
learned about from a crash.

| Alert | Threshold | `for:` | Fires at |
|---|---|---|---|
| `FalkorDBMemoryApproachingCeiling` | ratio > 0.60 | 2m | 9.6 GiB, ~2.4 GiB of write headroom left |
| `FalkorDBMemoryAtWriteCeiling` | ratio > 0.70 | 2m | 11.2 GiB, ~0.8 GiB before refusal |
| `FalkorDBOOMKilled` | OOMKilled + restart in 15m | 0m | the bound did not hold |

Thresholds are set against the **maxmemory ceiling** (0.75 of the limit), not
the cgroup limit, so they fire before writes are refused rather than after. The
denominator is `kube_pod_container_resource_limits`, never
`container_spec_memory_limit_bytes` - that family does not exist in this
Prometheus and a rule built on it cannot fire.

`for: 2m` is measured, not guessed: at the observed 0.44 GiB/min, 0.60 -> 0.75 is
only ~5.5 minutes, so the `for: 10m` that looks prudent could not fire before
writes were already being refused.

### The finding worth carrying elsewhere

Replaying the ratio expression over the incident gives a max of **0.9456** and 11
samples above 0.60 - but the **longest contiguous run is 30 seconds**. Each
container lived ~34s, and `container_memory_working_set_bytes` goes absent
between containers, **resetting the `for:` clock every time**.

So during this incident *no ratio alert with any usable `for:` could have fired
at all.* The ratio rules cover the slow-climb shape; `FalkorDBOOMKilled`, with
`for: 0m` and a recency guard, is the only one that survives a crash loop. Both
halves were proven: 22 points over the incident window (first 00:46:00Z), and an
**empty vector** against the recovered pod, so the guard really does clear.

## 6. The CI gate

`scripts/ci/falkordb-lan-browser-access-test.py` asserted
`resources == {"cpu":"50m","memory":"2Gi"} / {"memory":"8Gi"}` - a snapshot
frozen to hold a past PR's scope boundary. It went red on this fix and would
have blocked it.

It now asserts the **relationship** instead of the values, the same
shape-not-value conversion `AGENTS.md` records for `grafana-mcp-deploy-test.py`
and `samba-shared-xml-test.py`: a ceiling exists, it sits **strictly below** the
cgroup limit, no eviction policy is introduced, and the request does not
understate the working set. Each assertion was proven to refuse before merge -
`maxmemory` above the limit, absent, an eviction policy, and a 256Mi request -
with an untouched control passing 64/64. The parser distinguishes redis's `gb`
(1024^3) from `g` (10^9), which are not the Kubernetes suffixes.

## 7. If this recurs

1. Read the dataset first, not the limit: `GRAPH.MEMORY USAGE <graph>` and
   `INFO memory`. If `total_graph_sz_mb` has moved, the limit is the symptom.
2. Check the node's **actual** free memory (`kubectl top nodes`), not just
   requests/limits percentages - this cluster runs limits at 138-173% by design.
3. Raising the limit is legitimate; raising it **without** moving `--maxmemory`
   and the request with it is not - see section 3.
4. Never set `maxmemory` below `used_memory`, and never introduce an eviction
   policy to make it start.

Related: `docs/backups/falkordb-snapshot-restorability-2026-09-04.md` (why
`appendonly yes` is load-bearing for the kopiur backups of this claim).
