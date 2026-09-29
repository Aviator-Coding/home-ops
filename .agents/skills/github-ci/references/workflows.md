# Workflow inventory

Runner below is the `runs-on` value in the file. The table in
`.github/workflows/README.md` has drifted; trust the workflow.

| Workflow | Runner | Role |
|---|---|---|
| `validate.yaml` | home-ops scale set | Schema and `scripts/ci/*-test.py` for paths flate never sees |
| `flate.yaml` | home-ops scale set | `flate test` / diff. Lenient envsubst: skill `flux-substitution` |
| `image-pull.yaml` | home-ops scale set | `talosctl image pull` of images the PR adds, plus a recovery pass for bare Docker Hub refs flate misses |
| `terraform-diff.yaml` | home-ops scale set | Read-only `tofu plan` when `secrets-ci.vals.yaml` exists. Skill `authentik-terraform` |
| `terraform-publish.yaml` | `ubuntu-latest` | Publish `terraform/` as an OCI artifact on push to `main` |
| `labeler.yaml` | home-ops scale set | Path labels. The only required check. Skill [branch-protection.md](branch-protection.md) |
| `label-sync.yaml` | home-ops scale set | Sync labels from `.github/labels.yaml`. Deletes labels absent from the file |
| `renovate.yaml` | `ubuntu-latest` | Rollback writer. Schedule commented out. `push` on Renovate config still fires. Skill `renovate` |
| `build-talosctl-busybox.yaml` | `ubuntu-latest` | `docker buildx` of the talosctl image. Needs Docker |
| `build-litellm-pgvector.yaml` | `ubuntu-latest` | Vector-store image. Skill `litellm-proxy` |
| `ai-pr-review.yaml` | home-ops scale set | Advisory comment. Never a required check. Skill `litellm-proxy` |
| `codeql.yml` | home-ops scale set | Actions-language analysis |
| `tag.yaml` | home-ops scale set | Monthly CalVer tags |
| `test-runner.yaml` | home-ops scale set | Weekly runner smoke |

## validate.yaml

No trigger-level `paths:` filter. Each job filters itself, and `Validate - Success`
always posts (success, skip, or failure). That is what makes an aggregate check
safe to require later. A trigger-level filter leaves the check `Expected` forever
on PRs outside the path list.

Jobs: `talos`, `versions`, `bootstrap`, `renovate-config`, `terraform`,
`python-tests`, plus the aggregate. `python-tests` `needs:` the lighter jobs so
`uv pip install litellm[proxy]` does not overlap their `jdx/mise-action` Setup
Tools, and uses `always()` so a skipped predecessor does not skip the tests.

`terraform` here is `tofu fmt` + `tofu validate -backend=false`. It cannot see a
destructive plan.

## image-pull

The pull job retries with backoff for blips. An expired client cert fails every
attempt the same way. See [arc-runners.md](arc-runners.md).

## Fork guard

Same-repo `if:` on `validate.yaml`, `image-pull.yaml`, `flate.yaml`,
`labeler.yaml` and `terraform-diff.yaml`. Add it to any new job on this runner.

## validate.yaml timeouts

Measured on PR #1481: the mise-action jobs (talos, versions, bootstrap, terraform)
take 15-31s uncontended, but one was cancelled at 602s inside Setup Tools when
eight jobs fanned out onto the runner pool, so they carry `timeout-minutes: 25`.
python-tests takes ~96s alone and 469s contended (the `uv pip` install of
`litellm[proxy]` went from 33s to 416s), so it carries 20 and is ordered after the
light jobs.
