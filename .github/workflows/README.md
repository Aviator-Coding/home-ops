# GitHub Actions workflows

Which workflow runs where, and what `validate.yaml` does and does not prove:
skill `github-ci`
([workflows.md](../../.agents/skills/github-ci/references/workflows.md)).

`runs-on` in the workflow file wins over any table. The home-ops scale set
has no Docker daemon. `renovate.yaml` and the image-build workflows stay on
`ubuntu-latest` for that reason.

Renovate's live writer is the in-cluster CronJob. This workflow's schedule
is commented out. Skill `renovate`.
