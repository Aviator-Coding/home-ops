# kopiur restore drill

The procedure moved to [`kopiur-restore-runbook.md`](kopiur-restore-runbook.md).

Stage 2's gate passed: `downloads/sabnzbd-config` restored byte-identically from both ceph and r2. The two findings that gate still enforces: a `Succeeded` snapshot with `filesNew: 0` and `sizeBytes: 0` proves nothing, and a mover identity that does not own the files fails closed (`KOPIUR_PUID` / `KOPIUR_PGID`, read-only staging). Skill `kopiur-backups`.
