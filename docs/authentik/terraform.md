# Authentik OpenTofu

Live SSO state. Flux does not apply this stack. `tofu plan` is safe.
`tofu apply` and `tofu destroy` need an explicit go-ahead. A green PR is not that go-ahead.

Skill `authentik-terraform`:

- Apply sequence, rollback, LiteLLM client push: [apply-runbook.md](../../.agents/skills/authentik-terraform/references/apply-runbook.md)
- State bucket, tokens, `OP_CONNECT_TOKEN` scope: [state-and-credentials.md](../../.agents/skills/authentik-terraform/references/state-and-credentials.md)
- What CI plans: [ci-plan.md](../../.agents/skills/authentik-terraform/references/ci-plan.md)
- Server upgrade checks, including the Base URL setting required before 2026.11: [upgrade-checklist.md](../../.agents/skills/authentik-terraform/references/upgrade-checklist.md)

Review plan (read-only token):

```bash
cd terraform/authentik
vals exec -i -f secrets.vals.yaml -- tofu plan
```

After an explicit go-ahead, re-plan with `-out` through `secrets-apply.vals.yaml`
and apply that file. Never a bare `tofu apply`. Remove `AUTHENTIK_APPLY_TOKEN`
from 1Password item `Automation/authentik-terraform` immediately afterwards.
