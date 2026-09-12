# Apply plan: retired VolSync repository expiry

> **Nothing in this plan has been executed.** Merging the PR that added it applies nothing.
> Every command below is written to be run by firstmate on the captain's explicit go-ahead,
> once per destination. The crewmate that produced this plan ran only the read-only steps.

Policy and reasoning: [`volsync-retired-repository-expiry.md`](volsync-retired-repository-expiry.md).
Rule set: [`scripts/volsync-retired-expiry/ledger.yaml`](../../scripts/volsync-retired-expiry/ledger.yaml)
via [`render_lifecycle.py`](../../scripts/volsync-retired-expiry/render_lifecycle.py).

## Why this is a runbook and not a manifest

There is no declarative path, and that is a measured finding rather than a preference:

- **Rook's OBC controller has no update path.** It watches only ConfigMaps and CephCluster, and
  lifecycle is applied in the bucket provisioner's `Provision()`/`Grant()` paths. The `volsync`
  bucket was provisioned 341 days ago, so a `bucketLifecycle` added to the bound OBC is accepted
  into Git, renders clean under `flate`, passes review - **and never reaches the bucket.** The
  cluster's `ROOK_OBC_ALLOW_ADDITIONAL_CONFIG_FIELDS` (`maxObjects,maxSize`) would reject it
  anyway, but widening that would not fix the missing update path.
- **R2 and MinIO have no GitOps representation here at all** - one is a Cloudflare resource, the
  other lives on the NAS.

Because one of these systems silently ignores a lifecycle write that *looks* applied, **a
read-back check is built into the tool rather than being a step in this document.**
`apply_lifecycle.py` refuses to report success on a write it has not read back and compared, so
the verification cannot be skipped by forgetting it.

## Per-destination prerequisites - they are NOT the same

Measured read-only on 2026-09-12 with `GetBucketLifecycleConfiguration`:

| destination | endpoint | credential | lifecycle-manage permission |
|---|---|---|---|
| ceph | `https://s3.${SECRET_DOMAIN}` (the in-cluster RGW URL in the Secret is unreachable from a workstation) | Secret `selfhosted/syncthing-data-volsync-ceph-secret` | **Yes** - this key is byte-identical to the `volsync` OBC's generated key, i.e. it is the bucket owner |
| minio | `https://nas.${SECRET_DOMAIN}:9000` (from the Secret) | Secret `selfhosted/syncthing-data-volsync-minio-secret` | **Yes** - read succeeded |
| r2 | `https://<account>.r2.cloudflarestorage.com` (from the Secret) | **NOT the in-cluster token** | **No** - `AccessDenied`. The in-cluster token has object read/write only; lifecycle needs the **`Workers R2 Storage Write`** permission group |

**R2 therefore needs a credential that does not exist in the cluster.** Mint a scoped R2 API
token in the Cloudflare dashboard with `Workers R2 Storage Write`, use it for this one call, and
revoke it afterwards. It must not be stored in 1Password or the cluster - nothing else needs it,
and the in-cluster token deliberately stays object-scoped. Creating that token is a captain
action; the plan below assumes it is exported into the shell for the r2 step only.

R2's documented limit is 1000 lifecycle rules per bucket; this plan installs 48.

## What each command changes

One `PutBucketLifecycleConfiguration` call per bucket, replacing that bucket's whole lifecycle
configuration with the same 48 rules. **All three buckets currently have no lifecycle
configuration at all** (`radosgw-admin lc list` returned `[]`; both S3 reads returned
`NoSuchLifecycleConfiguration`), so the undo is a clean delete - and the tool still captures the
prior state to a file first, so the undo is exact rather than assumed.

Each rule is `{Filter: {Prefix: "<name>/"}, Expiration: {Date: "<tier date>T00:00:00Z"}, Status:
Enabled}`. The object store then deletes those objects on that date and not before. No object is
deleted at apply time.

## Step 0 - render and eyeball (read-only, no credentials, no network)

```sh
cd <repo>
eval "$(mise env -s bash)"
python3 scripts/volsync-retired-expiry/render_lifecycle.py > /tmp/volsync-lifecycle.json
jq '.Rules | length' /tmp/volsync-lifecycle.json                      # 48
jq -r '.Rules[] | "\(.Filter.Prefix)\t\(.Expiration.Date)"' /tmp/volsync-lifecycle.json | sort
# every prefix must end in "/" and none may be paperless-ngx/, paperless-ngx-media/
# or syncthing-data/:
jq -r '.Rules[].Filter.Prefix' /tmp/volsync-lifecycle.json \
  | grep -vE '/$' && echo "STOP: a prefix does not end in /"
jq -r '.Rules[].Filter.Prefix' /tmp/volsync-lifecycle.json \
  | grep -xE 'paperless-ngx/|paperless-ngx-media/|syncthing-data/' \
  && echo "STOP: a LIVE repository is in the rule set"
```

Both greps are expected to print nothing and to be silent on success.

## Step 1 - dry run each destination (read-only; this is what was already done)

Dry run is the default; `--confirm` is the only way anything is written. It lists the live
bucket and re-proves the match against current data, because the ledger is a dated measurement.

```sh
KC=<path to kubeconfig>   # pass explicitly: mise sets KUBECONFIG and would override yours

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination ceph  --kubeconfig "$KC" --endpoint https://s3.<SECRET_DOMAIN>

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination minio --kubeconfig "$KC"

# r2 needs the elevated token; without it this stops at the lifecycle read, by design
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...     # scoped R2 token, this shell only
uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination r2 --kubeconfig "$KC" --credentials env
```

**Abort the whole plan if any dry run reports a non-zero `live objects matched by these rules`.**
Results on 2026-09-12 - all three showed zero:

| destination | objects listed | live objects | live matched | retired matched | unlisted prefixes |
|---|---|---|---|---|---|
| ceph | 6,127 | 449 | **0** | 5,678 | 0 |
| r2 | 5,748 | 358 | **0** | 5,390 | 0 |
| minio | 11,278 | 303 | **0** | 10,975 | 0 |

(`objects listed` drifts upward between runs - the three live repositories are still being
backed up three times a day. That is expected; `live matched` is the number that must stay 0.)

## Step 2 - apply, one destination at a time

`--save-previous` is mandatory with `--confirm`; the tool refuses without it (exit 2).
Do ceph first, confirm it verified, then minio, then r2. Do not run them in parallel.

```sh
mkdir -p /tmp/volsync-lc-undo

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination ceph --kubeconfig "$KC" --endpoint https://s3.<SECRET_DOMAIN> \
  --confirm --save-previous /tmp/volsync-lc-undo/ceph.json

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination minio --kubeconfig "$KC" \
  --confirm --save-previous /tmp/volsync-lc-undo/minio.json

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination r2 --kubeconfig "$KC" --credentials env \
  --confirm --save-previous /tmp/volsync-lc-undo/r2.json
```

A successful run ends with:

```
VERIFIED: 48 rules stored, all prefixes exact-segment, no rule in range of a live repository.
Earliest deletion: 2027-03-31T00:00:00Z
```

Anything else - a mismatch, a missing rule, a prefix the store rewrote - exits non-zero and
names the problem. **Keep `/tmp/volsync-lc-undo/` until all three have verified.**

## Step 3 - independent verification (do not rely only on the tool's own read-back)

The tool reads back through the same client that wrote. A second, different instrument:

```sh
# ceph - the RGW's own view, not an S3 API call
kubectl --kubeconfig="$KC" -n rook-ceph exec deploy/rook-ceph-tools -- \
  radosgw-admin lc list
# expect the volsync bucket to appear with an UNINITIAL/PROCESSING status

# ceph - and the stored rules themselves
kubectl --kubeconfig="$KC" -n rook-ceph exec deploy/rook-ceph-tools -- \
  radosgw-admin lc get --bucket=volsync
```

The invariant to check is not "48 rules exist" but **"no stored rule's prefix is in range of
`paperless-ngx/`, `paperless-ngx-media/` or `syncthing-data/`"** - the tool asserts that against
what the store *kept*, and `radosgw-admin lc get` lets you confirm it by eye on ceph.

Then confirm nothing was deleted:

```sh
kubectl --kubeconfig="$KC" -n rook-ceph exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket stats --bucket=volsync | jq '.usage."rgw.main"'
# num_objects should be ~6,127 (unchanged apart from ongoing live backups),
# NOT ~449. Nothing expires until 2027-03-31.
```

## Step 4 - undo

Per destination, from the file saved in step 2. Because all three buckets had no prior
lifecycle, this deletes the configuration and returns them to today's state exactly.

```sh
uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination ceph --kubeconfig "$KC" --endpoint https://s3.<SECRET_DOMAIN> \
  --undo /tmp/volsync-lc-undo/ceph.json --confirm
```

`--undo` is dry-run by default too, and verifies the restored rule count afterwards. Undo is
safe at any time before the expiry date and is a pure configuration change - it deletes no
objects and un-deletes none either.

**After an expiry date passes, undo no longer recovers anything.** Deletion is done by the object
store and is not reversible; the buckets are not versioned (`"versioning": "off"` on ceph). So
the decision point is *before* 2027-03-31, not after.

## Residual risk, stated plainly

- **The write path is unexecuted.** Credential authority and the read path are measured; the
  `PutBucketLifecycleConfiguration` call itself has not been made against any of the three
  stores. Interop is the plausible failure (an S3-compatible store rewriting or rejecting a
  `Filter`-style rule, or a checksum-header disagreement), and that is precisely what the
  mandatory read-back catches. Expect the possibility of a first-run failure on one store; it is
  a configuration call, so a failure changes nothing.
- **Lifecycle processing is asynchronous.** On the expiry date each store works through the
  prefix over hours, so a repository is briefly partially deleted and therefore unusable before
  it is fully gone. That window starts on the expiry date, which is past the recovery window by
  definition - but the date is the *end* of the window, not the start of a grace period.
- **Re-apply is needed after any ledger change.** Adding a future retirement to the ledger does
  nothing until step 2 is run again for each destination. That is the deliberate trade: the
  expiry itself is automatic and permanent, and deciding what expires never is.
