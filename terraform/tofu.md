# OpenTofu in this repo

OpenTofu manages configuration that lives in an application's own API.
Flux does not reconcile this tree. The only stack is
[`authentik/`](./authentik).

Skill `authentik-terraform`
([conventions.md](../.agents/skills/authentik-terraform/references/conventions.md)).

1. Nothing here applies itself. `terraform-diff.yaml` may post a read-only
   plan. `tofu apply` stays behind the go-ahead in
   [`apply-runbook.md`](../.agents/skills/authentik-terraform/references/apply-runbook.md).
2. State holds OAuth client secrets in plaintext. It stays in a private
   bucket, never in Git.
3. Adopt objects that already exist (`import` blocks). Create only what
   never lived on the instance (LiteLLM).
4. Secrets come from 1Password through `vals exec -i`. No committed tfvars.

```bash
cd terraform/authentik
vals exec -i -f secrets.vals.yaml -- tofu plan
./scripts/ci/tofu-validate.sh
```

Never `tofu destroy` this stack. It unbinds ExtAuth.
