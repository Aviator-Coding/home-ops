# OpenTofu in this repo

OpenTofu manages the configuration of systems that live *outside* the Flux
reconciliation loop. Flux owns Kubernetes objects; OpenTofu owns the state inside
an application's own API, where a Helm chart cannot reach.

Today there is exactly one stack: [`authentik/`](./authentik), which manages the
applications and providers on the live Authentik SSO instance.

## Ground rules

These are not style preferences. Each one exists because breaking it has a
specific, known consequence.

1. **Nothing in this tree applies itself.** There is no CI job, no Flux
   Kustomization, and no cron that runs `tofu apply`. Every apply is a deliberate
   operator action. The `terraform` job in `.github/workflows/validate.yaml` runs
   with `-backend=false` and no credentials, by design.
2. **State is a secret.** It holds every value the provider read back, including
   OAuth2 client secrets, in plaintext. It lives in a private bucket, never in
   Git, and `terraform/.gitignore` plus a guard in `scripts/ci/tofu-validate.sh`
   both enforce that.
3. **Adopt, never recreate.** These stacks manage things that already exist and
   are in use. Every resource is paired with an `import` block. A plan that
   proposes to create or destroy an object that is already live is a bug in the
   code, not a step in the process.
4. **Secrets come from 1Password at invocation time.** Each stack ships a
   `secrets.vals.yaml` holding only `ref+op://` references, resolved by `vals`,
   the same mechanism `bootstrap/` and the `just talos` render path already use.
   The authentik stack also ships `secrets-apply.vals.yaml` for captain-approved
   apply only (a separate field that is absent until approval, so stray apply
   fails closed). There is no committed `tfvars` and no committed `backend.tfvars`.

## File organization

| File               | Purpose                                                     |
| ------------------ | ----------------------------------------------------------- |
| `main.tofu`        | `required_providers`, provider configuration                 |
| `variables.tofu`   | Input variable declarations                                  |
| `outputs.tofu`     | Output value declarations, if the stack has any              |
| `backend.tofu`     | Remote state configuration                                   |
| `imports.tofu`     | `import` blocks adopting the existing live objects           |
| `secrets.vals.yaml`| `ref+op://` environment for plan/init/state; references only |
| `secrets-apply.vals.yaml` | apply-only env; token field absent until approval |
| `*.tofu`           | Resources, grouped by subject (applications, flows, ...)     |

Files use the `.tofu` extension, not `.tf`. Renovate's built-in `terraform`
manager matches `**/*.tofu` by default and maintains `.terraform.lock.hcl`, so
provider pins are kept current without a custom manager.

## Style conventions

- **Indentation**: two spaces per nesting level
- **Naming**: lowercase with underscores (`forward_auth`, not `forwardAuth`)
- **Resource names**: descriptive nouns, singular (`authentik_application.echo`)
- **Variables**: must declare `type` and `description`; mark secrets `sensitive`
- **Outputs**: must declare `description`; mark secrets `sensitive`
- **Version pinning**: use `~>` (e.g. `~> 2026.5.1`)
- **Lock files**: commit `.terraform.lock.hcl`, with hashes for every platform
  that runs it. CI runs on linux_amd64; operators run darwin_arm64. Refresh with
  `tofu providers lock -platform=linux_amd64 -platform=darwin_arm64 -platform=darwin_amd64`.

## Commands

Always through `vals exec`, so credentials never touch the filesystem:

```bash
cd terraform/authentik

# One time, and after any provider version change
vals exec -i -f secrets.vals.yaml -- tofu init

# The only command that is safe to run unprompted
vals exec -i -f secrets.vals.yaml -- tofu plan

# Inspect adopted state
vals exec -i -f secrets.vals.yaml -- tofu state list
```

Always pass `-i` so PATH/mise and the op session survive into the child. File
keys still win over parent env; do not try to override `TF_VAR_*` with an export.

Validation, which needs no credentials and is what CI runs:

```bash
./scripts/ci/tofu-validate.sh
```

## Applying

`tofu apply` against the Authentik stack changes the cluster's live SSO. Read
[`docs/authentik/terraform.md`](../docs/authentik/terraform.md) before running
it; the approval gate and the pre-apply checklist are there, not here.

Never run `tofu destroy` in this tree. There is no scenario in a home cluster
where tearing down the SSO configuration is the right move, and for the Authentik
stack it would unbind ExtAuth from the embedded outpost and lock every SSO-gated
surface at once.
