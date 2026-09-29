# kopiur populator drift

Historical. A standing `Restore` is `ssa: IfNotPresent`, so raising a `KOPIUR_*` value does not update it. Deleting a bound claim's `Restore` does not touch the volume: the object has no finalizers and owns no data.

Current rules: skill `kopiur-backups`, `references/restore-and-cache.md`.
