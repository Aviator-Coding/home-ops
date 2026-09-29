---
name: secrets-1password
description: "Read before adding or editing an ExternalSecret, PushSecret, ClusterSecretStore or 1Password item reference, a vals ref+op:// reference, or when an app fails auth while its ExternalSecret reports SecretSynced. Covers the Home-Lab vs Homelab vault split, the single-vault store for writes, and why SecretSynced does not mean non-empty."
---

# Secrets: ExternalSecret, PushSecret and 1Password

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`docs/organisation-services-1password-setup.md`](../../../docs/organisation-services-1password-setup.md) - 1Password Connect setup
- [`kubernetes/apps/base/security/external-secrets/stores/onepassword`](../../../kubernetes/apps/base/security/external-secrets/stores/onepassword) - ClusterSecretStores
- [`docs/monitoring/exporter-endpoint-repair-2026-09-20.md`](../../../docs/monitoring/exporter-endpoint-repair-2026-09-20.md) - empty-secret audit

`AGENTS.md` entries (search for the opening words):

- 1Password vaults: `Home-Lab` and `Homelab` are two DIFFERENT vaults

## Related skills

- `app-workloads`
