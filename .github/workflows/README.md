# GitHub Actions workflows

Which workflow runs where, and what `ci.yaml` does and does not prove:
skill `github-ci`
([workflows.md](../../.agents/skills/github-ci/references/workflows.md)).

`ci.yaml`'s `CI Success` is the one gate. Every job in it runs on
`ubuntu-latest`. `image-pull.yaml` and `terraform-diff.yaml` need the cluster
and are advisory. `tests.yaml` runs the non-gate `scripts/ci` tests nightly.

Renovate's live writer is the in-cluster renovate-operator; `renovate.yaml` is
manual-dispatch only. Skill `renovate`.
