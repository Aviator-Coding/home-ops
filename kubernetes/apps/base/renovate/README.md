# renovate

In-cluster CronJob, every 4 hours. The GitHub Actions workflow is rollback
only: its schedule is commented out, and its `push` trigger still overlaps
this writer. Suspend that workflow before merging `.renovaterc.json5` or
`.renovate/**`.

Skill `renovate`
([operations.md](../../../../.agents/skills/renovate/references/operations.md),
[versioning-traps.md](../../../../.agents/skills/renovate/references/versioning-traps.md)).

## Rollback

1. Uncomment the `schedule` block in `.github/workflows/renovate.yaml` and merge.
2. Optionally set `env.RENOVATE_DRY_RUN: full` on the HelmRelease so the
   CronJob stops writing. Do not set that env to `"false"`.

GitHub App fields: 1Password `Homelab/renovate` (`BOT_APP_ID`,
`BOT_APP_PRIVATE_KEY`). Alerts watch kube-state-metrics CronJob timestamps
(`RenovateJobFailed`, `RenovateRunMissing`, `RenovateNeverSucceeded`,
`RenovateCronJobAbsent`). The chart has no metrics endpoint.
