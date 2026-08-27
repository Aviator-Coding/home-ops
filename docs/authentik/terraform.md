# Authentik configuration as code (OpenTofu)

Status as of 2026-08-26: **the code is written, a read-only plan has been run
against the live instance, and no `tofu apply` has happened.** The adoption plan
is clean: 9 imports, 0 to add, 0 to destroy (section 6).

One set of changes *was* made to the instance, under an explicit captain
authorization scoped to a read-only credential: a `tofu-readonly` service account
in the `authentik Read-only` group and its non-expiring API token (section 5).
Proven read-only against the live API - every write attempt returns 403. No
application, provider, flow or outpost was touched.

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

Then add the two keys to the `authentik-terraform` item in the **`Automation`**
vault as `TF_STATE_ACCESS_KEY_ID` / `TF_STATE_SECRET_ACCESS_KEY` (section 4).

State locking uses S3-native conditional writes (`use_lockfile = true`), which
RGW supports. There is no DynamoDB equivalent and none is needed.

One interaction worth knowing: `cephConfig.client.rgw.rgw_sigv4_insecure` is
currently `"true"` on this cluster (see the 2026-08-23 entry in
`docs/ceph-cluster-changelog.md`). It relaxes SigV4 header checking, so it cannot
break OpenTofu's S3 client today. When that flag is removed alongside a Tentacle
image bump, re-run a `tofu plan` to confirm the backend still authenticates.

## 4. Secrets

One 1Password item, `authentik-terraform`, in the **`Automation`** vault:

| Field | Contents |
| ----- | -------- |
| `AUTHENTIK_TOKEN` | read-only Authentik API token, see section 5 |
| `CODER_CLIENT_ID` | existing `client_id` of provider 4 |
| `OPEN_WEBUI_CLIENT_ID` | existing `client_id` of provider 36 |
| `PGADMIN_CLIENT_ID` | existing `client_id` of provider 2 |
| `TF_STATE_ACCESS_KEY_ID` | RGW `terraform` user access key, added during section 3 |
| `TF_STATE_SECRET_ACCESS_KEY` | RGW `terraform` user secret key, added during section 3 |

**Why `Automation` and not `Home-Lab`.** The rest of this repo's `vals`
references resolve against `Home-Lab`. This item cannot live there. Every
in-cluster write goes through 1Password Connect, and Connect can only see
`Homelab`, `Automation` and `Services` - verified by querying it directly.
`Home-Lab` (hyphenated) is a *different vault* from `Homelab`, and Connect cannot
reach it at all, so an item there could never be machine-maintained. Do not
"tidy" this back to `Home-Lab`; it would silently break the push.

The item is kept current by a `PushSecret`
(`kubernetes/apps/base/security/authentik/app/pushsecret.yaml`) reading the
in-cluster Secret `authentik-terraform-credentials`. It pushes through a
dedicated single-vault `SecretStore` (`secretstore-automation.yaml`) rather than
the shared `onepassword` ClusterSecretStore, because that store lists three
vaults with priorities and a write resolves through that ordering rather than to
a vault you named - a push there could land in `Homelab`.

This is deliberately **not** a self-healing reconciler like
`monitoring/grafana-sa-provisioner`. That CronJob exists because Grafana runs on
`emptyDir` SQLite and loses its service account on every pod restart. Authentik
persists tokens in Postgres, so there is no drift here for a timer to heal.

[`secrets.vals.yaml`](../../terraform/authentik/secrets.vals.yaml) maps those
fields to the environment and holds only `ref+op://` references.

## 5. The read-only API token, and how it was minted

The credential this stack plans with is a **read-only service account**, minted
2026-08-26 under an explicit captain authorization covering a read-only token
only. Reproduce or rotate it exactly as below.

Authentik has no CLI subcommand for this, and writing the rows by hand in
Postgres would bypass the ORM's defaults and key generation. The supported path
is the `ak` Django management shell, which is what the UI itself goes through.
It is idempotent: re-running it returns the existing account and token.

```bash
kubectl -n security exec deploy/authentik-worker -c worker -- ak shell -c "
from authentik.core.models import User, UserTypes, Token, TokenIntents, Group
g = Group.objects.get(name='authentik Read-only')
u, cu = User.objects.get_or_create(
    username='tofu-readonly',
    defaults={'name': 'OpenTofu read-only', 'type': UserTypes.SERVICE_ACCOUNT,
              'path': 'goauthentik.io/service-accounts'},
)
assert u.type == UserTypes.SERVICE_ACCOUNT
u.ak_groups.set([g])
u.save()
t, ct = Token.objects.get_or_create(
    identifier='tofu-readonly-api',
    defaults={'user': u, 'intent': TokenIntents.INTENT_API, 'expiring': False,
              'description': 'OpenTofu read-only plan credential'},
)
u.refresh_from_db()
assert u.is_superuser is False
assert [x.name for x in u.ak_groups.all()] == ['authentik Read-only']
print(t.key)
"
```

Capture the key straight into the source Secret without printing it, then let the
`PushSecret` carry it to 1Password:

```bash
kubectl -n security create secret generic authentik-terraform-credentials \
  --from-literal=AUTHENTIK_TOKEN="$KEY" \
  --from-literal=CODER_CLIENT_ID="$CID_CODER" \
  --from-literal=OPEN_WEBUI_CLIENT_ID="$CID_OWUI" \
  --from-literal=PGADMIN_CLIENT_ID="$CID_PGA" \
  --dry-run=client -o yaml | kubectl apply -f -
```

That Secret is the PushSecret's source and is deliberately **not** committed: it
holds the live token. It is the one hand-made object in this whole path.

### Why `authentik Read-only` is the right group

Measured on the live instance, not assumed: the role attached to that group
carries **104 model permissions, none of which is anything but `view_*`**, and
zero object-level permissions. `is_superuser` is false.

Confirmed end to end against the API, where DRF checks permissions before it
validates a body, so an empty POST cannot create anything either way:

| Request | Result |
| ------- | ------ |
| `GET /api/v3/core/applications/` | 200 |
| `GET /api/v3/flows/instances/` | 200 |
| `POST /api/v3/core/groups/` | **403** |
| `PATCH /api/v3/providers/oauth2/4/` | **403** |

This token can run `tofu plan` and can never run `tofu apply`. An apply needs a
separate, write-capable credential, minted only when an apply is approved.

## 6. Planning

```bash
cd terraform/authentik
vals exec -f secrets.vals.yaml -- tofu init
vals exec -f secrets.vals.yaml -- tofu plan
```

If you want plan evidence before the state bucket exists, move `backend.tofu`
aside so OpenTofu falls back to a local state file, plan, then restore it and
delete the local state. Plans are read-only against Authentik either way.

### The adoption plan, as measured 2026-08-26

```
Plan: 9 to import, 0 to add, 4 to change, 0 to destroy.
```

**0 to add and 0 to destroy is the number that matters.** Nothing is created and
nothing is torn down; all nine live objects are adopted.

The four in-place changes are all the same thing, and all benign: `property_mappings`
list *ordering*, with **zero net membership change**. Verified per resource by
diffing the sets, not by eyeballing the diff:

| Resource | Change | Net membership |
| -------- | ------ | -------------- |
| `provider_oauth2["coder"]` | one id reordered | none |
| `provider_oauth2["open-webui"]` | one id reordered | none |
| `provider_oauth2["pg-admin"]` | one id reordered | none |
| `provider_proxy.forward_auth` | all 5 shown as additions | none, set identical to live API |

The proxy case looks alarming and is not. Its read has an explicit guard,
`if len(localMappings) > 0`, which skips populating `property_mappings` when
there is nothing in state yet - which is exactly the situation during an import.
So prior state reads as empty and the plan shows all five as additions. The live
API returns the same five ids; the sets were compared directly and are equal.

These ordering diffs disappear after the first apply, once state carries a local
ordering for `ListConsistentMerge` to preserve.

### Three defects this plan found that `tofu validate` could not

Recorded because each is easy to reintroduce:

1. **`authentik_certificate_key_pair` defaults `fetch_certificate` and `fetch_key`
   to true.** That makes the data source additionally call `view_certificate/`
   and `view_private_key/` and store the PEM *and the private key* in state. Only
   the certificate's id is ever used. Both are now explicitly false - which also
   matters because `view_certificate/` is denied to the read-only role, so
   leaving them on breaks the plan outright.
2. **`redirect_uri_type` must be declared.** These three provider rows predate the
   field so the database holds no key for it, but the API defaults it to
   `authorization` and returns it. Omitting it made every plan propose removing it.
3. **`client_id` must not be marked `sensitive`.** It is a public OAuth2
   identifier. Terraform renders a sensitivity-marked attribute as
   `~ (sensitive value)` in an import plan even when the value is identical,
   which put a permanent phantom "change" on the one attribute whose real change
   would break every login for that app.

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
