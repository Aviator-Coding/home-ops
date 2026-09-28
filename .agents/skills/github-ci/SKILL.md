---
name: github-ci
description: "Read before editing .github/workflows/**, scripts/ci/**, the ARC runner scale sets or branch protection; before retiring an app or changing a value a scripts/ci gate may pin; or when a check is red or flaky. Covers validate.yaml scope, the fork guard, runners with no Kubernetes API or Docker, contention reruns, mise-action pins, and doc-reading tests."
---

# GitHub CI: workflows, scripts/ci gates and ARC runners

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`scripts/ci/README.md`](../../../scripts/ci/README.md) - gate inventory and local runs
- [`.github/workflows/README.md`](../../../.github/workflows/README.md) - workflow inventory
- [`kubernetes/apps/base/actions-runner-system/TROUBLESHOOTING.md`](../../../kubernetes/apps/base/actions-runner-system/TROUBLESHOOTING.md) - ARC runners
- [`docs/branch-protection.md`](../../../docs/branch-protection.md) - ruleset and required checks

`AGENTS.md` entries (search for the opening words):

- `.github/workflows/validate.yaml` is the only CI signal
- Never pin a version in `jdx/mise-action` `install_args`
- Retiring an app can break a `scripts/ci/*-test.py` gate
- A red `validate.yaml` check that passes on rerun is runner-pool contention
- The self-hosted ARC runner is deliberately unprivileged
- A stale `actions-runner` Talos ServiceAccount client certificate

## Related skills

- `renovate`
