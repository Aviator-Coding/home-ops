# Apply runbook

`tofu plan` is always safe. `tofu apply` changes live SSO, including the
forward-auth provider every ExtAuth SecurityPolicy uses.

## Before apply

1. The review plan (read-only token, no `-out`) shows zero destroys and only
   the change this commit declares. A create or destroy of an object that
   already lives on the instance is a bug in the stack.
2. A second person has read the diff on
   `authentik_provider_proxy.forward_auth` and its outpost attachment.
3. A second browser session is already logged into `auth.${SECRET_DOMAIN}` as
   an admin, so a broken ExtAuth does not lock out the fix path.
4. Breaking SSO is survivable at this hour.

`secrets.vals.yaml` injects the durable read-only `AUTHENTIK_TOKEN`. It
returns 403 on writes. Do not put the write token in that field. File keys
beat an exported `TF_VAR_`, so an override in the shell does not swap tokens.

`secrets-apply.vals.yaml` reads `AUTHENTIK_APPLY_TOKEN`. That 1Password field
is absent until approval. OpenTofu refuses to apply a saved plan whose
`TF_VAR_*` values differ from the plan, so the `-out` plan and the apply use
the same vals file.

## Sequence

```bash
cd terraform/authentik

# 1. Review plan. Approval is granted against this output. It cannot be applied.
vals exec -i -f secrets.vals.yaml -- tofu plan

# 2. After an explicit go-ahead, place the write token in
#    Automation/authentik-terraform AUTHENTIK_APPLY_TOKEN.

# 3. Re-plan with -out. Confirm it matches the approved review plan.
vals exec -i -f secrets-apply.vals.yaml -- tofu plan -out=authentik.tfplan

# 4. Apply that file. Never a bare `tofu apply`.
vals exec -i -f secrets-apply.vals.yaml -- tofu apply authentik.tfplan

# 5. Remove the plan and the apply token.
rm -f authentik.tfplan
```

Remove `AUTHENTIK_APPLY_TOKEN` from 1Password immediately after. The write
role can `change_flow` on every flow. Compare a before/after snapshot of
flows and stage bindings taken from the database, not Terraform's summary.

Then confirm ExtAuth still redirects and the server pod is Ready.

## Rollback

- Revert the commit and apply the reverted saved plan.
- If the provider is unbound from the outpost, re-attach it in the UI:
  Applications, Outposts, `authentik Embedded Outpost`. That does not need
  OpenTofu.
- State object corrupted: the bucket is versioned. Restore the previous
  object. `imports.tofu` can rebuild state from the live instance if the
  object is gone.

## Destroy

`tofu destroy` unbinds ExtAuth and removes the adopted applications plus the
LiteLLM provider, application, scope mapping and invalidation flow. There is
no live-cluster case for it.

## Push a generated LiteLLM client to the cluster

OpenTofu cannot write Kubernetes. After an apply that rotates the LiteLLM
OAuth client:

```bash
CID=$(tofu -chdir=terraform/authentik output -raw litellm_client_id)
CSEC=$(tofu -chdir=terraform/authentik output -raw litellm_client_secret)
kubectl -n ai create secret generic litellm-sso-credentials \
  --from-literal=LITELLM_SSO_CLIENT_ID="$CID" \
  --from-literal=LITELLM_SSO_CLIENT_SECRET="$CSEC"
```

If the Secret already exists, delete it and create it again, or use
`kubectl apply --server-side`. Client-side apply copies both values into
`kubectl.kubernetes.io/last-applied-configuration`.

The PushSecret then writes 1Password `Automation/litellm-sso`, and the
ExternalSecret refreshes `litellm-secret`. Compare the client id in 1Password
with the Authentik provider. They must match. The push uses ClusterSecretStore
`onepassword-automation`.
