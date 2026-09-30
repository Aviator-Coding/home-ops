# State backend and credentials

Bucket `terraform-state` on in-cluster Ceph RGW, user `terraform`, versioning
on, private ACL. Keys live only on 1Password item `Automation/authentik-terraform`
(`TF_STATE_ACCESS_KEY_ID`, `TF_STATE_SECRET_ACCESS_KEY`).

Not an ObjectBucketClaim. StorageClass `ceph-bucket` is
`reclaimPolicy: Delete`, and the owning Flux Kustomization prunes, so an OBC
would delete the state with the claim.

`backend.tofu` has no `endpoints` block. `AWS_ENDPOINT_URL_S3` supplies it:

| Consumer | Endpoint |
|---|---|
| Local plan/apply | `http://127.0.0.1:18081` after `kubectl -n rook-ceph port-forward svc/rook-ceph-rgw-ceph-objectstore 18081:80` |
| `terraform-diff` on the ARC runner | `http://rook-ceph-rgw-ceph-objectstore.rook-ceph.svc.cluster.local` |

Unset, the AWS SDK talks to real AWS and returns `PermanentRedirect`. Never
point OpenTofu at `https://s3.sklab.dev`. aws-sdk-go-v2 signs
`accept-encoding` and `amz-sdk-*`. Envoy rewrites one of them and RGW returns
`SignatureDoesNotMatch`. boto3 and minio-go do not sign those headers, so the
aws CLI can still create the bucket through the gateway and VolSync never
noticed.

A CI `tofu init` 403 `SignatureDoesNotMatch` is usually state-key drift, not
the gateway rewrite (CI never uses the gateway). Compare the Kubernetes
Secret, the live RGW `terraform` user and the 1Password item by hash before
regenerating a key.

## Tokens

| Field | Who uses it | Scope |
|---|---|---|
| `AUTHENTIK_TOKEN` | `secrets.vals.yaml` review plans | `tofu-readonly`, view-only, writes 403 |
| `AUTHENTIK_APPLY_TOKEN` | `secrets-apply.vals.yaml` only, absent until go-ahead | `tofu-writer`, no delete, model-level `change_flow` on every flow |
| `OP_CONNECT_TOKEN` | GitHub Actions secret for `terraform-diff` | 1Password Connect bearer for the whole Automation vault |

`OP_CONNECT_TOKEN` is not an item grant. Connect tokens are per-vault. This
one reads every current and future Automation item, not only
`authentik-terraform`. Mint a dedicated token
(`op connect token create ... --vault Automation`). Do not reuse the cluster
ESO Connect token. A CI compromise is then revocable on its own. Narrowing to
a vault that holds only CI items is an open follow-up.

`OP_SERVICE_ACCOUNT_TOKEN` is the workstation/vals credential for the
`Home-Lab` vault. It is a different token and a different vault from Connect.
Connect cannot see `Home-Lab`. Skill `secrets-1password`.

CI resolves the same `authentik-terraform` fields through
`ref+onepasswordconnect://` (`secrets-ci.vals.yaml`). Local vals uses
`ref+op://` (`secrets.vals.yaml`). `OP_CONNECT_HOST` is the in-cluster Connect
Service, not a secret.

The read-only token is machine-maintained: a PushSecret from the hand-made
Secret `authentik-terraform-credentials` into `Automation/authentik-terraform`.
Authentik stores tokens in Postgres, so this is not a restart-wiped Grafana
style provisioner.

## Mint or rotate the read-only token

Authentik has no CLI subcommand for this and hand-written Postgres rows skip
the ORM's key generation. Use the `ak` management shell on the worker. The
script is idempotent: a re-run returns the existing account and token. Mint
only under an explicit go-ahead for a read-only token.

```bash
kubectl --kubeconfig ./kubeconfig -n security exec deploy/authentik-worker -c worker -- ak shell -c "
from authentik.core.models import User, UserTypes, Token, TokenIntents, Group
g = Group.objects.get(name='authentik Read-only')
u, cu = User.objects.get_or_create(
    username='tofu-readonly',
    defaults={'name': 'OpenTofu read-only', 'type': UserTypes.SERVICE_ACCOUNT,
              'path': 'goauthentik.io/service-accounts'},
)
assert u.type == UserTypes.SERVICE_ACCOUNT
u.groups.set([g])
u.save()
t, ct = Token.objects.get_or_create(
    identifier='tofu-readonly-api',
    defaults={'user': u, 'intent': TokenIntents.INTENT_API, 'expiring': False,
              'description': 'OpenTofu read-only plan credential'},
)
u.refresh_from_db()
assert u.is_superuser is False
assert [x.name for x in u.groups.all()] == ['authentik Read-only']
print(t.key)
" 2>&1 | grep -v '^{'
```

Authentik 2026.8.3 deprecates `User.ak_groups`, so the script uses `groups`
(the old name still works but logs a JSON deprecation line, hence the
`grep -v '^{'`). Rotate by deleting the `Token` row and re-running.

The `authentik Read-only` group's role holds only `view_*` model permissions
(104 measured 2026-08-26), no object permissions, not superuser. Proof: `GET
/api/v3/core/applications/` is 200, `POST /api/v3/core/groups/` and `PATCH
/api/v3/providers/oauth2/4/` are 403. It can plan and never apply.

Put the key into the source Secret without echoing it. Patch, do not
`create --dry-run | apply`: the live Secret also carries the `*_CLIENT_ID` and
`TF_STATE_*` keys, and a full replace drops them.

```bash
kubectl -n security patch secret authentik-terraform-credentials --type merge \
  -p "{\"stringData\":{\"AUTHENTIK_TOKEN\":\"$KEY\"}}"
```

For a first-ever mint the Secret does not exist yet: `kubectl -n security
create secret generic authentik-terraform-credentials
--from-literal=AUTHENTIK_TOKEN="$KEY"` (plus the client-id keys). It is
deliberately never committed. The PushSecret then writes it to
`Automation/authentik-terraform`.

## Discovering import IDs

The import blocks need exact primary keys, which only the database gives.
Read-only `SELECT`s against Authentik's Postgres, never selecting
`client_secret`:

```bash
PRIMARY=$(kubectl --kubeconfig ./kubeconfig -n database get pods \
  -l 'cnpg.io/cluster=postgres-17,role=primary' -o jsonpath='{.items[0].metadata.name}')
kubectl --kubeconfig ./kubeconfig -n database exec "$PRIMARY" -c postgres -- \
  psql -d authentik -A -F'|' -c "select id, name from authentik_core_provider order by id;"
```

## Bootstrap (already done)

One-time: `radosgw-admin user create --uid=terraform`, bucket
`terraform-state` with versioning, confirm the ACL has no public grant, store
the keys on the Automation item. Do not repeat it to "fix" a plan.
