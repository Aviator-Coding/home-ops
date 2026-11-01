# Reading a retired VolSync repository

The VolSync operator was removed on 2026-11-01 (issue #2202 PR-6). Its restic repositories were
not: 51 of them stay in three `volsync` buckets (ceph RGW, MinIO on the NAS, R2) until the expiry
in [retired-repo-expiry.md](retired-repo-expiry.md), which is **NOT IN FORCE**. Nothing in the
cluster reads them any more. This is how the owner reads one without the operator.

## Rules

- **Read only.** Use `restic --no-lock` with `snapshots`, `ls`, `dump` and `restore`. Never
  `forget`, `prune`, `unlock`, `backup` or `key`. A retired repository is a frozen record.
- **Restore into a scratch directory or a scratch PVC**, never a live claim. The hard constraint
  of [kopiur-scratch-drill.md](kopiur-scratch-drill.md) applies.
- **Credentials live only in 1Password item `volsync-template`** (vault `Homelab`). Never delete
  or rotate it: it holds the only restic passwords for all 51 repositories. Never print a value;
  read each into the environment of a fresh shell and unset it afterwards. The shell's
  `OP_SERVICE_ACCOUNT_TOKEN` sees only `Home-Lab`, so unset it for these reads.
- **A repository is the exact path segment `<name>/`.** `syncthing` and `syncthing-data` are two
  repositories; so are `paperless-ngx` and `paperless-ngx-media`. Names are the entries of
  `scripts/volsync-retired-expiry/ledger.yaml`.

## Fields

Check the labels first (labels only, no values):
`env -u OP_SERVICE_ACCOUNT_TOKEN op item get volsync-template --vault Homelab --format json | jq -r '.fields[].label'`.

| Destination | `RESTIC_REPOSITORY` | Password | Access keys |
|---|---|---|---|
| ceph | `s3:https://s3.<SECRET_DOMAIN>/<CEPH_VOLSYNC_RESTIC_REPOSITORY>/<name>` from a workstation; `CEPH_HOME_OPS_ENDPOINT_URL` is the in-cluster RGW URL | `CEPH_VOLSYNC_RESTIC_PASSWORD` | `CEPH_HOME_OPS_ACCESS_KEY`, `CEPH_HOME_OPS_SECRET_KEY` |
| minio | `s3:https://nas.<SECRET_DOMAIN>:9000/<MINIO_VOLSYNC_RESTIC_REPOSITORY>/<name>` | `MINIO_VOLSYNC_RESTIC_PASSWORD` | `MINIO_HOME_OPS_ACCESS_KEY`, `MINIO_HOME_OPS_SECRET_KEY` |
| r2 | `s3:<R2_HOME_OPS_ENDPOINT_URL>/<R2_VOLSYNC_RESTIC_REPOSITORY>/<name>` | `R2_VOLSYNC_RESTIC_PASSWORD` | `R2_HOME_OPS_ACCESS_KEY`, `R2_HOME_OPS_SECRET_KEY` |

These are the templates of the deleted `components/volsync/{ceph,minio,r2}/externalsecret.yaml`
(read them with `git log --diff-filter=D -- kubernetes/components/volsync`). The ceph keys are
the OBC `system/volsync` credentials; PushSecret `volsync-ceph-bucket-credentials` keeps the item
in step with them.

## Workstation read

restic is not pinned in `.mise.toml`; use any current release. Example for r2:

```sh
op_field() { env -u OP_SERVICE_ACCOUNT_TOKEN op read "op://Homelab/volsync-template/$1"; }
export AWS_ACCESS_KEY_ID="$(op_field R2_HOME_OPS_ACCESS_KEY)"
export AWS_SECRET_ACCESS_KEY="$(op_field R2_HOME_OPS_SECRET_KEY)"
export RESTIC_PASSWORD="$(op_field R2_VOLSYNC_RESTIC_PASSWORD)"
export RESTIC_REPOSITORY="s3:$(op_field R2_HOME_OPS_ENDPOINT_URL)/$(op_field R2_VOLSYNC_RESTIC_REPOSITORY)/<name>"

restic --no-lock snapshots
restic --no-lock ls latest | head
mkdir -p "$TMPDIR/restic-<name>" && restic --no-lock restore latest --target "$TMPDIR/restic-<name>"

unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY RESTIC_PASSWORD RESTIC_REPOSITORY
```

## Large repositories (in the cluster, owner go-ahead)

A repository too large for a workstation is restored into a scratch PVC. These are cluster
writes. Label every object `fm.homeops/restore-drill` and delete them all afterwards.

1. A scratch `ExternalSecret` in the drill namespace, from ClusterSecretStore `onepassword`,
   `dataFrom.extract` of `volsync-template`, with the deleted component's template for that
   destination (inside the cluster the ceph endpoint field works). Never a hand-made Secret.
2. A scratch PVC on `ceph-block`, at least the repository's restored size.
3. A throwaway pod with the `restic/restic` image, `envFrom` that Secret, the PVC at
   `/restore`, running `restic --no-lock restore latest --target /restore`.
4. Mount the PVC `readOnly` in a second pod to compare; then delete the pods, the PVC and the
   ExternalSecret (its Secret goes with it).

## Reading what comes back

- **Modes may be one group bit wider than the source.** VolSync staged its clones writable, so
  kubelet's `fsGroup` walk added group bits before restic read or wrote them (`644` to `664`,
  `600` to `660`, `.git-credentials` included). Compare content by sha256, not modes. Before
  putting restored credential files anywhere, re-tighten them.
- **A uid mismatch does not mean the backup is incomplete.** The same writable staging let
  `fsGroup` add group-read before restic opened a file, so a `1000:1000` mover read root-owned
  `0600` files (`changedetection-config`, 3058 files byte-identical). Prove completeness with a
  restore, not with the source's ownership.
- restic restores ownership only when it runs as root; a workstation restore is owned by you.
