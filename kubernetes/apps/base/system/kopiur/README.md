# kopiur (operator and repositories)

This directory is the operator, the `ClusterRepository` objects, and the
credential ExternalSecrets. Per-claim policies and schedules live in
`kubernetes/components/kopiur/`.

Read skill `kopiur-backups` before changing it. Operator invariants are
`references/stage0-operator.md`. Credentials are `references/credentials.md`.

## Invariants

- `r2` is `create.enabled: false`. It must attach to the existing bucket.
  `ceph` may create its bucket.
- `ClusterRepository.spec.parameters` is write-only from Git. Removing the
  block does not roll the live repository back.
- Do not commit `takeoverPolicy: Force`. `IndexBlobHealth=False` is epoch
  tuning. `ceph` `minDuration` is `4h`. `r2` is untuned.
- 1Password item `kopiur-ceph-bucket` is a record nothing reads. The OBC
  Secret is what the controller uses.

Full-cluster restore: `.agents/skills/kopiur-backups/references/full-cluster-restore-r2.md`.
