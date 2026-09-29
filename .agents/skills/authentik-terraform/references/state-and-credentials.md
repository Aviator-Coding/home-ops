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

## Bootstrap (already done)

One-time: `radosgw-admin user create --uid=terraform`, bucket
`terraform-state` with versioning, confirm the ACL has no public grant, store
the keys on the Automation item. Do not repeat it to "fix" a plan.
