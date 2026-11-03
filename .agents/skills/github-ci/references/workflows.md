# Workflow inventory

Runner below is the `runs-on` value in the file; trust the workflow over any table.

| Workflow | Runner | Role |
|---|---|---|
| `ci.yaml` | `ubuntu-latest` | `CI Success`, the one gate. Flat: no reusable-workflow calls |
| `tests.yaml` | `ubuntu-latest` | Every non-gate `scripts/ci` test, minus the docs guards. Nightly, dispatch, and push to main on `scripts/ci/**`. Never on PRs; a failure opens or updates the `ci-nightly` issue |
| `image-pull.yaml` | `ubuntu-latest`; only `pull` on the home-ops scale set | Advisory. `talosctl image pull` of images the PR adds, plus a recovery pass for bare Docker Hub refs flate misses |
| `terraform-diff.yaml` | home-ops scale set | Advisory. Read-only `tofu plan` when `secrets-ci.vals.yaml` exists. Skill `authentik-terraform` |
| `labeler.yaml` | `ubuntu-latest` | Path labels. Skill [branch-protection.md](branch-protection.md) |
| `label-sync.yaml` | `ubuntu-latest` | Sync labels from `.github/labels.yaml`. Deletes labels absent from the file |
| `renovate.yaml` | `ubuntu-latest` | Manual dispatch only (dry runs, debugging). Skill `renovate` |

## ci.yaml

No trigger-level `paths:` filter, so `CI Success` always posts. A trigger-level
filter leaves a required check `Expected` forever on PRs outside the path list.

- `changes`: the only fork guard, and one `action-changed-files` step per area
  (`kubernetes`, `talos`, `versions`, `bootstrap`, `renovate`, `terraform`).
- Path-filtered: `flate-test`, `flate-diff` (hr/ks sticky comments, bot App token),
  `talos`, `versions`, `bootstrap`, `renovate-config`, `terraform`.
- Every PR: `lint` (`yamllint -s .`, `actionlint`, `zizmor --offline --min-severity medium`,
  `shellcheck`, then the three docs guards) and `gate-tests` (the `# ci: gate` files).
- `success` (`CI Success`): `always()`, needs every job, red on failure, cancel or fork PR.

Every job other than `changes` and `success` needs `changes` and has no
`always()`/`!cancelled()`, so a fork PR skips all of them. Configs: `.yamllint.yaml`,
`.github/actionlint.yaml`, `.github/zizmor.yml`; the same tools are pre-commit hooks.
`terraform` is `tofu fmt` + `tofu validate -backend=false`; it cannot see a destructive plan.
Pin: `scripts/ci/workflow-contract-test.py`.

## image-pull

The pull job retries with backoff for blips. An expired client cert fails every
attempt the same way. talosctl comes from the runner's `talosctl-busybox` init
container. See [arc-runners.md](arc-runners.md).

## Fork guard

Same-repo `if:` on `ci.yaml` `changes`, `image-pull.yaml`, `labeler.yaml` and
`terraform-diff.yaml`. Add it to any new job that runs on ARC or reads a secret.

## App tokens

The bot App token is minted only where a job writes: the `flate-diff` sticky comment,
`labeler`, the terraform plan comment, `renovate`. Checkout of this public repo passes no token.

## Retired

2026-11-02: `test-runner.yaml`, `codeql.yml`, `terraform-publish.yaml`, then
`flate.yaml` and `validate.yaml` (folded into `ci.yaml`), `advisory-tests.yaml` and
`nightly-tests.yaml` (now `tests.yaml`), `tag.yaml` (nothing used the CalVer tags).
GitHub drops the registration of a deleted workflow file by itself; only entries
that were `disabled_manually` linger in `gh workflow list --all`.
