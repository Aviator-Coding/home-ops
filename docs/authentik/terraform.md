# Authentik configuration as code (OpenTofu)

Status as of 2026-08-26: **the code is written and validated; nothing has been
applied.** No `tofu apply` has run, and no Authentik-mutating API call has been
made. The live instance is exactly as it was.

Authentik is the SSO for the whole cluster, including the ExtAuth in front of the
public gateway. A wrong apply here does not degrade one app; it locks or unlocks
every SSO-gated surface at once. That is why the apply procedure below has an
explicit approval gate and why CI is deliberately unable to reach the instance.

- Code: [`terraform/authentik/`](../../terraform/authentik)
- Conventions: [`terraform/tofu.md`](../../terraform/tofu.md)
- App deployment (unchanged by this): `kubernetes/apps/base/security/authentik/`

## 1. The inventory this code was written from

Taken read-only on 2026-08-26, before a line of OpenTofu was written. The code
adopts what is here; it does not propose a different configuration.

### Applications and providers, all hand-created

| Application | Slug | Provider | Provider kind | Notes |
| ----------- | ---- | -------- | ------------- | ----- |
| Coder | `coder` | 4 | OAuth2 | explicit-consent authorization flow |
| open-webui | `open-webui` | 36 | OAuth2 | only app with a launch URL and a logout URI |
| pgAdmin | `pg-admin` | 2 | OAuth2 | two redirect URIs (`pgadmin.` and `pg.`) |
| echo | `echo` | 37 | Proxy | **the ExtAuth provider**, see below |

All four applications sit at `policy_engine_mode = "any"` with **zero policy
bindings**, so any authenticated user can reach any of them. That is the current
state, recorded here as fact. Introducing group-scoped access is a real
authorization change and belongs in its own reviewed change, not folded into
adoption.

### The ExtAuth provider, provider 37

Live name is `sklab-externel-auth-provider`, misspelling included. It runs in
`forward_domain` mode for `external_host = https://auth.sklab.dev` with
`cookie_domain = .sklab.dev` and `intercept_header_auth = true`.

It is bound to `authentik Embedded Outpost`
(`a827266f-21ed-4a8b-a080-7b59a75a042e`), and two Envoy Gateway SecurityPolicies
forward to that outpost's Service:

- `kubernetes/apps/base/network/echo/app/securitypolicy.yaml`
- `kubernetes/apps/base/ai/agentgateway/app/policies/authentik-policy.yaml`
  (covers the agentgateway `internal` **and** `public` https listeners)

This is the single most dangerous object in the stack.

### Everything else is Authentik's own

| Object | Count | Owner |
| ------ | ----- | ----- |
| Flows | 14 | built-in blueprints (`default/flow-*.yaml`) |
| Stages | 18 | built-in blueprints |
| Policies | 14 | built-in blueprints |
| Property mappings | 36 | built-in blueprints (`system/providers-*.yaml`), all carry a `managed` marker |
| Groups | 2 | `authentik Admins`, `authentik Read-only` |
| Brands | 1 | `authentik-default`, stock |
| Certificates | 2 | self-signed + internal JWT, generated at first boot |
| Sources | 1 | `authentik Built-in` |
| Outposts | 1 | embedded, `managed = goauthentik.io/outposts/embedded` |

There are **no** hand-written flows, stages, policies or property mappings on
this instance. 28 `blueprintinstance` rows are `successful` and cover all of the
above, which is why the stack references them as data sources and owns none of
them.

### How the inventory was taken

Through read-only `SELECT`s against Authentik's Postgres database, not the API:

```bash
export KUBECONFIG=./kubeconfig
PRIMARY=$(kubectl -n database get pods -l 'cnpg.io/cluster=postgres-17,role=primary' \
  -o jsonpath='{.items[0].metadata.name}')
kubectl -n database exec "$PRIMARY" -c postgres -- \
  psql -d authentik -A -F'|' -c "select id, name from authentik_core_provider order by id;"
```

Two reasons. There was no Authentik API token to read with (see section 5), and
the database is the only place that yields the exact primary keys the import
blocks need. `client_secret` was deliberately never selected.

## 2. Import strategy

Every resource the stack declares is paired with an `import` block in
[`imports.tofu`](../../terraform/authentik/imports.tofu). Without them the first
plan would show nine creates, and applying that would mint new client secrets and
stand up a second forward-auth provider.

| Resource | Import ID | Format |
| -------- | --------- | ------ |
| `authentik_provider_oauth2.oauth2["coder"]` | `4` | numeric provider pk |
| `authentik_provider_oauth2.oauth2["open-webui"]` | `36` | numeric provider pk |
| `authentik_provider_oauth2.oauth2["pg-admin"]` | `2` | numeric provider pk |
| `authentik_provider_proxy.forward_auth` | `37` | numeric provider pk |
| `authentik_application.oauth2["coder"]` | `coder` | slug |
| `authentik_application.oauth2["open-webui"]` | `open-webui` | slug |
| `authentik_application.oauth2["pg-admin"]` | `pg-admin` | slug |
| `authentik_application.echo` | `echo` | slug |
| `authentik_outpost_provider_attachment.forward_auth` | `a827266f-...-7b59a75a042e:37` | `<outpost uuid>:<provider pk>` |

Each of those resources has a passthrough importer in the provider source, so the
ID is passed to the read call unchanged.

Three properties make the adoption non-destructive, and all three are load-bearing:

- **`client_secret` is not declared.** It is optional+computed, so after import
  OpenTofu carries the live secret in state and plans no change. Declaring it
  would require reproducing the current value exactly; getting that wrong rotates
  the secret and breaks every login for that app with no error at plan time.
- **`property_mappings` is declared on every provider.** It is a plain optional
  list, so omitting it would plan the *removal* of the scopes each provider has.
- **`grant_types` is not declared.** It is optional+computed and the live values
  are Authentik's stock set.

The import blocks are left in the tree permanently. OpenTofu ignores an import
block whose target is already in state, and keeping them means the stack can be
rebuilt from scratch if the state file is ever lost.

## 3. State backend

State lives in the cluster's own Ceph RGW object store.

**Why RGW and not the VolSync MinIO target.** The MinIO used by VolSync is
external to the cluster (`nas.sklab.dev:9000`, see
`kubernetes/components/volsync/minio/externalsecret.yaml`). RGW is the in-cluster,
Flux-managed S3 service already used for application buckets, and its HTTPRoute
`s3.sklab.dev` is published on the `envoy-internal` gateway only, so state is
never reachable from the public gateway.

**The bucket must stay private.** The state file contains every OAuth2 client
secret this stack adopts, in plaintext. No bucket policy, no public ACL, and the
RGW user's keys live only in 1Password.

**Why a hand-made bucket and not an `ObjectBucketClaim`.** The `ceph-bucket`
StorageClass is `reclaimPolicy: Delete`. An OBC would tie the lifetime of the
state bucket to a Flux Kustomization with `prune: true`, so pruning the claim
would delete the bucket and the state with it. A standalone RGW user is the same
pattern the existing `volsync` RGW user already uses.

### Bootstrap, run once

```bash
export KUBECONFIG=./kubeconfig

# 1. Dedicated RGW user. Record the returned access_key and secret_key.
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- \
  radosgw-admin user create --uid=terraform --display-name="OpenTofu state"

# 2. Bucket, through the S3 API. Any S3 client works; this uses the tools pod.
AWS_ACCESS_KEY_ID=<access_key> AWS_SECRET_ACCESS_KEY=<secret_key> \
  aws --endpoint-url https://s3.sklab.dev s3api create-bucket --bucket terraform-state

# 3. Versioning, so a corrupted or truncated state can be rolled back.
AWS_ACCESS_KEY_ID=<access_key> AWS_SECRET_ACCESS_KEY=<secret_key> \
  aws --endpoint-url https://s3.sklab.dev s3api put-bucket-versioning \
  --bucket terraform-state --versioning-configuration Status=Enabled

# 4. Confirm it is private: this must NOT return a public grant.
AWS_ACCESS_KEY_ID=<access_key> AWS_SECRET_ACCESS_KEY=<secret_key> \
  aws --endpoint-url https://s3.sklab.dev s3api get-bucket-acl --bucket terraform-state
```

Then store the two keys in 1Password as described in section 4.

State locking uses S3-native conditional writes (`use_lockfile = true`), which
RGW supports. There is no DynamoDB equivalent and none is needed.

One interaction worth knowing: `cephConfig.client.rgw.rgw_sigv4_insecure` is
currently `"true"` on this cluster (see the 2026-08-23 entry in
`docs/ceph-cluster-changelog.md`). It relaxes SigV4 header checking, so it cannot
break OpenTofu's S3 client today. When that flag is removed alongside a Tentacle
image bump, re-run a `tofu plan` to confirm the backend still authenticates.

## 4. Secrets

One 1Password item, `authentik-terraform`, in the `Home-Lab` vault (the vault
`vals` is wired to in this repo):

| Field | Contents |
| ----- | -------- |
| `AUTHENTIK_TOKEN` | Authentik API token, see section 5 |
| `CODER_CLIENT_ID` | existing `client_id` of provider 4 |
| `OPEN_WEBUI_CLIENT_ID` | existing `client_id` of provider 36 |
| `PGADMIN_CLIENT_ID` | existing `client_id` of provider 2 |
| `TF_STATE_ACCESS_KEY_ID` | RGW `terraform` user access key |
| `TF_STATE_SECRET_ACCESS_KEY` | RGW `terraform` user secret key |

The three client IDs must be the values already in use. They are read out of the
live instance, never invented:

```bash
kubectl -n database exec "$PRIMARY" -c postgres -- psql -d authentik -A -F'|' \
  -c "select provider_ptr_id, client_id from authentik_providers_oauth2_oauth2provider
      where provider_ptr_id in (2,4,36) order by provider_ptr_id;"
```

[`secrets.vals.yaml`](../../terraform/authentik/secrets.vals.yaml) maps those
fields to the environment. It holds only `ref+op://` references and is safe in
Git, the same as `bootstrap/kustomize/apps/security/secret.yaml`.

## 5. The API token

There is currently **no Authentik API token anywhere** in this repo or the
cluster. The `authentik` 1Password item has 20 fields and none is a token; the
ExternalSecret template does not create one; the only row in
`authentik_core_token` is the embedded outpost's own autogenerated credential,
which belongs to the outpost service account and must not be reused.

Two tokens are wanted, in this order:

1. **A read-only token, to produce plan evidence.** `tofu plan` with import
   blocks performs reads only, so a service account in the `authentik Read-only`
   group is enough to prove the adoption is clean. This token cannot change
   anything, which is exactly why it should come first.
2. **A write-capable token, only when an apply is approved.** A service account
   with permission to manage applications, providers and outposts.

To create one: Directory -> Users -> Create a service account, add it to the
appropriate group, then Directory -> Tokens -> Create with intent `API`. Put the
token in `authentik-terraform/AUTHENTIK_TOKEN`.

## 6. Planning

```bash
cd terraform/authentik
vals exec -f secrets.vals.yaml -- tofu init
vals exec -f secrets.vals.yaml -- tofu plan
```

A correct first plan imports nine resources and shows **no** creates, no
destroys, and no changes to any live object.

If you want plan evidence before the state bucket exists, move `backend.tofu`
aside so OpenTofu falls back to a local state file, plan, then restore it and
delete the local state. Plans are read-only against Authentik either way.

### What to confirm on that first plan

These could not be settled without an API token, because they depend on how the
provider normalizes values the database stores as empty strings. None is
destructive, but all should be understood before an apply rather than after:

- `meta_launch_url = ""` and `group = ""` on the applications that have no launch
  URL or group. The database holds `""`; the code declares `""`.
- `internal_host = ""` and `skip_path_regex = ""` on the proxy provider. Both are
  unused in `forward_domain` mode.
- Ordering of `property_mappings`. The data source returns IDs in its own order;
  if the only diff is a reordering of the same set, that is benign, but confirm
  it *is* the same set before applying.
- `meta_icon`, `meta_description` and `meta_publisher` are omitted from the code.
  All three are empty on every application, and all three are cosmetic.
- `allowed_redirect_uris` on providers 2, 4 and 36 have no `redirect_uri_type`
  key in the database, while provider 37's entries do. The code mirrors the
  database.

Anything else in the plan is a defect in the code. Fix the code, do not apply
around it.

## 7. Applying

**`tofu apply` requires an explicit go-ahead. A green CI run does not grant it,
and neither does a clean plan.**

Pre-apply checklist:

1. A `tofu plan` from the current commit is in hand, and it shows only the nine
   imports with no creates, no destroys, and no changes.
2. Someone other than the plan's author has read it, specifically the lines
   touching `authentik_provider_proxy.forward_auth` and
   `authentik_outpost_provider_attachment.forward_auth`.
3. A second browser session is already authenticated to `auth.sklab.dev` as an
   admin, so a broken ExtAuth cannot lock you out of the tool you need to fix it.
4. The apply is being run at a time when breaking SSO is survivable.

```bash
cd terraform/authentik
vals exec -f secrets.vals.yaml -- tofu plan -out=tfplan   # gitignored
vals exec -f secrets.vals.yaml -- tofu apply tfplan
rm -f tfplan
```

Apply a saved plan, never a bare `tofu apply`. The saved plan is what was
reviewed; a bare apply re-plans against whatever the instance looks like now.

Immediately afterwards, verify ExtAuth is still intact:

```bash
kubectl -n security get pods -l app.kubernetes.io/name=authentik
curl -sSI https://echo.sklab.dev | head -1     # expect a redirect to auth.sklab.dev
```

### Rollback

The adoption apply is a no-op by construction, so there is nothing to roll back
from a clean plan. If a later change breaks something:

- **Config change**: revert the commit and apply the reverted plan.
- **Provider unbound from the outpost**: re-attach it in the UI at Applications
  -> Outposts -> `authentik Embedded Outpost`. This is the fastest path back and
  does not need OpenTofu.
- **State lost or corrupted**: the bucket is versioned; restore the previous
  object version. Failing that, the import blocks in `imports.tofu` rebuild state
  from the live instance.

### Never run destroy

`tofu destroy` in this stack unbinds ExtAuth from the embedded outpost and
deletes four applications and four providers. There is no scenario where that is
the right move on a live cluster.
