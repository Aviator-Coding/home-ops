# Exporter endpoint repair

The still-true checks live in the skills. This path remains because
`app-workloads` and `secrets-1password` still name it.

- An ExternalSecret can be `SecretSynced` with an empty value. Check decoded
  length, not status. Skill `secrets-1password` and skill `media-stack`.
- A probe that cannot return non-200 never feeds `KubePodNotReady`. n8n
  liveness stays `/healthz`; readiness is `/healthz/readiness`. Skill
  `observability`.
- plex-exporter probes `/` on purpose so a 500 stays in the Prometheus target
  list and TargetDown can fire. Pointing probes at `/metrics` would drop the
  target. Skill `observability`.
