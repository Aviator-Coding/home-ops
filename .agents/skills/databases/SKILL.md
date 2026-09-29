---
name: databases
description: "Read before editing kubernetes/apps/base/database/** (falkordb, surrealdb, cloudnative-pg including offsite-mirror, emqx) or the dragonfly component, changing a database's memory or durability flags, or bumping a database operator. Covers FalkorDB memory numbers moving together with AOF on, one barman destination per CNPG Cluster, and crash-consistent snapshots needing fsync-level durability."
---

# Databases: FalkorDB, SurrealDB, CloudNativePG, EMQX, Dragonfly

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`docs/database/falkordb-memory-ceiling.md`](../../../docs/database/falkordb-memory-ceiling.md) - FalkorDB memory ceiling
- [`docs/backups/falkordb-snapshot-restorability-2026-09-04.md`](../../../docs/backups/falkordb-snapshot-restorability-2026-09-04.md) - FalkorDB snapshot restorability
- [`kubernetes/apps/base/database/cloudnative-pg/Readme.md`](../../../kubernetes/apps/base/database/cloudnative-pg/Readme.md) - CloudNativePG
- [`kubernetes/apps/base/database/cloudnative-pg/offsite-mirror/README.md`](../../../kubernetes/apps/base/database/cloudnative-pg/offsite-mirror/README.md) - barman archive offsite mirror
- [`docs/backups/postgres-offsite-destination-design-2026-09-12.md`](../../../docs/backups/postgres-offsite-destination-design-2026-09-12.md) - offsite destination design
- [`kubernetes/apps/base/database/emqx/Readme.md`](../../../kubernetes/apps/base/database/emqx/Readme.md) - EMQX
- [`kubernetes/apps/base/database/dragonfly/Readme.md`](../../../kubernetes/apps/base/database/dragonfly/Readme.md) - Dragonfly

`AGENTS.md` entries (search for the opening words):

- Both backup engines snapshot a volume CRASH-CONSISTENTLY
- CNPG can express only one live barman destination per `Cluster`

## Related skills

- `kopiur-backups`
- `node-scheduling`
