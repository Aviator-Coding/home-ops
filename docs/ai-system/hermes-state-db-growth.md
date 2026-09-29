# Hermes state.db growth

The measurements and the vacuum mechanism moved to skill `hermes-agent`, `references/state-db.md`.

Never set `sessions.vacuum_after_prune: true`. `retention_days` is what bounds growth. Reclaiming space is an offline `hermes sessions optimize-storage`, and it needs free space first.
