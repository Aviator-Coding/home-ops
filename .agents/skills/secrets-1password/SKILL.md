---
name: secrets-1password
description: "Read before adding or editing an ExternalSecret, PushSecret, ClusterSecretStore or 1Password item reference, a vals ref+op:// reference, or when an app fails auth while its ExternalSecret reports SecretSynced. Covers the Home-Lab vs Homelab vault split, the single-vault store for writes, and why SecretSynced does not mean non-empty."
---

# Secrets: ExternalSecret, PushSecret and 1Password

`Home-Lab` and `Homelab` are different vaults. Mixing them is the usual
reason a new secret never arrives.

## Tripwires

1. **Connect cannot see `Home-Lab`.** Vals (bootstrap and Talos) resolves
   `ref+op://Home-Lab/...` with the `op` CLI. In-cluster External Secrets
   uses 1Password Connect, which sees `Homelab`, `Automation`, and
   `Services` only. No ExternalSecret or PushSecret can read or write
   `Home-Lab`. Machine-maintained items live in a Connect-visible vault.
2. **A write that must land in one vault uses `onepassword-automation`.**
   The shared `onepassword` ClusterSecretStore lists three vaults and
   picks by priority, so the target is not deterministic. The
   single-vault store is cluster-scoped on purpose: a namespaced
   SecretStore may only reference a Secret in its own namespace, and the
   Connect token lives in `security`.
3. **`SecretSynced` / `Ready=True` does not mean non-empty.** ESO writes
   a present-but-empty 1Password field as a zero-length string and
   reports healthy. On an auth failure, check the Secret key length
   (`base64 -d | wc -c`), not the ExternalSecret status.
4. **Never seed a Secret with client-side `kubectl apply`.** The value is
   stored in `kubectl.kubernetes.io/last-applied-configuration`. Use
   `kubectl create`, or server-side apply, and do not print the value.
5. **Connect token and service-account token are different credentials.**
   `OP_SERVICE_ACCOUNT_TOKEN` is what `vals` uses for `Home-Lab` on a
   workstation. `OP_CONNECT_TOKEN` is a Connect access token, scoped to a
   whole vault (not an item) and used by CI against Connect. Do not reuse
   the cluster's ESO Connect token for CI. Skill `authentik-terraform`
   for the Automation-vault scope of the terraform-diff token.
6. **`resources/` is not a secret path.** Renovate ignores it, and
   plaintext in git is forbidden. Bootstrap secrets stay
   `ref+op://Home-Lab/...`.

## Where things live

| What | Path |
|---|---|
| App ExternalSecret | `kubernetes/apps/base/<ns>/<app>/app/externalsecret.yaml` |
| Shared store | ClusterSecretStore `onepassword` |
| Single-vault write store | `kubernetes/apps/base/security/external-secrets/stores/onepassword/` (`onepassword-automation`) |
| Bootstrap injection | `bootstrap/kustomize/apps/security/` via `vals` |
| Workstation token | `OP_SERVICE_ACCOUNT_TOKEN` in gitignored `.secrets.env`, vault `Home-Lab` |
| Postgres item recipe | [recipes.md](references/recipes.md) |

## Procedures

- New item, postgres-init fields, which vault: [recipes.md](references/recipes.md).

## Verify

- ExternalSecret `Ready=True` and every credential-shaped key has a
  non-zero decoded length.
- A PushSecret's `secretStoreRef` is `onepassword-automation` when the
  item must land in `Automation`.
- `kubectl get clustersecretstore onepassword,onepassword-automation`.
