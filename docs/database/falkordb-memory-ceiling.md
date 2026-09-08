# FalkorDB memory ceiling and alerts (2026-09-08)

`database/falkordb` had no memory bound of its own. `maxmemory` and
`QUERY_MEM_CAPACITY` were both `0` - the two mechanisms FalkorDB exposes were
both disabled - so nothing stood between the process and the cgroup limit. On
2026-09-08 that produced **13 OOMKills in 95 minutes** after 3d7h with zero
restarts. The limit was raised 2Gi -> 8Gi live during the incident, which bought
headroom but not a bound: the same open-ended arrangement one octave up.

This change sets `--maxmemory 6gb`, commits the resources that were already
live, and adds the first memory alert covering this app.

Diagnosis this builds on: `firstmate:data/homeops-falkordb-oom/report.md`.
Every claim inherited from it was re-measured here before being relied on.

---

## 1. What was measured, and how

All read-only against the live pod unless stated. `redis-cli` runs inside the
`app` container, which carries `REDISCLI_AUTH` via `envFrom: falkordb-secret`;
auth was confirmed positively with `PING` -> `PONG` rather than assumed.

| Claim | Measured value | Instrument |
|---|---|---|
| No dataset ceiling | `maxmemory` = `0` | `CONFIG GET maxmemory` |
| No per-query ceiling | `QUERY_MEM_CAPACITY` = `0` | `GRAPH.CONFIG GET` |
| Policy is correct but inert | `noeviction` | `CONFIG GET maxmemory-policy` |
| Redis can see the graph | `used_memory` 1,892,950,984 B vs cgroup `anon` 1,929,535,488 B - **1.93% gap** | `INFO memory`, `/sys/fs/cgroup/memory.stat` |
| Writes honour OOM refusal | `GRAPH.QUERY` -> `write denyoom` | `COMMAND INFO` |
| Reads do not | `GRAPH.RO_QUERY` -> `readonly` | `COMMAND INFO` |
| Resting set | 1.80 GiB working set, 22.5% of the 8Gi limit | cAdvisor + cgroup |
| Graph size | `total_graph_sz_mb` 1735, graph `plc_code_graph` | `GRAPH.MEMORY USAGE` |

`GRAPH.MEMORY USAGE` is the right instrument for "is the graph still fully
loaded" after a restart: it reports total and per-label/per-relationship-type
sizes without running a traversal, so it costs nothing and cannot itself
exhaust memory.

### The behaviour change, proven rather than inferred

The load-bearing claim is that `maxmemory` converts an OOMKill into a clean
write refusal. That was verified **empirically on the same image**, in a
throwaway pod (`falkordb-ceiling-probe`, own emptyDir, deleted afterwards), so
the live database was never touched:

```
--maxmemory 6gb on the command line  ->  CONFIG GET maxmemory = 6442450944
                                          (exactly 6 GiB; Redis parses gb as 1024^3)
maxmemory-policy                     ->  noeviction

# with used_memory pushed above the ceiling:
GRAPH.QUERY  "CREATE (:T {n:3})"     ->  OOM command not allowed when used memory > 'maxmemory'.
GRAPH.RO_QUERY "MATCH (n:T) ..."     ->  count(n) = 2          <- reads keep serving
pod state                            ->  Running, restarts=0    <- no kill
```

So the failure mode moves from *process killed, AOF reload, restart loop* to
*writes refused, database up and readable*. For a graph database that is the
right trade.

## 2. Why 6gb, and why the resources had to be committed with it

`6gb` = 6 GiB = **75% of the 8Gi limit**, leaving 2 GiB of transient headroom.
The measured ingestion transient above resting was >= 0.5-0.75 GiB, and that is
a *floor* - every observation was truncated by the kill, so the true peak is
unknown and the margin is deliberately generous.

The 8Gi limit and 2Gi request were **live but never committed**. They were
applied with `kubectl patch` during the incident; `kubectl-patch` was still a
field manager on the Deployment, `driftDetection` is unset on the HelmRelease,
and both git and `helm get manifest falkordb` still said `limits.memory: 2Gi`.

That made committing them a **prerequisite, not tidy-up**. `resources` is
chart-rendered, so it lives in Helm's own previous release manifest, and Helm's
3-way merge reconciles such a field back to the manifest value. The next helm
upgrade - which *any* edit to this HelmRelease triggers - would therefore have
restored the 2Gi limit while installing `maxmemory 6gb`. That combination is
strictly worse than doing nothing:

- loading the AOF alone peaks at **1.89 GiB**, so a 2 GiB cap crashloops on
  startup, before a single query;
- a 6 GiB `maxmemory` above a 2 GiB cgroup limit **never engages** - the kernel
  reaches its ceiling first.

> **`main` described a configuration that would crashloop the live database.**
> Until this landed, any change to the falkordb HelmRelease - including a
> routine Renovate chart bump - would have reverted the limit to 2Gi and taken
> the database down. That is why the sanctioned pre-merge suspend/patch/resume
> flow was **not** used here: `flux resume ks` re-applies from `main`, which
> would have triggered exactly that revert.

`scripts/ci/falkordb-lan-browser-access-test.py` now pins the relationship
rather than just the numbers: `--maxmemory` must be present and **strictly
below** the cgroup limit, and no eviction policy may be introduced. Both checks
were proven to refuse (a `16gb` maxmemory, and a missing one) before merge.

## 3. The alerts

There was no memory alert covering falkordb at all - `database` carried rules
for surrealdb and tikv only, which is why this was learned about from a crash.

Thresholds are set against the **maxmemory ceiling**, not copied from the
surrealdb rules. Writes are refused at ratio 0.75, so an 0.8-style threshold
would fire only *after* the ceiling had already bitten.

| Alert | Threshold | `for:` | Fires at |
|---|---|---|---|
| `FalkorDBMemoryApproachingCeiling` | ratio > 0.60 | 10m | 4.8 GiB, ~1.2 GiB of write headroom left |
| `FalkorDBMemoryAtWriteCeiling` | ratio > 0.70 | 2m | 5.6 GiB, ~0.4 GiB before refusal |
| `FalkorDBOOMKilled` | OOMKilled + restart in 15m | 0m | the ceiling did not hold |

The denominator is `kube_pod_container_resource_limits`, **not**
`container_spec_memory_limit_bytes`: that family does not exist in this
Prometheus (re-measured - it returns an empty vector for these pods), and a rule
built on it is structurally incapable of firing. That is the same trap that left
`database/tikv-rules`' `TiKVMemoryHighUsage` dead.

### Fireability, proven against the real incident

Each rule was evaluated **both ways** per `AGENTS.md`: quiet today, and
returning a real series when replayed over the 2026-09-08 21:20-23:30Z window.

Replaying the ratio expression over that window (the old 2Gi pod):

```
max ratio 0.9027    21:20Z 0.6101 ... 22:46Z 0.7470 ... 23:05Z 0.8715 ... 23:20Z 0.9026
```

`for:` durations are measured, not guessed - `AGENTS.md` records that a `for:`
longer than the event cannot fire. Contiguous runs above each threshold:

| threshold | runs | longest | total above | first |
|---|---|---|---|---|
| > 0.60 | 6 | **50m** | 89m | 21:20Z |
| > 0.70 | 5 | **10m** | 17m | 21:50Z |
| > 0.80 | 2 | 6m | 7m | 23:05Z |

So `for: 10m` at 0.60 has 5x margin, and `for: 2m` at 0.70 has 5x margin - while
the 5m/10m that would look prudent at 0.70 are marginal-to-unfireable. The
warning would have fired continuously **from 21:20Z**, over an hour before the
crash that was actually noticed.

The `FalkorDBOOMKilled` expression, replayed unchanged over the same window,
returns **63 points, first firing 21:42Z** - one minute after the first kill.
Its `and on (...) increase(restarts[15m]) > 0` half is a recency guard, not
decoration: `kube_pod_container_status_last_terminated_reason` stays at 1 for
the life of the container, so the left side alone latches and would keep firing
long after recovery. `[15m]` respects the 1m global `scrapeInterval`.

**Gap:** there is no FalkorDB/Redis exporter in this cluster (no ServiceMonitor,
and `redis_*` metrics return zero series), so `used_memory` is not in Prometheus
and there is no direct "writes are being refused" signal. The cgroup working-set
ratio is a proxy for it, accurate to the 1.93% measured above.

## 4. `QUERY_MEM_CAPACITY` - deliberately left at 0

`maxmemory` bounds **cumulative** growth, because `denyoom` is checked *before* a
command runs. It does not bound how much one already-admitted query then
allocates. `QUERY_MEM_CAPACITY` is the knob for that, and it is still `0`.

**No defensible value could be derived, so none was set.** The available
evidence does not support one:

- the telemetry stream records duration, not memory, per query (p50 10.5ms,
  p95 46ms, max 894ms across 1000 queries) - there is no per-query memory
  measurement anywhere;
- the only memory figure that exists is the **aggregate** transient during
  ingestion (>= 0.5-0.75 GiB), and it cannot be decomposed per query;
- that figure is a *floor*, truncated by the OOMKill. Deriving a ceiling from a
  lower bound is how you abort legitimate work.

The cost of guessing low is not a slow query - it is an aborted ingestion batch
on a graph holding millions of the captain's edges, i.e. the data loss this
whole change exists to prevent. A round number would be worse than nothing.

**What would settle it:** run one ingestion under the now-bounded 8Gi pod with
`maxmemory` active, sampling `used_memory` at 1s while a single client sends
batches sequentially. The peak delta across a batch is the per-query working set;
set `QUERY_MEM_CAPACITY` to a comfortable multiple of the observed maximum. That
needs a write, so it belongs to whoever next runs an ingestion.

## 5. The ingestion path - a finding, not a fix

The behaviour change has a consumer. Today an over-limit ingestion kills the
process; afterwards it gets `OOM command not allowed` and the connection stays
up. **Whatever loads the captain's files will start seeing write rejections
instead of dropped connections.**

What the ingestion path is, from the live telemetry stream
(`telemetry{plc_code_graph}`, 1000 entries, 996 of them writes, spanning
22:42-23:42Z):

- batched `UNWIND [ ... ] AS e` queries with **inline literals**, not parameters;
- nodes: `UNWIND [...] AS n CREATE (node:UDT {id: n.id}) SET node += n`;
- edges: `UNWIND [...] AS e MATCH (a:Block:FC {id: e._from}) MATCH (b:Network {id: e._to}) CREATE (a)-[r:CONTAINS]->(b) SET r += e`;
- `CREATE`, **not** `MERGE`, on both;
- one client, sequential batches, over the LAN LoadBalancer (10.50.0.24). It is
  **not in this repo and not in the cluster** - it is an external tool, so its
  error handling could not be inspected from here.

### The trap, measured

The edge shape is `MATCH ... MATCH ... CREATE`. Measured on the probe pod:

```
both endpoints exist    ->  Relationships created: 1
endpoints MISSING       ->  (no "Relationships created" line, no error, exit 0)
```

**A missing-endpoint edge batch reports plain success and creates nothing.** So
if a node batch is OOM-refused and memory then settles back below the ceiling,
the following edge batches are admitted, find no endpoints, and silently write
nothing - while returning success. A client that checks errors per batch and
logs-and-continues finishes with a **silently incomplete graph**.

Because the ingestion uses `CREATE` rather than `MERGE`, a naive re-run after a
partial failure duplicates everything rather than repairing it.

Two consequences:

1. **The alert matters more than the ceiling.** Today a partial ingestion is
   loud (crash, restart loop). Afterwards it can be silent. `FalkorDBMemoryAtWriteCeiling`
   is what makes it loud again, and it is why that rule fires *before* 0.75.
2. **The ingestion path should be checked** for whether it aborts the whole run
   on the first write rejection, and whether it can distinguish "0 rows created"
   from "batch applied". Per the task's scope that is a separate piece of work -
   raised here, not changed.

## 6. Not done here

- The 8Gi limit, the 20Gi volume, and the kopiur backup wiring are untouched.
- `aof_rewrites` is still `0` and one has never run: base 114.6 MiB, current
  208.3 MiB, `auto-aof-rewrite-percentage 100` fires at ~229 MiB. It forks a
  1.8 GiB process and re-serialises the whole graph. Harmless against 8Gi at
  today's size, but it is a headroom consumer that `maxmemory` does not bound
  (a fork's COW pages are not `used_memory`) - which is part of why
  `FalkorDBOOMKilled` is kept as a backstop.
- `database/tikv-rules`' `TiKVMemoryHighUsage` still divides by the dead
  `container_spec_memory_limit_bytes` and remains structurally unfireable.
  Out of scope, still true, flagged again here.
