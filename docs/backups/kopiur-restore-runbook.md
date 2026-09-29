# kopiur restore drill

Prove a claim's ceph and r2 snapshots come back byte-identical, without
touching the live volume. Full-cluster recovery is a different procedure:
`docs/backups/full-cluster-restore-from-r2.md`. Rebuilding a corrupt claim
from restic is `docs/backups/corrupt-claim-recreation-runbook.md`.

Skill `kopiur-backups`, reference `references/restore-and-cache.md`.

## Hard constraint

Never scale, overwrite, re-provision, or write the live claim.

- Do not run `just kube restore`. It scales the app to zero and restores into
  the live PVC.
- Do not edit the standing `${APP}-kopiur-dst` `Restore`. It is
  `ssa: IfNotPresent`. A hand edit persists forever.
- Use `Restore.spec.target.pvc`. It creates a new PVC and cannot address an
  existing claim. Do not use `target.pvcRef`.
- Set `policy.onMissingSnapshot: Fail`. `Continue` yields an empty volume and
  then looks successful. The standing populator uses `Fail` for the same
  reason; a drill must not weaken it.
- Set `credentialProjection.enabled: true`. There is no standing repository
  Secret in the workload namespace. Without projection the CR goes green and
  the mover fails at run time.
- Mount the restored volume `readOnly: true` on the volume source, not only
  on the `volumeMount`. A mount-only flag still lets kubelet's `fsGroup` walk
  rewrite modes before you record them.
- Label every drill object `fm.homeops/restore-drill`. Never delete an
  unlabelled object.

Deleting a `Restore` does not delete backup data (no finalizers). Deleting a
`Snapshot` CR does: it owns its kopia snapshot. Do not delete one that has a
`kopiaSnapshotID`.

## Procedure

### 0. Baseline

```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph health detail
kubectl -n <ns> get pvc <claim> -o jsonpath='uid={.metadata.uid}{"\n"}pv={.spec.volumeName}{"\n"}'
kubectl -n <ns> get pod -l app.kubernetes.io/name=<app> \
  -o jsonpath='{range .items[*]}pod={.metadata.name} started={.status.startTime} restarts={.status.containerStatuses[0].restartCount}{"\n"}{end}'
```

Re-run at the end. The PVC uid and the pod `startTime` are the proof the live
claim was not replaced.

### 1. The snapshot must contain files

```bash
kubectl -n <ns> get snapshot.kopiur.home-operations.com <name> \
  -o jsonpath='{.status.phase}{" "}{.status.stats}{"\n"}'
```

`{"filesNew":0,"sizeBytes":0}` is an empty snapshot. A green `Succeeded` phase
does not mean the backup has data. A `Succeeded` snapshot whose
`.status.stats` does not cover the volume is not a proof.

### 2. Mover identity

Measure file ownership on the claim. Do not copy `runAsUser`. Set the drill
mover's `podSecurityContext` to that uid/gid. The component default is
`1000:1000`. A mismatch fails closed because kopiur stages read-only and
kubelet does not fix permissions. Authoritative pairs:
`EXPECTED_IDENTITY` in `scripts/ci/kopiur-stage3-test.py`.

### 3. Names are free

```bash
kubectl get restore.kopiur.home-operations.com,pvc,pod -A -l fm.homeops/restore-drill
```

Must be empty. A drill `Restore` name must not be `<app>-kopiur-dst`.

### 4. Bracket a stable manifest

A live volume changes while you snapshot it. Hash before and after, and
compare only the files that did not change (`comm -12`). Prune `lost+found`
(`root:root` `0700`). Use `-xdev` so `emptyDir` mounts inside the data
directory are not counted.

Busybox `find -exec … {} +` can truncate the argument list and exit 0. Use
`\;`, and cross-check `find <mount> -type f | wc -l` against the manifest
line count. An empty-string sha256
(`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`) means
both sides were empty.

`LC_ALL=C sort` so the two sides order paths the same way.

### 5. Snapshot only if coverage is missing

An on-demand `Snapshot` whose policy is r2 can evict that day's newest
snapshot: r2 policies have no `keepHourly`. Prefer the newest existing
snapshot (`source.fromPolicy.offset: 0`) when one already covers the claim.
Confirm `.status.resolved.kopiaSnapshotID` is the snapshot you meant.
Scheduled ceph runs can move `offset: 0` onto a newer snapshot between the
two destinations.

### 6. Scratch Restore, one per destination

```yaml
apiVersion: kopiur.home-operations.com/v1alpha1
kind: Restore
metadata:
  name: <app>-kopiur-drill-<dest>
  namespace: <ns>
  labels:
    fm.homeops/restore-drill: kopiur-<dest>
spec:
  repository:
    kind: ClusterRepository
    name: <dest>          # ceph | r2
  credentialProjection:
    enabled: true
  source:
    fromPolicy:
      name: <app>-<dest>
      offset: 0
  target:
    pvc:
      name: <app>-kopiur-drill-<dest>
      accessModes: ["ReadWriteOnce"]
      capacity: <same as the live claim>
      storageClassName: ceph-block
  policy:
    onMissingSnapshot: Fail
  mover:
    podSecurityContext:
      runAsUser: <measured>
      runAsGroup: <measured>
      fsGroup: <measured>
    cache:
      mode: Ephemeral
      capacity: <the claim's KOPIUR_CACHE_CAPACITY>
```

Cache smaller than `min(snapshot sizeBytes, ~6.2 GiB)` fails terminally and
does not retry. Use the claim's Git capacity, not `2Gi`, unless that value
is already above the plateau.

A drill does not exercise the standing populator. That object stays
`IfNotPresent` and is ceph-only. A scratch `target.pvc` proof is not a
populator proof.

### 7. Wait

```bash
kubectl -n <ns> wait --for=jsonpath='{.status.phase}'=Completed \
  restore.kopiur.home-operations.com/<drill-name> --timeout=600s
```

Logs are `kubectl logs job/<name> -c mover` (the pod has a k8tz init
container).

### 8. Compare

Mount both restored PVCs read-only in one pod. Diff the stable manifest
against each restore and the two restores against each other. All three
diffs empty, and the file count is not zero.

Also compare modes. kopiur restores reproduce the original modes. A VolSync
restore does not (skill `volsync-carveouts`).

### 9. Live claim untouched

The mover Job's volumes must name only the drill PVCs. Re-run step 0.

### 10. Clean up

Delete the verify pod, then the `Restore` CRs, then any drill PVCs still
present. The `Restore` owns the mover Job; deleting the PVC first sticks it
in `Terminating` behind `pvc-protection`.

```bash
kubectl get restore.kopiur.home-operations.com,pvc,pod,job -A -l fm.homeops/restore-drill
```

Must print nothing.
