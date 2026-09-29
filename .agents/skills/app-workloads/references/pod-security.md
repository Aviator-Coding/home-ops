# Pod security, fsGroup, linuxserver, RWO

## The empty-volume shape

The volume root ends up `*:1000` (the VolSync mover default) and the pod
declares no matching `fsGroup`. The kubelet does no ownership fix-up. The
process falls through to the other bits of a `2775` directory. Probes pass,
Gatus is green, and the backup engine copies the empty tree.

Two shapes that produced it:

- `runAsUser` / `runAsGroup` set, no `fsGroup`.
- The security context nested under a key app-template does not read, so
  Helm dropped it and the pod shipped `securityContext: {}`.

Fix the key the chart actually reads, and set `fsGroup` to the group that
must own the tree. Prefer `readOnly: true` on a mount that is supposed to
be read-only.

## fsGroup on Ceph

`kubectl get csidriver` shows `fsGroupPolicy: File` for the Ceph block and
filesystem drivers. The kubelet applies `fsGroup` to those claims.
`fsGroupChangePolicy: OnRootMismatch` does not protect existing content
when you change the declared group: the root mismatch is what triggers the
recursive chown. That chown stays on the volume after a revert. An app
whose gid now matches the volume keeps working without the `fsGroup` line,
and a later revert will not reproduce the original failure.

## linuxserver

s6-overlay (`docker-baseimage-alpine`) assumes it starts as root so it can
`chown` and `usermod` before dropping privileges. Under
`runAsNonRoot: true` and `readOnlyRootFilesystem: true`, set:

```yaml
LSIO_NON_ROOT_USER: "true"
LSIO_READ_ONLY_FS: "true"
```

That skips the root-only init. It is the supported path, not a workaround.

s6 still writes `/run` and `/tmp`. Mount `emptyDir` there. Those volumes
are root-owned, and s6 preinit refuses a mismatched owner, so a `busybox`
initContainer running as UID 0 must chown them to the app uid before the
main container starts. Copy `downloads/bazarr`.

## RWO

`ceph-block` is ReadWriteOnce. `strategy.type: Recreate` so the old pod
releases the volume before the new one mounts. RollingUpdate on an RWO
claim does not fail the manifest; it stalls the rollout.

## Requests and limits

A memory limit with no request reserves the whole limit, and the API
server has already defaulted the pod by the time you can read it. Audit
the Deployment or HelmRelease. A CPU limit is a CFS quota. `limits: {}`
does not clear a chart default. Skill `node-scheduling`.

## Helm will not remove a hand-added securityContext

See skill `flux-gitops`, [live-testing.md](../../flux-gitops/references/live-testing.md).
