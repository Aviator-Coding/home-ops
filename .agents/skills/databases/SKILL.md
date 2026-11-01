---
name: databases
description: "Read before editing kubernetes/apps/base/database/** (surrealdb, cloudnative-pg including offsite-mirror, emqx) or the dragonfly component, changing a database's memory or durability flags, or bumping a database operator. Covers one barman destination per CNPG Cluster, and crash-consistent snapshots needing fsync-level durability."
---

# Databases: SurrealDB, CloudNativePG, EMQX, Dragonfly

Versions live in the HelmRelease or the component, not in a Readme. Several
Readmes in this namespace are behind the manifests (CNPG chart tag, Dragonfly
image, scrape-target count).

## Tripwires

1. **SurrealDB does not roll back, and it has one writer.** Upgrade
   remediation is `strategy: uninstall`. `replicaCount: 1` is the RocksDB
   lock, and the replica alert is `< 1`. Do not set `storageClassName`.
   [surrealdb.md](references/surrealdb.md)
2. **One CNPG Cluster has one live barman destination.** A second
   `ScheduledBackup` with its own `barmanObjectName` still reports completed
   and writes to the first store. Health is `readyInstances` plus
   `ContinuousArchiving`, not `Ready`. Keep the `cluster` metric label.
   Never give a Cluster `serverTLSSecret` or `serverCASecret`: replication
   would trust a public chain. [cnpg.md](references/cnpg.md#tls)
3. **EMQX operator stays on 2.2.x.** 2.3.x drops `apps.emqx.io/v2beta1` and
   calls an Enterprise-only API that 404s against this OSS 5.8 broker.
   [emqx.md](references/emqx.md)
4. **Dragonfly is cache-only.** `--cache_mode=true` means a restart drops
   the data on purpose. There is no central cluster. Five apps, ten scrape
   targets. [dragonfly.md](references/dragonfly.md)
5. **A snapshot backup is crash-consistent.** Both engines have no
   application hook. A database that has not fsynced restores a stale copy
   and still starts clean. FalkorDB (retired, [retired.md](references/retired.md)) measured
   2174 of 2199 nodes with AOF and 200 of 2199 on the image's stock RDB-only
   durability.

## Where things live

| What | Path |
|---|---|
| SurrealDB | `kubernetes/apps/base/database/surrealdb/` |
| CNPG operator, clusters (`cluster-17`, `cluster-18`), pgAdmin | `kubernetes/apps/base/database/cloudnative-pg/` |
| Barman offsite mirror (Backups owns the README) | `kubernetes/apps/base/database/cloudnative-pg/offsite-mirror/` |
| EMQX | `kubernetes/apps/base/database/emqx/` |
| Dragonfly operator | `kubernetes/apps/base/database/dragonfly/` |
| Per-app Dragonfly clusters | `kubernetes/components/dragonfly/` |
| CNPG health gate | `kubernetes/apps/main/database/cloudnative-pg.yaml` (do not edit from this skill's PR; the gate is stated in [cnpg.md](references/cnpg.md)) |

## Procedures

- SurrealDB replica and chart traps: [surrealdb.md](references/surrealdb.md).
- CNPG backup, restore, labels: [cnpg.md](references/cnpg.md).
- EMQX pin and clients: [emqx.md](references/emqx.md).
- Dragonfly consumers and cache policy: [dragonfly.md](references/dragonfly.md).
- Retired FalkorDB and DuckLake: what was kept, how to delete it, revival rules: [retired.md](references/retired.md).

## Verify

- `task flux:test:all`.
- CNPG: `kubectl -n database get cluster postgres-17 postgres-18` shows
  `readyInstances` and `ContinuousArchiving`, not only `Ready`.
- Dragonfly: `up{job=~".*dragonfly.*"}` returns 10 series.
- EMQX operator chart is still a 2.2.x tag.
