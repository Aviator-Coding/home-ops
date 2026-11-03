# Workflow inventory

Runner below is the `runs-on` value in the file. The table in
`.github/workflows/README.md` has drifted; trust the workflow.

| Workflow | Runner | Role |
|---|---|---|
| `ci.yaml` | `ubuntu-latest` | `CI Success`, the one gate: calls `flate.yaml` and `validate.yaml` |
| `validate.yaml` | `ubuntu-latest` (called by `ci.yaml`) | Schema and the `# ci: gate` `scripts/ci` tests for paths flate never sees |
| `advisory-tests.yaml` | `ubuntu-latest` | The other `scripts/ci` tests, minus nightly and docs guards. Never blocks; kept out of `validate.yaml` so it does not hold `CI Success` |
| `flate.yaml` | `ubuntu-latest` (called by `ci.yaml`) | `flate test` / diff. Lenient envsubst: skill `flux-substitution` |
| `image-pull.yaml` | `ubuntu-latest`; only `pull` on the home-ops scale set | `talosctl image pull` of images the PR adds, plus a recovery pass for bare Docker Hub refs flate misses |
| `terraform-diff.yaml` | home-ops scale set | Read-only `tofu plan` when `secrets-ci.vals.yaml` exists. Skill `authentik-terraform` |
| `labeler.yaml` | `ubuntu-latest` | Path labels. The only required check. Skill [branch-protection.md](branch-protection.md) |
| `label-sync.yaml` | `ubuntu-latest` | Sync labels from `.github/labels.yaml`. Deletes labels absent from the file |
| `renovate.yaml` | `ubuntu-latest` | Rollback writer. Schedule commented out. `push` on Renovate config still fires. Skill `renovate` |
| `ai-pr-review.yaml` | home-ops scale set | Advisory comment. Never a required check. Skill `litellm-proxy` |
| `tag.yaml` | `ubuntu-latest` | Monthly CalVer tag plus release, through the bot App token |

## validate.yaml

No trigger-level `paths:` filter. Each job filters itself, and `Validate - Success`
always posts (success, skip, or failure). That is what makes an aggregate check
safe to require later. A trigger-level filter leaves the check `Expected` forever
on PRs outside the path list.

Jobs: `talos`, `versions`, `bootstrap`, `renovate-config`, `terraform`,
`docs-guards`, `lint`, `python-tests` (the `# ci: gate` files), plus the aggregate that
`ci.yaml`'s `CI Success` reads. A called workflow reports only when every job ends, so
the advisory tests live in `advisory-tests.yaml`.

`lint` runs `yamllint -s .`, `actionlint`, `zizmor --offline --min-severity medium`
and `shellcheck` over the whole tree (configs: `.yamllint.yaml`, `.github/actionlint.yaml`,
`.github/zizmor.yml`). The same tools are pre-commit hooks; `yamlfmt` is manual-stage only.
`ci.yaml` passes `flate.yaml` exactly `BOT_APP_ID` and `BOT_APP_PRIVATE_KEY`, never
`secrets: inherit`.

`terraform` here is `tofu fmt` + `tofu validate -backend=false`. It cannot see a
destructive plan.

## image-pull

The pull job retries with backoff for blips. An expired client cert fails every
attempt the same way. See [arc-runners.md](arc-runners.md).

## Fork guard

Same-repo `if:` on `validate.yaml`, `advisory-tests.yaml`, `image-pull.yaml`, `flate.yaml`,
`labeler.yaml` and `terraform-diff.yaml`. One copy on the `filter` job covers every job
that only `needs: filter`; a job with `always()` or `!cancelled()` carries its own. Add it
to any new job that runs on ARC or reads a secret.

## App tokens

The bot App token is minted only where a job writes: the flate `diff` sticky comment,
`labeler`, `tag`. Checkout of this public repo passes no token.

Deleted 2026-11-02 after comparing with onedr0p/home-ops: `test-runner.yaml`, `codeql.yml`,
`terraform-publish.yaml` (nothing consumed its artifact). GitHub keeps a registration for a
deleted file; the owner disables those with `gh api` (an owner step, not Git).

## validate.yaml timeouts

Measured on PR #1481: the mise-action jobs (talos, versions, bootstrap, terraform)
take 15-31s uncontended, but one was cancelled at 602s inside Setup Tools when
eight jobs fanned out onto the runner pool, so they carry `timeout-minutes: 25`.
The advisory tests took ~96s alone and 469s contended (the `uv pip` install of
`litellm[proxy]` went from 33s to 416s), so they carry 20. They no longer wait on
the light jobs: hosted runners share no pool.
