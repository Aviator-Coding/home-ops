# Hermes `state.db` growth: measurement and the retention fix

**Measured:** 2026-09-15, against the live pod (`ai/hermes`, image
`nousresearch/hermes-agent:v2026.9.11`).
**Contract:** read-only. Nothing under `/opt/data` was written, no `VACUUM`, no prune and no
retention command was run against the live database. Every SQLite open below used
`file:/opt/data/state.db?mode=ro&immutable=1`, proven read-only two ways each time: an explicit
write attempt was **refused** (`OperationalError: attempt to write a readonly database`) and the
size + mtime of `state.db`, `state.db-wal` and `state.db-shm` were byte-identical before and after.

Shipped change: the `sessions:` block in
`kubernetes/apps/base/ai/hermes/app/resources/config.yaml`.

---

## 1. Bottom line

`state.db` was **9.31 GiB** (2,440,957 x 4 KiB pages) on a 25Gi claim already **76% full**
(19G used, 6.0G avail), growing **~175-235 MB/day**. Hermes' own `doctor` warns above
**1 GiB** (`hermes_cli/doctor_state.py:45`, `STATE_DB_SIZE_WARN_BYTES`), so this is 9.3x its own
threshold.

There **is** a supported upstream setting, it was **already enabled**, and it was **already
running daily**. It reclaimed nothing because its window is too wide for this install:

| | |
|---|---|
| `sessions.auto_prune` | already `true` (upstream default since #54189) |
| `sessions.retention_days` | `90` (upstream default) |
| `last_auto_prune` in `state_meta` | **present and current** - the sweep runs |
| `last_vacuum` in `state_meta` | **ABSENT** - VACUUM has never once run |
| `PRAGMA freelist_count` | **292** of 2,440,957 pages (**0.012%**) |

The file is not bloat. It is 9.31 GiB of dense, live data. Pruning had freed essentially nothing,
so the `pruned > 0` precondition on VACUUM had never been met.

**The fix is to narrow the window, not to enable anything.** `retention_days: 90` cannot fit this
claim: see section 3.

---

## 2. What was measured

### 2.1 Volume and rate

```
/dev/rbd16   25G  19G  6.0G  76% /opt/data
state.db      9,999,585,280 bytes   (2,440,957 pages x 4096)
```

`du -sm /opt/data/*`, largest first: `state.db` 9538, `home` 6268, `wiki` 1644, `ai-wiki` 510,
`lazy-packages` 405, `lsp` 112, `plc_code_graph` 104.

Growth, from the kopiur snapshot `sizeBytes` series (whole volume): +290, +193, +217, +241 MB on
successive days. An independent bottom-up estimate agrees: **~3,100 `message_count`/day** (below) at
**~56.0 KiB all-in per `message_count`** (9,999,585,280 / 174,276) is **~174 MB/day**.

### 2.2 Row census

9,340 sessions, **238,052 message rows**. Grouped by source (`sessions.message_count`):

| source | ended | open | `message_count` |
|---|---|---|---|
| `cron` | 8,888 | 346 | **170,436** |
| `subagent` | 13 | 0 | 1,126 |
| `cli` | 43 | 21 | 1,370 |
| `tui` | 1 | 20 | 889 |
| `telegram` | 3 | 2 | 429 |
| `webui` | 0 | 1 | 18 |
| `api_server` | 2 | 0 | 8 |
| **total** | | | **174,276** |

**`cron` is 98.9% of sessions and 97.8% of `message_count`.** This is an automation-log store that
happens to also hold a handful of human conversations, not the reverse.

> Note `SUM(message_count)` (174,276) is lower than the actual `messages` row count (238,052),
> ratio 1.366. `message_count` is used above for **proportions** only; absolute byte figures are
> derived from the file size and the true row count.

### 2.3 Activity is accelerating

`sessions.started_at` windows:

| window | `message_count` | per day |
|---|---|---|
| last 1d | 3,106 | 3,106 |
| last 7d | 23,054 | 3,293 |
| last 30d | 90,656 | 3,022 |
| last 60d | 142,056 | 2,368 |
| last 99d (all) | 174,295 | 1,761 |

Install age **98.9 days** (oldest session 2026-06-08). The recent rate is **~1.8x** the
lifetime average, so any sizing done against the lifetime average understates the problem.

---

## 3. Why `retention_days: 90` could never bound this claim

Two independent reasons, both measured.

**(a) The window is wider than the install is old.** At 98.9 days, only sessions idle >90 days
existed to prune: **218 sessions / 5,710 `message_count` (3.3%)**. That is why `freelist_count` was
292 pages. Pruning was working perfectly and had nothing in scope.

**(b) The 90-day steady state does not fit.** Steady state is
`retention_days x daily_rate x bytes_per_message_count`:

| `retention_days` | projected `state.db` steady state |
|---|---|
| 90 (default) | **~15.6 GiB** |
| 45 | ~7.8 GiB |
| **30 (shipped)** | **~5.2 GiB** |
| 21 | ~3.6 GiB |
| 14 | ~2.4 GiB |

With ~9.7 GB of other `/opt/data` content, the 90-day steady state is **~25.3 GB against a 25Gi
claim**. The default is not merely generous here, it is arithmetically unsatisfiable, and the
model is self-consistent: at the *older* 1,761/day rate a 90-day window projects ~8.9 GiB, which is
approximately the 9.31 GiB actually on disk.

### What `retention_days: 30` discards

Measured directly (ended, unpinned, idle > 30 days):

| source | sessions deleted | `message_count` |
|---|---|---|
| `cron` | 3,982 | 56,629 |
| `cli` | 43 | 409 |
| `api_server` | 2 | 8 |

**99.3% of the delete set is `cron` automation.** Every `telegram` (5), `tui` (21) and `subagent`
(13) session survives. The only human-authored loss is 43 ended `cli` sessions whose newest activity
was **64 days ago**.

Separately the same pass **closes** (does not delete) ~350 stale-open automation sessions via
`startup_orphan_reap`; they stay resumable and get a further full retention window before removal.

Dialling it further is a one-number change in Git. A specific session can be exempted permanently
by **pinning** it: `pinned` sessions are never pruned at any age (none are pinned today).

---

## 4. What is actually large: 79% of the file is FTS machinery

`state.db` carries **two** FTS5 indexes and both are declared with an inline content table, so the
message text is stored **three times** in total:

```sql
CREATE VIRTUAL TABLE messages_fts         USING fts5( content )
CREATE VIRTUAL TABLE messages_fts_trigram USING fts5( content, tokenize='trigram' )
```

Neither carries `content=''`, so FTS5 keeps its own full copy in `messages_fts_content` and
`messages_fts_trigram_content` on top of the canonical `messages` rows.

Sizes below are **estimates** from unbiased sampling (random exact-`rowid` lookups, hit rate also
estimating row count; no table scans, deliberately, since the volume was under load):

| region | est. size | share |
|---|---|---|
| `messages` (all text columns) | ~1.95 GiB | 21% |
| `messages_fts_content.c0` (duplicate #1) | ~1.46 GiB | 16% |
| `messages_fts_trigram_content.c0` (duplicate #2) | ~1.51 GiB | 16% |
| residual: the two FTS inverted indexes + `docsize`/`idx` + overhead | ~4.39 GiB | 47% |

So roughly **1.95 GiB of message data is costing 9.31 GiB on disk**, and ~79% of the file is FTS
storage and index. `messages.content` alone averages ~6.8 KiB/row, consistent with tool-result
payloads dominating.

> Methodology note: a first attempt sampled evenly-spaced `rowid >= target LIMIT 1`, which
> over-counts rows following deletion gaps and produced an impossible 433% of the file. If you
> re-measure, use exact-`rowid` rejection sampling.

### This DB is on legacy FTS layout 0

`state_meta.fts_storage_version` is **absent** (= layout 0) against the image's
`FTS_STORAGE_VERSION = 2`, and `state_meta.fts_optimize_available = 1`. Upstream describes layout
>=1 as "v23 external-content layout with a tool-row-excluded trigram" and layout 2 as "trigram also
excludes structured `tool_calls` JSON", and its own config comment calls the compact layout worth
"~60%+ of state.db". The migration is **opt-in** and offline: `hermes sessions optimize-storage`.
See section 6.

---

## 5. Why `vacuum_after_prune` had to be turned OFF

This is the one place the shipped config deliberately contradicts upstream's default, and it is
load-bearing.

VACUUM is the only operation that returns freed pages to the filesystem. It **cannot succeed here**:
it rewrites every page through the WAL (`hermes_state_maintenance.py::vacuum` TRUNCATEs the WAL
afterwards precisely because "a 3 GB DB leaves a 3 GB -wal"), so a ~9.3 GiB database needs
comparable free space. The claim has **6.0 GiB**.

Left at `true` it would not fail quietly once per month, it would fail **repeatedly**:

- the gate is `vacuum and pruned > 0 and vacuum_due`;
- `vacuum_due = since_vacuum is None or since_vacuum >= min_vacuum_interval_days * 86400`, and
  `last_vacuum` is **absent**, so `since_vacuum is None` holds **forever** and the 30-day throttle
  never engages;
- `last_vacuum` is only set *after* `self.vacuum()` returns, so a failure never records an attempt;
- the freelist ratio after a 30-day prune (~33%) clears the 25% `AUTO_VACUUM_MIN_FREELIST_RATIO`
  gate.

So every pass that deleted rows would attempt a full rewrite, write until ENOSPC and roll back. On
a volume already at 76% that risks filling it to **100%**, which stops Hermes persisting anything.
Not reclaiming is strictly safer.

**Consequence, stated plainly: the file does not shrink.** Pruning frees pages onto the freelist and
SQLite reuses them for subsequent inserts (upstream's own note: "freed pages are just reused on
subsequent INSERTs"), so **growth stops and the file plateaus near 9.31 GiB**. With a 30-day window
the steady-state content is ~5.2 GiB, so a ~3-4 GiB freelist should absorb new writes indefinitely.

---

## 6. Reclaiming the existing 9.31 GiB: a proposal, NOT done

No safe in-place path exists today, because every option needs free space the claim does not have.
**None of the below was run.** All of it is an operator decision.

Ordered by what unlocks the next step:

1. **Free space first.** ~6.0 GiB avail is the blocker. The largest reclaimable item is the uv
   cache / wheel content described in section 7.
2. **Or grow the claim.** `ceph-block` supports online expansion; `KOPIUR_CAPACITY` in
   `kubernetes/apps/main/ai/hermes.yaml` is the value a rebuild would provision, so it must be
   raised in the same change. 25Gi -> 40Gi leaves room for both a VACUUM temp copy and the WAL.
3. **Then `hermes sessions optimize-storage`, offline, gateway stopped.** This is the big win: it
   migrates off legacy FTS layout 0, which removes the two duplicate content copies (~3 GiB by the
   section 4 estimate) and narrows the trigram index. Upstream puts it at "~60%+". It is documented
   as "disk-heavy (transient ~2x file size to fully reclaim via VACUUM)", it must run with **every**
   holder stopped, and it is interruption-aware (leaves resumable markers). Take a kopiur snapshot
   immediately before.
4. **`hermes sessions optimize`** is the non-destructive lesser option: merges FTS5 segments and
   VACUUMs without touching session data. Still needs the space from step 1 or 2.

A plain one-off `hermes sessions prune --older-than 30 --source cron --dry-run` is safe to *inspect*
at any time and is the cheapest way to confirm the section 3 delete set independently.

> Ordering matters: run the retention change first (it is already merged and needs no space), let it
> plateau the file, and only then decide whether reclaiming is worth an offline window.

---

## 7. The CUDA/torch wheels: the bytes are real but shared

`/opt/data/plc_code_graph/.venv` is ~5.6 GiB of CUDA/torch wheels. The obvious reading, that
deleting it reclaims 5.6 GiB, is **wrong**, and `du` says so plainly:

```
du -sm /opt/data/plc_code_graph          ->  5673
du -sm /opt/data/home /opt/data/plc_code_graph
  6268  /opt/data/home
   104  /opt/data/plc_code_graph          <-- same directory, 104 not 5673
```

Every large file in the venv has **link count 2**: they are hardlinks into the uv cache at
`/opt/data/home/.cache/uv` (5,978 MB), which is how `uv` populates a venv.

- Deleting the venv alone reclaims **~104 MB**.
- Reclaiming the ~5.6 GiB needs **both** the venv and the uv cache.

**For backups the finding is different, actionable, and measured.** `ignoreCacheDirs` is honoured and
the uv cache **is** `CACHEDIR.TAG`-tagged, as are `wiki/.venv`, `venv-httpx`,
`skills/media/youtube-content/.venv` and `.hw-venv`. `plc_code_graph/.venv` is the **only** venv on
this volume with **no** `CACHEDIR.TAG`, and because it hardlinks the same content, excluding the
cache currently saves nothing: the wheels reach every snapshot through the untagged venv path.

The kopiur `Snapshot` series dates this to the day, and it is not an inference:

```
hermes-r2-20260907232856   11,578,076,360
hermes-r2-20260908232553   11,888,210,983
hermes-r2-20260909232733   17,894,927,710   <-- +6.01 GB in one day
hermes-r2-20260910232525   18,145,582,182
...
hermes-r2-20260914232408   19,086,156,974
```

`/opt/data/plc_code_graph/.venv` has mtime **2026-09-09 08:30**. Snapshots jumped **+6.01 GB** on
exactly that date and have carried it every day since, which is the uv cache content (5,978 MB)
arriving through the untagged path. Everything before 2026-09-09 sat at ~11.9 GB. So tagging that
one directory would return every snapshot to roughly its pre-09-09 size, and it is also why the
current snapshot (19.09 GB) is barely smaller than the volume (19.49 GB) despite a tagged 5,978 MB
cache: both copies have to be excluded for either to count.

The clean GitOps fix is `spec.files.ignoreRules` on the SnapshotPolicy, layered per-app with
`spec.patches` on the Flux Kustomization exactly as `database/surrealdb` layers `hooks` onto the
shared component (`kubernetes/components/kopiur/ceph/snapshotpolicy.yaml` documents that precedent).

**Deliberately not shipped in this change, for two reasons.** It is a *backup-scope* change to the
one volume in the fleet with a proven end-to-end restore, so a regression there should be
attributable to its own PR. And `ignoreRules` **replaces the default list wholesale** (the CRD is
explicit: re-add `/lost+found`, `System Volume Information`, `$RECYCLE.BIN`, `@eaDir`, `.snapshot`,
and an explicit `[]` opts fully out), so a wrong glob silently drops real data from backups with no
signal. It needs its own PR plus a post-merge check that `filesNew`/`sizeBytes` moved the expected
~5.6 GiB and nothing else.

---

## 8. What this change does NOT fix

1. **The existing 9.31 GiB.** Growth stops; the file does not shrink. Section 6.
2. **The boot-path `quick_check` overrun.** After an unclean exit,
   `gateway/lifecycle_ledger.py::check_state_db_integrity` runs `PRAGMA quick_check(1)` over the
   whole file with **no size ceiling**, unlike its sibling `hermes_cli/backup.py`, which carries
   `DEFAULT_INTEGRITY_CHECK_MAX_BYTES = 2 << 30` and states that databases "in the tens of GB are
   normal for heavy users". Runtime scales with page count, so holding the file flat stops this
   getting worse but does not make it better, and was **not** addressed by this change. The
   watchdog-budget and memory-limit mitigation has since shipped separately: see
   ["Restarting Hermes is never clean"](../../kubernetes/apps/base/ai/hermes/README.md#restarting-hermes-is-never-clean-and-that-used-to-be-unrecoverable)
   in the app README.
3. **Applying the change requires a pod restart** (reloader on the ConfigMap, `strategy: Recreate`).
   A *clean* shutdown sets the lifecycle sentinel to `exited` and the next boot skips the integrity
   check entirely. A shutdown that overruns the grace period does not, and the next boot runs the
   ~25-minute check. Watch the rollout rather than firing and forgetting.
4. **The sweep is startup-only, so the bound is enforced at restart cadence.** There are exactly two
   call sites, `gateway/run.py::_init_session_db` and `cli.py`'s
   `_run_state_db_auto_maintenance`, both at **process construction**, and each is additionally
   throttled by `min_interval_hours` (24). Nothing runs it on a timer. Measured 2026-09-15:
   `last_auto_prune` was 4.21 h old and had **not** advanced across that day's several gateway
   restarts, because the 24h throttle skipped it each time. The in-process cron scheduler does not
   spawn `hermes` CLI processes, so cron activity does not trigger it either.

   In practice this pod restarts often enough (Renovate image bumps, any ConfigMap change via
   reloader, node maintenance) that the sweep does fire, and the effective cadence is bounded below
   by 24h. But a gateway left running for months would not prune at all, and no supported setting
   adds a periodic trigger. Deliberately **not** solved with a bespoke CronJob here: that would need
   a second process against an RWO claim the Deployment holds, and the direct path has not yet
   demonstrably failed. If the section 9 check ever shows the file growing with `last_auto_prune`
   weeks stale, that is the trigger to revisit.
5. **Per-source retention**, which is what this workload actually wants (short for `cron`, long for
   human chat), **does not exist upstream**. `NousResearch/hermes-agent#110589` requests exactly it
   and is **open**; #38378 (cron session auto-cleanup) is **open**. Confirmed against the running
   image: no `source_retention`, `cron.session_retention_days` or `persist_sessions` key exists
   anywhere in `/opt/hermes`. One global number is the whole of the supported surface.

---

## 9. How to verify in a week

The sweep runs **only at gateway/CLI process startup**, and then only if more than
`min_interval_hours` (24, the default) has passed since `last_auto_prune`. So the **first** pass
lands on the first Hermes restart after the merge that is also >24h after the last sweep, not
immediately. The merge itself causes a restart (reloader on the ConfigMap), so if `last_auto_prune`
is less than 24h old at that moment the first real pass waits for the *following* restart.

Check where it stands at any time:

```bash
kubectl -n ai exec -i $POD -c app -- python3 - <<'EOF'
import sqlite3, time
c = sqlite3.connect("file:/opt/data/state.db?mode=ro&immutable=1", uri=True)
for k in ("last_auto_prune", "last_vacuum"):
    r = c.execute("SELECT value FROM state_meta WHERE key = ?", (k,)).fetchone()
    print("%-16s %s" % (k, time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(float(r[0]))) if r else "(ABSENT)"))
EOF
```

`last_vacuum` should stay **ABSENT** forever now: that is `vacuum_after_prune: false` working.

```bash
export KUBECONFIG=/Users/coder/firstmate/projects/home-ops/kubeconfig
POD=$(kubectl -n ai get pod -l app.kubernetes.io/name=hermes -o name | head -1)

# 1. The setting is live (proves the ConfigMap reached /opt/data/config.yaml):
kubectl -n ai exec $POD -c app -- grep -A3 '^sessions:' /opt/data/config.yaml

# 2. The sweep ran and what it did (the one log line it emits):
kubectl -n ai logs $POD -c app | grep 'auto-maintenance'
#   expect: "state.db auto-maintenance: closed N stale open session(s), pruned M
#            session(s) inactive for 30 days"

# 3. Growth stopped. This is the real signal, and the freelist is the tell:
kubectl -n ai exec $POD -c app -- sh -c 'df -h /opt/data; stat -c "%s" /opt/data/state.db'
```

Expected after the first pass: `page_count` roughly flat week over week, and
`PRAGMA freelist_count` up from **292** to **hundreds of thousands** of pages. A growing freelist
with a flat file size is success; it means deletions are being recycled instead of extending the
file.

Read `freelist_count` without touching the live file:

```bash
kubectl -n ai exec -i $POD -c app -- python3 - <<'EOF'
import sqlite3
c = sqlite3.connect("file:/opt/data/state.db?mode=ro&immutable=1", uri=True)
pc = c.execute("PRAGMA page_count").fetchone()[0]
fl = c.execute("PRAGMA freelist_count").fetchone()[0]
print("page_count=%d freelist=%d (%.2f%% reclaimable)" % (pc, fl, 100.0*fl/pc))
EOF
```

**Failure signals**, in the order they would surface:

1. No `auto-maintenance` log line after a restart. The block is not being read; check that
   `copy-config` actually overwrote `/opt/data/config.yaml`.
2. Line present, `pruned 0`. Nothing was in scope; re-run the section 3 query before lowering
   `retention_days` further.
3. `state.db VACUUM failed` appears at all. `vacuum_after_prune: false` is not in effect.
4. File still growing with a large freelist. Something other than session data is growing; re-do
   the section 4 attribution.

### Suggested alerting

Owned by the monitoring work, not added here: alert on **`/opt/data` PVC utilisation** (the volume
was 76% full and its failure mode is silent). Note this repo's guidance on dead alert rules first,
and that `kubelet_volume_stats_*` is the series that actually exists for this.

---

## 10. Upstream references

- [`sessions.md` user guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/sessions.md)
  and [Sessions docs](https://hermes-agent.nousresearch.com/docs/user-guide/sessions)
- [Session storage developer guide](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/session-storage.md)
- **#110589** per-source retention (cron exhausts one global `retention_days`) - **open**
- **#38378** optional auto-cleanup of cron sessions - **open**
- **#54189** the change that made `auto_prune` default true ("without it state.db grows without
  bound (multi-GB installs reported within weeks)")
- **#24034** `state.db-wal` grows unbounded, PASSIVE checkpoint never truncates
- **#103647** `optimize-storage` fails on CJK-extension DBs (does not apply: `messages_fts_cjk` is
  not present here, only `messages_fts` and `messages_fts_trigram`)

In-image sources read for this analysis: `hermes_cli/config_defaults.py` (`sessions:` block),
`hermes_cli/config.py` (`load_config` deep-merges `DEFAULT_CONFIG`),
`hermes_state_maintenance.py` (`maybe_auto_prune_and_vacuum`, `prune_sessions`, `vacuum`),
`hermes_state.py` (`_AUTO_PRUNE_STALE_OPEN_SOURCES`), `hermes_state_common.py`
(`AUTO_VACUUM_MIN_FREELIST_RATIO`, `FTS_STORAGE_VERSION`), `hermes_cli/doctor_state.py`
(`STATE_DB_SIZE_WARN_BYTES`), `gateway/run.py` and `cli.py` (the two auto-maintenance call sites).
