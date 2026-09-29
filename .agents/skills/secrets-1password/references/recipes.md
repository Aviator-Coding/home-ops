# 1Password item recipes

## Vaults

| Consumer | Vault | Scheme |
|---|---|---|
| ExternalSecret / PushSecret (Connect) | `Homelab`, `Automation`, `Services` | ClusterSecretStore |
| Bootstrap and Talos `vals` | `Home-Lab` | `ref+op://Home-Lab/...` |
| Workstation `op` / service account | `Home-Lab` | `OP_SERVICE_ACCOUNT_TOKEN` |

`Home-Lab` is hyphenated and invisible to Connect. An item that ESO must
read cannot be created there.

The shared store's write target follows vault priority. A PushSecret
that must update one item uses ClusterSecretStore `onepassword-automation`
(`kubernetes/apps/base/security/external-secrets/stores/onepassword/`).
Do not create a namespaced SecretStore outside `security`: ESO rejects a
Secret reference that crosses namespaces.

## Empty fields

ESO does not reject an empty property. `media/plex` and `downloads/bazarr`
have both failed auth on an empty token while `SecretSynced` stayed true.
Zigbee2MQTT's network identity is the opposite case: those fields are the
network, and a wrong encoding re-forms it. Skill `home-automation`.

Check length, not status:

```bash
kubectl -n <ns> get secret <name> -o jsonpath='{.data.<KEY>}' | base64 -d | wc -c
```

## Postgres-backed apps

The hostname is not a 1Password field. The ExternalSecret hardcodes
`postgres-17-rw.database.svc.cluster.local`. Do not add `POSTGRES_DB_HOST`
to new items.

The app item holds the app's own fields (`POSTGRES_DB_NAME`,
`POSTGRES_DB_USER_NAME`, `POSTGRES_DB_USER_PASSWORD`, plus app secrets).
`POSTGRES_SUPER_PASS` stays on the shared `cloudnative-pg` item in
`Homelab`. The ExternalSecret extracts both. The `postgres-init` container
(`ghcr.io/home-operations/postgres-init`) reads `INIT_POSTGRES_*`:

| Env | Source |
|---|---|
| `INIT_POSTGRES_DBNAME` | app item `POSTGRES_DB_NAME` |
| `INIT_POSTGRES_USER` | app item `POSTGRES_DB_USER_NAME` |
| `INIT_POSTGRES_PASS` | app item `POSTGRES_DB_USER_PASSWORD` |
| `INIT_POSTGRES_SUPER_PASS` | `cloudnative-pg` / `POSTGRES_SUPER_PASS` |
| `INIT_POSTGRES_HOST` | hardcoded in the template |

Generate passwords with `openssl rand`. Put them in 1Password, then let
ESO project them. Never commit them, and never `kubectl apply` a
client-side Secret manifest that contains them.

## Seeding a one-off Secret

`kubectl apply -f` of a Secret (including `kubectl create --dry-run=client -o yaml | kubectl apply -f -`)
records the data in the last-applied annotation. Prefer:

```bash
kubectl -n <ns> create secret generic <name> --from-literal=<KEY>="$VALUE"
```

If the Secret already exists and must be replaced, use server-side apply
(`kubectl apply --server-side`) so the annotation is not written. Do not
echo `$VALUE`.

## Connect versus service account

- **Service account token** (`OP_SERVICE_ACCOUNT_TOKEN`): `op` and `vals`
  `ref+op://` on a workstation or in bootstrap. Scope it to `Home-Lab`
  for Talos render. It is not a Connect token.
- **Connect access token** (`OP_CONNECT_TOKEN`): HTTP bearer against
  `onepassword-connect`. Scope is the whole vault named at mint time, not
  one item. The terraform-diff CI token is a dedicated Automation-vault
  token, not the token the cluster's ESO deployment uses, so a CI leak
  can be revoked on its own. It can read every current and future item
  in `Automation`. Skill `authentik-terraform`.
