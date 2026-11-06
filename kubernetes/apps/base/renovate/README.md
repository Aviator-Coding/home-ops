# renovate

mogenius `renovate-operator` with one hourly `RenovateJob` (`home-ops`). The
GitHub Actions workflow is rollback only (manual dispatch). UI at
`renovate.${SECRET_DOMAIN}`, internal route, Authentik OIDC
(`terraform/authentik/renovate-operator.tofu`, 1Password `Homelab/renovate-operator-oidc`).

Skill `renovate`
([operations.md](../../../../.agents/skills/renovate/references/operations.md),
[versioning-traps.md](../../../../.agents/skills/renovate/references/versioning-traps.md)).

## Rollback

1. Set `spec.suspend: true` on `renovate-operator/jobs/renovatejob.yaml` and merge.
2. Dispatch `.github/workflows/renovate.yaml` (or add a `schedule` to it).

GitHub App fields: 1Password `Homelab/renovate` (`BOT_APP_ID`,
`BOT_APP_PRIVATE_KEY`); the installation ID is in `jobs/externalsecret.yaml`.
Alerts use the operator's own metrics (`RenovateRunFailed`, `RenovateRunMissing`,
`RenovateDiscoveryEmpty`, `RenovateOperatorAbsent`).
