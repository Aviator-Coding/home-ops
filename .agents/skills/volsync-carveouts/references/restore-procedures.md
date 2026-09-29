# VolSync restore procedures

## Why `latestImage` lies

`${APP}-dst` is created with `trigger: {manual: restore-once}` and
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`. It runs once, at first admit,
writes a temp PVC, and publishes `status.latestImage`. Flux never runs it
again.

`pvc.yaml` sets `dataSourceRef` to that `ReplicationDestination`. The populator
clones `latestImage`, not the restic repository. An app whose repository was
empty at onboarding logged `No eligible snapshots found` / `No data will be
restored`. `latestImage` is then a snapshot of an empty volume for the life of
the object. Deleting the PVC and letting Flux recreate it restores nothing.
PVC `Bound`, app `Running` and Flux `Ready` all succeed.

Measured on `ai/opencode`: `latestImage` was still the empty image days after
the repository held 4,749 files. Those files were the app's own first-boot
writes. The same emptiness was re-measured on the three surviving destinations
(`paperless-ngx`, `paperless-ngx-media`, `syncthing-data`). `syncthing-data-dst`
was later deleted and Flux-recreated; `restore-once` then succeeded from
snapshot `9dd34c26`. `paperless-ngx` and `paperless-ngx-media` were still empty
at that measurement, and only `paperless-ngx` still points `dataSourceRef` at
its destination. Read `status.latestMoverStatus.logs` and
`status.lastSyncTime` before trusting either.

To re-fire against a populated repository: delete the `ReplicationDestination`
**and** the PVC together, so Flux recreates both and `restore-once` runs as a
new object. Do not patch `spec.trigger.manual`. `IfNotPresent` would keep that
patch forever.

Two neighbours of the same operation:

- VolSync re-stages a clone within seconds of a mover Job being deleted. Delete
  the `ReplicationSource` objects for the duration (they do not touch the
  restic repository) instead of fighting the restage.
- A kopiur `Snapshot` left `Running` blocks later scheduled backups
  (`concurrencyPolicy: Forbid`). Skill `kopiur-backups`.

Human steps, including the busybox `find` inventory that must not use
`-exec {} +`: [corrupt-claim-recreation.md](corrupt-claim-recreation.md).

## Scratch drill versus live restore

Never patch `<app>-dst`. A hand-set trigger or repository field persists, and
a live destination has drifted `spec.restic.repository` onto the wrong Secret
this way.

- **Prove a snapshot without touching the app:** new `ReplicationDestination`
  and scratch PVC, using the app's existing read-only credential Secret.
  Procedure: [volsync-scratch-drill.md](volsync-scratch-drill.md). A small app
  restores and mount-verifies in about a minute. Ceph and MinIO were both
  proved end to end.
- **Put data back into the live claim:** `just kube restore <namespace> <app>`
  (`kubernetes/mod.just`). It builds `<app>-manual` from `<app>-dst` with
  server-side apply, scales the app down, waits for the mover Job, scales up.
  It does not patch `<app>-dst`.

## Modes

The destination PVC is staged writable. kubelet's recursive `fsGroup` walk
runs before restic writes. Restored modes gain one group-write bit:
`644→664`, `755→775`, `2755→2775`, `600→660`, `444→664`. Measured across
4,749 files on `ai/opencode`, including `.git-credentials`. Ownership is
unchanged. The relaxation stays on the volume after the app mounts it.

kopiur stages read-only and reproduces the original modes. The same asymmetry
hides a mover-identity mismatch from VolSync and fails kopiur closed.

## Identity mismatch is not an incomplete backup

`changedetection-config` ran as root and wrote 2292 mode-`0600` root-owned
files against a `1000:1000` mover. The ceph restic snapshot restored
byte-identical (3058 files, per-file sha256, `changedetection.json` included).
`fsGroup` had added group-read on the writable clone before restic opened it.
Restored copies came back `660`/`664` where live was `600`/`644`. That
inflation is the fingerprint. Do not report a VolSync integrity defect without
a restore or a read-only mount as the mover uid.
