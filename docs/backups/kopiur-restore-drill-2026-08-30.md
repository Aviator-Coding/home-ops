# kopiur restore drill - 2026-08-30

> **Historical snapshot, but the procedure is durable.** Numbers below were measured live
> against the cluster on 2026-08-30 between 19:20Z and 19:32Z, on branch
> `fm/homeops-kopiur-stage2`. Re-measure timings before relying on them; the *procedure*
> itself (manifests, ordering, verification method, cleanup) is the reusable part.
>
> Sibling document: [`restore-drill-2026-08-23.md`](restore-drill-2026-08-23.md) is the same
> exercise against VolSync, and is the house standard this one is built to. Read its
> "Hard constraint this procedure satisfies" section first; it is binding here too.

## Headline result - read this before the rest

**The restore path works from both destinations. Byte-level data fidelity was NOT proven,
and could not be, because the Stage 1 pilot volume contains no data.**

`downloads/autobrr` keeps all of its state in the shared `postgres-17` CNPG cluster
(`AUTOBRR__DATABASE_TYPE=postgres`). Its 5Gi `ceph-block` claim holds **zero files** and has
done so for the 328 days it has existed. Four independent sources agree, and none of them is
this drill's own restore:

| Evidence | Result |
|---|---|
| Live pod, `find /config -xdev -type f \| wc -l` | `0` (only the `log` mount point dir) |
| Live pod, `df -k /config` | `12` KiB used of 5 074 592 KiB |
| kopiur `Snapshot` CRs, `.status.stats`, both destinations | `{"filesNew":0,"sizeBytes":0}` |
| VolSync restic mover logs, all **three** destinations | `processed 0 files, 0 B in 0:00` |

So the two Stage 1 "real first backups" are two faithful backups of an empty volume, and
Stage 1's gate (`Snapshot` reaches `Succeeded`) passed without ever moving a byte of data.

This does not make the drill worthless - see [What this drill proved](#what-this-drill-proved-and-did-not-prove)
below; the restore *mechanism* is now exercised end to end against both repository backends,
including the offsite one. But the Stage 2 acceptance criterion as written - byte-level
equivalence demonstrated by checksums - is **not met**, and cannot be met on this volume.
Choosing a pilot volume with real files on disk is a prerequisite for closing it.

## Why this exists

kopiur is the staged replacement for VolSync (migration plan: `kubernetes/apps/base/system/kopiur/README.md`
and `kubernetes/components/kopiur/Readme.md`). Stage 1 put `downloads/autobrr` on both backup
systems simultaneously. Stage 2 is the acceptance gate for the entire migration: **nothing gets
retired anywhere until a restore has been demonstrated.** A backup that has never been restored
is a hypothesis, not a backup.

kopiur raises the stakes over VolSync in one specific way, which is why the gate matters more
here than it did on 2026-08-23: a kopiur `Snapshot` CR **owns** its kopia snapshot through a
finalizer, so deleting CRs can delete backup data. Deleting a VolSync `ReplicationSource` never
touches the restic repository. Every deletion in this procedure is therefore justified against
what it can reach before it is run.

## Hard constraint this procedure satisfies

**Never scale down, overwrite, re-provision, or write into the live app's claim. Not once, not
briefly, not "just to check".**

- **Never** run the in-place `just kube restore` flow (`kubernetes/mod.just` `restore` recipe).
  It scales the target app to zero and restores straight into the live PVC - exactly the shape
  this drill exists to avoid.
- **Never** use the app's standing `${APP}-kopiur-dst` `Restore` object. It carries
  `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`, so Flux creates it once and never reconciles
  it again; any hand edit persists silently forever. It also carries
  `policy.onMissingSnapshot: Continue`, which **silently yields an empty volume** when it finds
  no snapshot - the single worst property a drill can have.
- Use `Restore.spec.target.pvc`. It provisions a brand-new PVC that kopiur owns and **cannot
  address an existing claim at all**. Do not use `target.pvcRef`, which can. `target.populator`
  needs a CSI populator handshake (our `ceph-block` supports it - 33 PVCs already bind that
  way) but it is the wrong mode for a drill because it waits to be claimed rather than
  producing a volume you can inspect.
- Set `policy.onMissingSnapshot: Fail`. This is a **deliberate departure from the component's
  standing Restore**, and it is what makes an empty result trustworthy: with `Fail`, an empty
  restored volume means the snapshot was genuinely empty, not that the restore quietly found
  nothing. This drill depended on that distinction entirely.
- Mount the restored volume **read-only**, and mark it `readOnly: true` on the *volume source*,
  not only on the `volumeMount`. A `volumeMount`-only `readOnly` still lets kubelet run its
  `fsGroup` ownership walk over the volume, which would destroy the restored ownership and
  permission bits before you can record them.

Every object created below is new, uniquely named, labelled `fm.homeops/restore-drill`, and
owned solely by the drill. Nothing that already exists is patched, scaled, or written into.

## Procedure

### 0. Baseline: record `ceph health` and the live claim's identity

```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph health detail
kubectl -n <ns> get pvc <app> -o jsonpath='uid={.metadata.uid}{"\n"}pv={.spec.volumeName}{"\n"}'
kubectl -n <ns> get pod -l app.kubernetes.io/name=<app> \
  -o jsonpath='{range .items[*]}pod={.metadata.name} started={.status.startTime} restarts={.status.containerStatuses[0].restartCount}{"\n"}{end}'
```

Re-run all three at the end. The PVC uid/PV pair and the pod's `startTime` + `restartCount`
are the proof that nothing scaled the app down or re-provisioned its volume.

### 1. Confirm the source snapshots exist, and record what they claim to contain

```bash
kubectl -n <ns> get snapshot.kopiur.home-operations.com -o wide
kubectl -n <ns> get snapshot.kopiur.home-operations.com <name> \
  -o jsonpath='{.status.phase}{" "}{.status.snapshot.kopiaSnapshotID}{" "}{.status.stats}{"\n"}'
```

**Read `.status.stats` before restoring, every time.** `{"filesNew":0,"sizeBytes":0}` means
the snapshot is empty and no restore of it can prove anything about data fidelity. That one
field is the check this drill's headline finding turned on, and it costs nothing to run.

### 2. Check the drill names are free, and that credentials exist in the workload namespace

```bash
kubectl -n <ns> get restore.kopiur.home-operations.com <drill-name>   # must 404
kubectl -n <ns> get pvc <drill-name>                                  # must 404
kubectl get restore.kopiur.home-operations.com,pvc,pod -A -l fm.homeops/restore-drill  # must be empty
kubectl -n <ns> get secret | grep kopiur
```

That last one is load-bearing and easy to miss: a kopiur mover Job runs **in the workload
namespace** and loads repository credentials with `envFrom`, which is namespace-local. A
`ClusterRepository` whose Secrets live only in `system` reconciles perfectly clean and then
fails at run time. The Stage 1 pilot copies them into `downloads` (see
`kubernetes/components/kopiur/Readme.md` "Credentials"); a restore needs them just as a backup
does.

### 3. Create a scratch `Restore` per destination

One per repository backend. A restore that works only from local storage does not prove the
offsite copy.

```yaml
---
apiVersion: kopiur.home-operations.com/v1alpha1
kind: Restore
metadata:
  name: <app>-kopiur-drill-<yyyymmdd>-<dest>     # NOT <app>-kopiur-dst
  namespace: <ns>
  labels:
    fm.homeops/restore-drill: kopiur-<dest>      # makes the cleanup sweep trivial
spec:
  repository:
    kind: ClusterRepository
    name: <dest>                                 # ceph | r2
  source:
    fromPolicy:
      name: <app>-<dest>                         # the app's SnapshotPolicy
      offset: 0                                  # 0 = newest snapshot
  target:
    pvc:                                         # creates a NEW PVC; cannot reach a live claim
      name: <app>-kopiur-drill-<yyyymmdd>-<dest>
      accessModes: ["ReadWriteOnce"]
      capacity: <same as the app's real PVC>
      storageClassName: ceph-block
  policy:
    onMissingSnapshot: Fail                      # fail-closed; see the hard constraint above
  mover:
    podSecurityContext:                          # mirror the app's SnapshotPolicy
      runAsUser: 1000
      runAsGroup: 1000
      fsGroup: 1000
    cache:
      mode: Ephemeral
      capacity: 2Gi
```

`source.fromPolicy` is preferred over `source.snapshotRef` for a drill: it creates no reference
of any kind to a `Snapshot` CR, so there is no path by which the drill's own lifecycle can
reach snapshot data. Confirm afterwards that `offset: 0` resolved to the snapshot you expected
by matching `.status.resolved.kopiaSnapshotID` against step 1 - that gives you `snapshotRef`'s
precision without its coupling.

### 4. Wait for each restore, and verify which snapshot it actually read

```bash
kubectl -n <ns> wait --for=condition=Complete job/<drill-name> --timeout=300s
kubectl -n <ns> get restore.kopiur.home-operations.com <drill-name> \
  -o jsonpath='{.status.phase}{" | "}{.status.logTail}{"\nsnapID="}{.status.resolved.kopiaSnapshotID}{"\n"}'
kubectl -n <ns> logs job/<drill-name> -c mover
```

`phase: Completed` with `Restore completed: snapshot <id>`, and `<id>` equal to the snapshot
recorded in step 1. The mover pod has a `k8tz` init container, so `kubectl logs job/... -c mover`
(or `--all-containers` after it starts) is needed rather than a bare `logs`.

### 5. Mount both restored volumes read-only in one pod and build checksum manifests

Both restored PVCs are RWO but nothing else holds them, so a single pod can mount both - which
lets you diff the destinations against each other directly as well as against live.

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: kopiur-restore-drill-verify
  namespace: <ns>
  labels:
    fm.homeops/restore-drill: kopiur-verify
spec:
  restartPolicy: Never
  securityContext: { runAsUser: 1000, runAsGroup: 1000, runAsNonRoot: true }
  containers:
    - name: verify
      image: docker.io/library/busybox:1.36
      command: ["sh", "-c", "sleep 900"]
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: { drop: ["ALL"] }
      volumeMounts:
        - { name: ceph, mountPath: /restore-ceph, readOnly: true }
        - { name: r2,   mountPath: /restore-r2,   readOnly: true }
  volumes:
    - name: ceph
      persistentVolumeClaim: { claimName: <app>-kopiur-drill-<date>-ceph, readOnly: true }
    - name: r2
      persistentVolumeClaim: { claimName: <app>-kopiur-drill-<date>-r2,   readOnly: true }
```

Then produce a real manifest, not an eyeball check:

```bash
kubectl -n <ns> exec kopiur-restore-drill-verify -- sh -c '
for d in /restore-ceph /restore-r2; do
  echo "############ $d ############"
  ls -ldn $d                                              # root dir mode + numeric owner
  find $d                                                 # every entry, all types, incl. hidden
  printf "files: %s\n" "$(find $d -type f | wc -l)"
  find $d -type f -exec cat {} + 2>/dev/null | wc -c      # total bytes in regular files
  ( cd $d && find . -type f | sort | while read -r f; do sha256sum "$f"; done )
  ( cd $d && find . -type f | sort | while read -r f; do sha256sum "$f"; done | sha256sum )
done'
```

The last line is the **manifest digest**: one sha256 over the sorted `sha256(path)` list. Two
volumes with the same manifest digest have identical file content at identical paths. Compare
it between destinations, and against the live claim.

> **Beware the empty manifest.** `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
> is the sha256 of the empty string. If both sides show it, your manifests matched because both
> were **empty**, which proves nothing. Always print the file count and byte total alongside the
> digest so an empty match cannot be mistaken for a successful comparison. That is precisely
> what happened here.

For the live side, `kubectl exec` into the running app pod and run the same manifest command -
read-only reads against a live volume, never a mount. Two caveats on this app-specific point:
`find` needs `-xdev` so it does not descend into `emptyDir`s mounted *inside* the data dir, and
anything an `emptyDir` masks (autobrr mounts one at `/config/log`) is invisible from the live
pod but **is** present in the restore, so the trees legitimately differ there.

### 6. Prove the live claim was never touched

```bash
kubectl -n <ns> get job <drill-name> \
  -o jsonpath='{range .spec.template.spec.volumes[*]}{.name}{" -> pvc="}{.persistentVolumeClaim.claimName}{"\n"}{end}'
```

Every mover Job must mount only its own drill PVC. Combine with the step 0 re-run.

### 7. Clean up - drill artifacts only

**Confirm what each deletion can reach before running it.** For a drill, all three object kinds
are safe, and here is why:

```bash
# Restore CRs: no finalizers, no ownerReferences -> deletion cannot cascade anywhere.
kubectl -n <ns> get restore.kopiur.home-operations.com <drill-name> \
  -o jsonpath='finalizers={.metadata.finalizers} ownerRefs={.metadata.ownerReferences}{"\n"}'
# Snapshot CRs: carry kopiur.home-operations.com/snapshot-cleanup and OWN their kopia data.
# Confirm none of them has an ownerReference to anything you are about to delete.
kubectl -n <ns> get snapshot.kopiur.home-operations.com \
  -o jsonpath='{range .items[*]}{.metadata.name}{" ownerRefs="}{.metadata.ownerReferences}{"\n"}{end}'
```

Then delete, in this order:

```bash
kubectl -n <ns> delete pod kopiur-restore-drill-verify
kubectl -n <ns> delete restore.kopiur.home-operations.com <drill-name-ceph> <drill-name-r2>
kubectl -n <ns> delete pvc <drill-name-ceph> <drill-name-r2>     # only if still present
```

Deleting the `Restore` CRs is sufficient for almost everything: they own their mover Jobs, so
GC reaps Job -> pod -> the pod's generic-ephemeral `*-kopia-cache` PVC, and releasing the mover
pod is also what lets a `Terminating` drill PVC finish. Deleting the PVCs *first* leaves them
stuck in `Terminating` behind `kubernetes.io/pvc-protection` until the completed mover pod goes
away - harmless, but it makes `kubectl delete --wait` hang, which is what happened here.

Final sweep - must return nothing:

```bash
kubectl get restore.kopiur.home-operations.com,pvc,pod,job -A -l fm.homeops/restore-drill
```

**Never delete an unlabelled object.** In particular never delete a `Snapshot` CR: with
`deletionPolicy: Delete` (the default) that deletes the backup data out of the repository.
Upstream ADR-0006 records a real incident where ownerReference GC fired 600-700 concurrent
snapshot deletions at one repository.

## Results

Both restores ran against `downloads/autobrr`, restoring the two Stage 1 verification snapshots
into fresh 5Gi `ceph-block` PVCs. Both resolved `offset: 0` to exactly the expected snapshot.

| Destination | Snapshot restored | Expected (Stage 1) | Match | Apply -> `Completed` | Mover runtime | PVC bind |
|---|---|---|:--:|---:|---:|---|
| **ceph** (in-cluster RGW) | `8ff00143e56f37faa572f0ef79a7a06c` | `8ff00143…a06c` | yes | 19:23:13Z -> 19:23:23Z, **10s** | 12s (Job) | immediate |
| **r2** (Cloudflare, offsite) | `0a55e125322f1786663b353788d5d135` | `0a55e125…d135` | yes | 19:25:33Z -> 19:25:45Z, **12s** | 14s (Job) | immediate |

Content of both restored volumes, measured read-only:

| Measure | `/restore-ceph` | `/restore-r2` | Live `autobrr` claim |
|---|---|---|---|
| Regular files | 0 | 0 | 0 |
| Total bytes in regular files | 0 | 0 | 0 (12 KiB fs overhead) |
| Directories | `lost+found`, `log` | `lost+found`, `log` | `log` (see note) |
| Root dir mode / owner | `drwxrwsr-x 0:1000` | `drwxrwsr-x 0:1000` | `drwxrwsr-x root:1000` |
| sha256 manifest digest | `e3b0c442…b855` | `e3b0c442…b855` | `e3b0c442…b855` |
| Filesystem | ext4 | ext4 | ext4 |

The manifest digests are equal across all three - and that equality is **vacuous**, because
`e3b0c442…b855` is the sha256 of the empty string. This is the finding, stated plainly: there
was nothing to compare.

What the restore *did* faithfully reproduce is the volume's structure and metadata: the empty
`log` directory that exists in the claim, and the root directory's setgid mode and `root:1000`
ownership, identical on both destinations and matching live. `lost+found` appears only on the
restored volumes because it is created by `mkfs` on the fresh target filesystem, not carried in
the snapshot.

### Live claim untouched - how we know

| Check | Result |
|---|---|
| `autobrr` PVC uid / PV | `897b4178-…` / `pvc-6f0f5288-…`, unchanged; created 2025-10-06 |
| Drill PVCs' PVs | `pvc-3a276ed6-…`, `pvc-a717fd36-…` - three distinct volumes |
| Deployment replicas | `1/1` throughout; never scaled |
| App pod | `autobrr-6cd76b8d99-54pjd`, started 2026-08-26T10:57:30Z, **0 restarts**, ready |
| Mover Job volumes | each mounted only its own drill PVC; the string `autobrr` alone never appears |
| Live-side reads | `kubectl exec` into the running pod only - read-only commands, no mount |

One trap worth naming: the verify pod's `/restore-ceph` and the live pod's `/config` both
appeared as `/dev/rbd8`. RBD device numbering is per-node and the pods were on talos-3 and
talos-1 respectively; the distinct PV names above are the real proof, not the device path.

### Ceph health

Identical before and after, unchanged by the drill:

```
HEALTH_WARN 1 OSD(s) experiencing slow operations in BlueStore; 1 MDSs report slow metadata IOs;
  (muted: AUTH_EMERGENCY_CIPHERS_SET AUTH_INSECURE_CLIENT_KEY_TYPE
          AUTH_INSECURE_KEYS_ALLOWED AUTH_INSECURE_KEYS_CREATABLE)
```

`osd.3` slow BlueStore ops, `mds.ceph-filesystem-c` slow metadata IOs. This is the pre-existing
baseline, not a drill effect. The four muted alerts are the tracked CephX items (see
`docs/ceph-cluster-changelog.md`).

### Cleanup

All drill objects removed; sweep returns nothing:

```
$ kubectl get restore.kopiur.home-operations.com,pvc,pod,job -A -l fm.homeops/restore-drill
No resources found
```

Deliberately retained: both Stage 1 `Snapshot` CRs (`autobrr-ceph-stage1-verify`,
`autobrr-r2-stage1-verify`), the two `SnapshotPolicy`/`SnapshotSchedule` pairs, and the standing
`autobrr-kopiur-dst` `Restore`. All are Git-managed or deliberate Stage 1 state, and deleting a
`Snapshot` CR would delete backup data.

## What this drill proved, and did not prove

**Proved:**

- The kopiur restore path works end to end from **both** repository backends: `Restore` CR ->
  mover Job -> newly provisioned PVC -> bound -> mountable. Roughly 10-12s each for a 5Gi claim.
- The **offsite** R2 repository is readable and its credentials work, independently of the local
  Ceph one. R2 was never exercised by the 2026-08-23 VolSync drill, so this is the first restore
  proof of any kind from that destination.
- `target.pvc` creates a brand-new PVC and never addresses a live claim - the drill-safe mode.
- `source.fromPolicy` with `offset: 0` resolves to the expected newest snapshot in both
  repositories, verified by kopia snapshot ID.
- The Stage 1 namespace-local credential copy (`kopiur-ceph-secret` / `kopiur-r2-secret` in
  `downloads`) is sufficient for the **restore** path, not only the backup path.
- Deleting drill `Restore` CRs and PVCs does not reach snapshot data, and the mechanism why
  (no finalizers, no ownerReferences on the drill objects; no ownerReference from any `Snapshot`
  CR to anything the drill owns) is recorded above.
- The drill leaves `ceph health` unchanged.

**Did not prove - and this is the gate that remains open:**

- **Byte-level data fidelity. There were no bytes.** The Stage 2 acceptance criterion is not
  met. Until a kopiur restore reproduces real file content verified by checksum, no VolSync
  retirement (Stage 5) is justified for any volume.
- Restore of a volume with meaningful size. Both restores moved 0 bytes, so the 10-12s timings
  measure orchestration overhead only and **must not** be extrapolated - kopia mover time scales
  with snapshot content.
- Restore of a CephFS / RWX claim. This drill covered `ceph-block`/RWO only.
- `target.populator` mode against a real claim (Stage 5 mechanism, deliberately out of scope).
- Retention/pruning behaviour, and `Maintenance`.

## Follow-up required before Stage 2 can be signed off

1. **Re-run this drill on a pilot volume that actually has files on disk.** The procedure above
   is unchanged; only the app changes. Verify with `.status.stats` (step 1) *before* restoring
   that the chosen snapshot has non-zero `sizeBytes`.
2. **Add the `.status.stats` check to the Stage 1 gate.** As written, Stage 1's gate
   ("kopiur `Snapshot` reaches `Succeeded`") is satisfied by an empty volume, which is how a
   pilot that moves no data passed it. A `Succeeded` snapshot of 0 bytes is not evidence that
   backup works.
3. **Re-check whether `downloads/autobrr` should be backed up at all.** VolSync has been storing
   three copies of an empty volume for 328 days. Its real state lives in `postgres-17`, which is
   covered by CNPG Barman. This is a captain call, not a drill outcome, and it is deliberately
   *not* actioned here - but it is the cheapest coverage cleanup on the board.

## Safety notes for whoever runs this next

- A restore performs **no writes to the repository**. Kopia takes no lock for a read of this
  shape and the drill wrote nothing to either bucket.
- `mover.cache.mode: Ephemeral` still provisions a real PVC - the CRD calls `capacity` the
  "Size of the PVC backing the mover's kopia cache". It is a generic ephemeral volume named
  `<mover-pod>-kopia-cache`, owned by the mover pod and reaped with it, so nothing *stands*
  after the run; but a drill does transiently consume 2Gi of `ceph-block` per destination.
- Always verify the `<app>-kopiur-drill-*` names are free before applying, so a re-run cannot
  collide with a previous drill's leftovers.
- Do not "tidy up" the standing `<app>-kopiur-dst` `Restore` while it sits `Pending`. `Pending`
  is its correct steady state for the whole parallel run - `target.populator` means it is
  waiting to be claimed, and every claim's `dataSourceRef` still points at VolSync until
  Stage 5.
