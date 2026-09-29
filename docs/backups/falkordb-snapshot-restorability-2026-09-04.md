# FalkorDB snapshot restorability

Both backup engines snapshot crash-consistently, with no application hook. On one FalkorDB snapshot of 2199 nodes, AOF `everysec` recovered 2174 and stock RDB-only recovered 200. Both startups looked healthy.

Durability has to be configured on the database. Skill `kopiur-backups` tripwire 14, and skill `databases`.
