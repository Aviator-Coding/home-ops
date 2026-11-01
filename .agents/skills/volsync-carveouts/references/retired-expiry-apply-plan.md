# Apply plan: retired VolSync repository expiry

> **Nothing in this plan has been executed.** Merging Git applies nothing. Run it only on the
> owner's explicit go-ahead, once per destination, one destination at a time.

Policy and reasoning: [retired-repo-expiry.md](retired-repo-expiry.md). Rule set:
`scripts/volsync-retired-expiry/ledger.yaml` via `render_lifecycle.py`; applier:
`apply_lifecycle.py`.

## Why this is a runbook and not a manifest

- **Rook's OBC controller has no update path.** It watches only ConfigMaps and CephCluster;
  lifecycle is applied in the provisioner's `Provision()`/`Grant()` paths. A `bucketLifecycle`
  added to the bound `volsync` OBC is accepted into Git, renders clean under `flate`, passes
  review and never reaches the bucket. `ROOK_OBC_ALLOW_ADDITIONAL_CONFIG_FIELDS`
  (`maxObjects,maxSize`) would reject it anyway, and widening that would not fix the missing
  update path.
- **R2 and MinIO have no GitOps representation** (a Cloudflare resource and the NAS).

Because one system silently ignores a write that looks applied, the read-back is built into the
tool: `apply_lifecycle.py` refuses to report success on a write it has not read back and compared.

## Per-destination prerequisites (they are not the same)

| destination | endpoint | credential | lifecycle-manage permission |
|---|---|---|---|
| ceph | `https://s3.${SECRET_DOMAIN}` (pass `--endpoint` with the resolved host; the in-cluster RGW URL is unreachable from a workstation) | default `--credentials secret`: the ObjectBucketClaim's Secret `system/volsync` (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`). The OBC is `prune: disabled`, so it outlives the operator. The agent role cannot read Secrets, so the owner runs this | Yes: the key is the `volsync` OBC's generated key, i.e. the bucket owner (the OBC ConfigMap `system/volsync` holds `BUCKET_NAME=volsync` and the in-cluster host) |
| minio | `https://nas.${SECRET_DOMAIN}:9000` (pass `--endpoint`; also `buckets.minio.endpoint` in `ledger.yaml`) | `--credentials env` only (no in-cluster Secret is left). 1Password item `volsync-template`, fields `MINIO_HOME_OPS_ACCESS_KEY` and `MINIO_HOME_OPS_SECRET_KEY`, exported as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` | Yes |
| r2 | defaults to `buckets.r2.endpoint` in `ledger.yaml` (the item field `R2_HOME_OPS_ENDPOINT_URL` holds the same URL) | `--credentials env` only. A dry run may use `volsync-template` fields `R2_HOME_OPS_ACCESS_KEY` and `R2_HOME_OPS_SECRET_KEY`, exported as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` | **No**: `AccessDenied`. That token is object read/write only; lifecycle needs the **`Workers R2 Storage Write`** permission group |

The per-claim `<claim>-volsync-<dest>-secret` Secrets are not a credential source any more: they
are pruned as each claim leaves VolSync (issue #2202 decision 5). `--credentials secret` for
minio or r2 exits 2 with a message.

Fill the environment from 1Password without printing it (item `volsync-template` is in the
Connect-visible `Homelab` vault; the shell's `OP_SERVICE_ACCOUNT_TOKEN` sees only `Home-Lab`, so
unset it for these reads. Check the field names first with
`op item get volsync-template --vault Homelab --format json | jq -r '.fields[].label'`):

```sh
export AWS_ACCESS_KEY_ID="$(env -u OP_SERVICE_ACCOUNT_TOKEN op read 'op://Homelab/volsync-template/MINIO_HOME_OPS_ACCESS_KEY')"
export AWS_SECRET_ACCESS_KEY="$(env -u OP_SERVICE_ACCOUNT_TOKEN op read 'op://Homelab/volsync-template/MINIO_HOME_OPS_SECRET_KEY')"
# r2 dry run: R2_HOME_OPS_ACCESS_KEY / R2_HOME_OPS_SECRET_KEY the same way, in a fresh shell
```

**R2 needs a credential that does not exist in the cluster for the apply.** The owner mints a
scoped R2 API token with `Workers R2 Storage Write`, it is exported as `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` in the shell for the r2 step only, and revoked afterwards. Never store it
in 1Password or the cluster. R2's limit is 1000 lifecycle rules per bucket; this plan installs 51.

## What each command changes

One `PutBucketLifecycleConfiguration` per bucket, replacing the whole lifecycle configuration
with the same 51 rules `{Filter: {Prefix: "<name>/"}, Expiration: {Date: "<tier date>T00:00:00Z"},
Status: Enabled}`. No object is deleted at apply time. All three buckets had no lifecycle
configuration when the plan was written (check again with a dry run: the tool captures the
prior state to a file first, so undo is exact).

## Step 0 - render and eyeball (read-only, no S3 credentials)

```sh
cd <repo>
eval "$(mise env -s bash)"
python3 scripts/volsync-retired-expiry/render_lifecycle.py > /tmp/volsync-lifecycle.json
jq '.Rules | length' /tmp/volsync-lifecycle.json                      # 51
jq -r '.Rules[] | "\(.Filter.Prefix)\t\(.Expiration.Date)"' /tmp/volsync-lifecycle.json | sort
jq -r '.Rules[].Filter.Prefix' /tmp/volsync-lifecycle.json \
  | grep -vE '/$' && echo "STOP: a prefix does not end in /"
kubectl get replicationsource -A   # cluster read; expect "No resources found"
```

The grep must print nothing. `kubectl get replicationsource -A` must list nothing: no
repository is live since 2026-11-01, when `paperless-ngx`, the last, was retired. A source that
exists again must have its app in `ledger.yaml` `protected` first (the renderer then refuses any
rule for it); otherwise stop.

## Step 1 - dry run each destination (read-only)

Dry run is the default; `--confirm` is the only way anything is written. It lists the live
bucket and re-proves the match against current data.

```sh
KC=<path to kubeconfig>   # ceph only (reads Secret system/volsync); pass explicitly: mise sets KUBECONFIG and would override yours

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination ceph  --kubeconfig "$KC" --endpoint https://s3.<SECRET_DOMAIN>

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination minio --credentials env --endpoint https://nas.<SECRET_DOMAIN>:9000

# r2 needs the elevated token; without it this stops at the lifecycle read, by design
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...     # scoped R2 token, this shell only
uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination r2 --credentials env
```

**Abort the whole plan if any dry run reports a non-zero `live objects matched by these rules`.**
`objects listed` drifts upward between runs (the live repositories keep backing up); `live
matched` is the number that must stay 0.

## Step 2 - apply, one destination at a time

`--save-previous` is mandatory with `--confirm` (the tool exits 2 without it). Ceph first, confirm
it verified, then minio, then r2. Never in parallel.

```sh
mkdir -p /tmp/volsync-lc-undo

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination ceph --kubeconfig "$KC" --endpoint https://s3.<SECRET_DOMAIN> \
  --confirm --save-previous /tmp/volsync-lc-undo/ceph.json

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination minio --credentials env --endpoint https://nas.<SECRET_DOMAIN>:9000 \
  --confirm --save-previous /tmp/volsync-lc-undo/minio.json

uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination r2 --credentials env \
  --confirm --save-previous /tmp/volsync-lc-undo/r2.json
```

Success ends with `VERIFIED: 51 rules stored, all prefixes exact-segment, no rule in range of a
live repository.` and `Earliest deletion: 2027-03-31T00:00:00Z`. Anything else (mismatch,
missing rule, a prefix the store rewrote) exits non-zero and names the problem. Keep
`/tmp/volsync-lc-undo/` until all three have verified.

## Step 3 - independent verification

The tool reads back through the client that wrote. Use a second instrument on ceph:

```sh
kubectl --kubeconfig="$KC" -n rook-ceph exec deploy/rook-ceph-tools -- radosgw-admin lc list
# expect the volsync bucket with an UNINITIAL/PROCESSING status
kubectl --kubeconfig="$KC" -n rook-ceph exec deploy/rook-ceph-tools -- \
  radosgw-admin lc get --bucket=volsync
kubectl --kubeconfig="$KC" -n rook-ceph exec deploy/rook-ceph-tools -- \
  radosgw-admin bucket stats --bucket=volsync | jq '.usage."rgw.main"'
```

The invariant is not "51 rules exist" but **every stored rule's prefix is an exact segment
that reaches no other repository and no live one** (none is live since 2026-11-01; `paperless-ngx/`
and `paperless-ngx-media/`, `syncthing/` and `syncthing-data/` are distinct rules). `num_objects`
must be unchanged; nothing expires until 2027-03-31.

## Step 4 - undo

Per destination, from the file saved in step 2 (dry-run by default; verifies the restored rule
count afterwards):

```sh
uv run --with boto3 python scripts/volsync-retired-expiry/apply_lifecycle.py \
  --destination ceph --kubeconfig "$KC" --endpoint https://s3.<SECRET_DOMAIN> \
  --undo /tmp/volsync-lc-undo/ceph.json --confirm
```

Undo is a pure configuration change, safe any time before the expiry date. **After an expiry
date passes, undo recovers nothing**: deletion is done by the object store and the buckets are
not versioned. The decision point is before 2027-03-31.

## Residual risk

- **The write path is unexecuted.** Credential authority and the read path were measured; the
  `PutBucketLifecycleConfiguration` call has not been made against any store. Interop (a store
  rewriting or rejecting a `Filter`-style rule, or a checksum-header disagreement) is the
  plausible failure, and the mandatory read-back catches it. A failed first run changes nothing.
- **Lifecycle processing is asynchronous.** A repository is briefly partially deleted, hence
  unusable, before it is fully gone. The date is the end of the window, not a grace period.
- **Re-apply after any ledger change.** Adding a future retirement to the ledger does nothing
  until step 2 is run again for each destination.
