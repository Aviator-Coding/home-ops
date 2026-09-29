# OpenTofu conventions

Flux owns Kubernetes objects. OpenTofu owns state inside an application's API.
Today the only stack is `terraform/authentik/`.

| File | Role |
|---|---|
| `main.tofu` | providers |
| `variables.tofu` | inputs. `type` and `description`. Secrets `sensitive` |
| `outputs.tofu` | outputs. Same |
| `backend.tofu` | remote state. No `endpoints`, no `backend.tfvars` |
| `imports.tofu` | adopt live objects |
| `secrets.vals.yaml` | `ref+op://` for plan/init. Plain `AWS_ENDPOINT_URL_S3` is the non-secret exception |
| `secrets-ci.vals.yaml` | `ref+onepasswordconnect://` mirror. Absent means schema-only CI |
| `secrets-apply.vals.yaml` | apply token. Field absent until go-ahead |
| `*.tofu` | resources, grouped by subject |

`.tofu`, not `.tf`. Two-space indent. Resource names are singular nouns
(`authentik_application.echo`). Provider constraint uses `~>`.

Commit `.terraform.lock.hcl` with hashes for `linux_amd64` (CI),
`darwin_arm64` and `darwin_amd64`:

```bash
tofu providers lock -platform=linux_amd64 -platform=darwin_arm64 -platform=darwin_amd64
```

Always `vals exec -i -f <file> -- tofu ...` so credentials stay off disk and
PATH/mise survive. `-i` is required. There is no committed tfvars.

Local schema check, no credentials:

```bash
./scripts/ci/tofu-validate.sh
```

State files and plan files stay gitignored (`terraform/.gitignore` and the
guard in `tofu-validate.sh`).

`docs/authentik/terraform.md` is the short human pointer. Procedures live in
this skill. Section numbers in older comments mean:

| Old section | Here |
|---|---|
| state backend / port-forward | [state-and-credentials.md](state-and-credentials.md) |
| secrets and drift | [state-and-credentials.md](state-and-credentials.md) |
| apply gate | [apply-runbook.md](apply-runbook.md) |
| generated LiteLLM secret | [apply-runbook.md](apply-runbook.md) |
| CI plan | [ci-plan.md](ci-plan.md) |
