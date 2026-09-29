# kopiur restore cache

Required cache is `min(snapshot sizeBytes, ~6.2 GiB)` plus headroom. Under that plateau any value works. The first crossing fails the `Restore` terminally and it never retries.

Current per-claim values and what each one proved are skill `kopiur-backups`, `references/restore-and-cache.md`. A raised `KOPIUR_*` does not reach a standing `Restore` until that object is deleted and Flux recreates it. Deleting a `Restore` is safe.
