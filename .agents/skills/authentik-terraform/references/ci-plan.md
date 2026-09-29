# CI plans

Two workflows, two strengths.

| Workflow | What it does |
|---|---|
| `validate.yaml` terraform job, `scripts/ci/tofu-validate.sh` | `tofu fmt -check` and `tofu validate -backend=false`. No credentials. Cannot tell a destructive plan from a safe one |
| `terraform-diff.yaml` | On same-repo PRs that touch `terraform/**`: real `tofu init` and `tofu plan -lock=false`, plan posted as a comment |

`terraform-diff` runs on `gha-runner-scale-set-aviator-coding-home-ops` because
Connect and RGW are ClusterIP-only. The runner has no Kubernetes API and
cannot port-forward. `secrets-ci.vals.yaml` is the Connect mirror of
`secrets.vals.yaml`.

A stack directory with no `secrets-ci.vals.yaml` stays schema-only. If the
file exists and `OP_CONNECT_TOKEN` is unset, the workflow warns and takes the
same schema-only path. It never half-runs vals.

CI does not mint, read or reference `AUTHENTIK_APPLY_TOKEN`. It does not run
`tofu apply`. A `tofu plan -out` file in that workflow is ephemeral.

`OP_CONNECT_TOKEN` reads the entire Automation vault. See
[state-and-credentials.md](state-and-credentials.md). Fork PRs never reach
this runner (skill `github-ci`). A same-repo PR from a write collaborator does.

## Adding another stack

Copy the file layout in [conventions.md](conventions.md). Opt into live plans
with a `secrets-ci.vals.yaml` of `ref+onepasswordconnect://` refs. Until that
file exists, `terraform-diff` stays schema-only for the new directory.

Renovate's built-in terraform manager already matches `**/*.tofu` and
maintains `.terraform.lock.hcl`. No custom manager. The authentik provider
pin in `.renovate/overrides.json5` tracks the server release line. Skill
`renovate`.
