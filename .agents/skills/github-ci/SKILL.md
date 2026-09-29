---
name: github-ci
description: "Read before editing .github/workflows/**, scripts/ci/**, the ARC runner scale sets or branch protection; before retiring an app or changing a value a scripts/ci gate may pin; or when a check is red or flaky. Covers validate.yaml scope, the fork guard, runners with no Kubernetes API or Docker, contention reruns, mise-action pins, and doc-reading tests."
---

# GitHub CI: workflows, scripts/ci gates and ARC runners

`validate.yaml` is the only CI signal for `talos/`, `bootstrap/`, `.renovate/`
and credential-less `terraform/`. `flate` and `image-pull` only see
`kubernetes/**`. A green schema check is not permission to `just talos apply-node`.

## Tripwires

1. **`talosctl validate` is schema-only.** It catches render breakage and
   unknown machine-config keys. Invalid enums, bad CIDRs and a missing install
   disk all pass. Bootstrap coverage is `bootstrap/kustomize/` only.
2. **Keep the fork guard on every self-hosted `pull_request` job.**
   `if: github.event.pull_request.head.repo.full_name == github.repository`.
   This repo is public. A fork PR skips those workflows; a maintainer re-runs
   them from a same-repo branch.
3. **Runners have no Kubernetes API and the home-ops set has no Docker.**
   Both scale sets set `automountServiceAccountToken: false` and no
   `serviceAccountName` (chart no-permission SA). The only node credential is
   the home-ops Talos `ServiceAccount` at `os:operator`. `docker://` actions
   and `docker run` fail on `gha-runner-scale-set-aviator-coding-home-ops`.
   [arc-runners.md](references/arc-runners.md)
4. **A red `validate.yaml` check that passes on rerun is pool contention.**
   Editing the workflow matches every per-job filter, so the set launches at
   once. `maxRunners: 15` was not the ceiling (PR #1481). Rerun. Do not raise
   `maxRunners`, drop the workflow from its own filters, or serialize jobs
   (concurrency wait counts toward `timeout-minutes`).
5. **`jdx/mise-action` `install_args` names the tool only.** A `@version` pin
   installs a different copy than the `.mise.toml` shim. Gate:
   `scripts/ci/mise-install-args-test.py`.
6. **Grep `scripts/ci/` before retiring an app or changing a pinned value.**
   `task flux:test:all` does not run these tests. Assert a relationship or a
   pin's shape. Leave the `ARG TALOS_VERSION` canary in
   `.github/docker/talosctl-busybox/Dockerfile` alone (skill `talos-nodes`).
   [scripts-ci.md](references/scripts-ci.md)
7. **A stale runner client cert fails `image-pull` on every `kubernetes/**`
   PR.** `remote error: tls: expired certificate` is the node's apid rejecting
   the runner's mTLS cert. Delete Secret `actions-runner` in
   `actions-runner-system` so the `talos.dev` controller reissues it. The
   pull step's retries cannot fix an already-expired cert.
8. **Only `Labeler - Labeler` is a required check.** Ruleset
   `main-branch-protection` (id `21250320`) is applied with `gh api`, not Git.
   `platformAutomerge: false` because GitHub merges the moment that one check
   is green. [branch-protection.md](references/branch-protection.md)
9. **Workflow comments are not workflow logic.** Edits under `.github/workflows/`
   may change comments and docs. Leave `runs:`, `if:`, `with:` and `@sha` pins
   unchanged unless the task is a workflow change.

## Where things live

| What | Path |
|---|---|
| Workflows | `.github/workflows/` |
| Gate inventory and local run | `scripts/ci/README.md` |
| Runner manifests | `kubernetes/apps/base/actions-runner-system/` |
| Talos role | `gha-runner-scale-set/app/rbac.yaml` (`os:operator`) |
| Ruleset record | `docs/branch-protection.md` |
| Contention pin | `scripts/ci/validate-contention-test.py` |
| Renovate writer overlap | skill `renovate` |

## Procedures

- Runner down, ghost jobs, broker timeouts, cert reissue: [arc-runners.md](references/arc-runners.md).
- Which workflow posts what, and where it runs: [workflows.md](references/workflows.md).
- Adding or retargeting a gate: [scripts-ci.md](references/scripts-ci.md).
- Required checks and the ruleset payload: [branch-protection.md](references/branch-protection.md).

## Verify

- One gate: `python3 scripts/ci/<name>-test.py`.
- The set: `for f in scripts/ci/*-test.py; do python3 "$f" || echo "FAILED: $f"; done`.
- Docs gates (`docs-budget`, `doc-links`, `functional-comments-guard`) run in
  the `docs-guards` job. `functional-comments-guard` needs `DIFF_GUARD_BASE`.
- Live ruleset: `gh api repos/Aviator-Coding/home-ops/rulesets/21250320`.
