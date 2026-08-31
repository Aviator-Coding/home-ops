# kopiur restore drill - 2026-08-30

> **Historical snapshot, but the procedure is durable.** Numbers below were measured live
> against the cluster on 2026-08-30 between 19:20Z and 20:50Z, on branch
> `fm/homeops-kopiur-stage2`. Re-measure timings before relying on them; the *procedure*
> itself (manifests, ordering, verification method, cleanup) is the reusable part.
>
> Sibling document: [`restore-drill-2026-08-23.md`](restore-drill-2026-08-23.md) is the same
> exercise against VolSync, and is the house standard this one is built to. Read its
> "Hard constraint this procedure satisfies" section first; it is binding here too.

## Result

**Stage 2 PASSES.** kopiur restored `downloads/sabnzbd-config` from both the ceph and the r2
repositories into fresh volumes, and all **2,062 files / 2,208,506,538 bytes** came back
byte-identical to the live claim, verified by per-file sha256:

| | files | bytes | sha256 manifest digest |
|---|---:|---:|---|
| live claim (stable set) | 2062 | 2 208 506 538 | `5f748bb724937dabd5c5030135c772d50a6056b38221fcc3dd04356fdb5b4e6f` |
| restored from **ceph** | 2062 | 2 208 506 538 | `5f748bb7…4e6f` |
| restored from **r2** | 2062 | 2 208 506 538 | `5f748bb7…4e6f` |

File modes and numeric ownership were reproduced exactly as well, including five `0600` files.
The live claim was never mounted, scaled, or written to. `ceph health` was unchanged throughout.

Getting there took two attempts and produced **two findings that matter more than the pass
itself**:

1. [The original Stage 1 pilot holds no data at all](#finding-1-the-stage-1-pilot-volume-is-empty),
   so it could never have proven fidelity - and Stage 1's gate did not notice.
2. [kopiur cannot read a claim whose files it does not own](#finding-2-a-mover-identity-mismatch-fails-the-backup-outright),
   which failed both destinations outright, against real data, until the mover identity was
   matched to the workload. VolSync survives the same mismatch, by a mechanism that has its own
   consequence. **This is a rollout prerequisite for every app in Stage 3, not a sabnzbd
   quirk:** any claim whose owning workload does not run as uid 1000, and which contains any
   file without a world-read bit, will fail the same way until `KOPIUR_PUID`/`KOPIUR_PGID` are
   set to match it.

## Why this exists

kopiur is the staged replacement for VolSync (plan: `kubernetes/apps/base/system/kopiur/README.md`,
component: `kubernetes/components/kopiur/Readme.md`). Stage 2 is the acceptance gate for the
entire migration: **nothing gets retired anywhere until a restore has been demonstrated.** A
backup that has never been restored is a hypothesis, not a backup.

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
  standing Restore**, and it is what makes an empty or short result trustworthy rather than
  ambiguous.
- Mount the restored volume **read-only**, and mark it `readOnly: true` on the *volume source*,
  not only on the `volumeMount`. A `volumeMount`-only `readOnly` still lets kubelet run its
  `fsGroup` ownership walk over the volume, which would rewrite the restored ownership and
  permission bits before you can record them - destroying exactly the evidence
  [finding 2](#finding-2-a-mover-identity-mismatch-fails-the-backup-outright) turns on.

Every object created below is new, uniquely named, labelled `fm.homeops/restore-drill`, and
owned solely by the drill. Nothing that already exists is patched, scaled, or written into.

## Procedure

### 0. Baseline: `ceph health` and the live claim's identity

```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph health detail
kubectl -n <ns> get pvc <claim> -o jsonpath='uid={.metadata.uid}{"\n"}pv={.spec.volumeName}{"\n"}'
kubectl -n <ns> get pod -l app.kubernetes.io/name=<app> \
  -o jsonpath='{range .items[*]}pod={.metadata.name} started={.status.startTime} restarts={.status.containerStatuses[0].restartCount}{"\n"}{end}'
```

Re-run all three at the end. The PVC uid/PV pair and the pod's `startTime` + `restartCount` are
the proof that nothing scaled the app down or re-provisioned its volume.

### 1. Check the volume is a valid fidelity subject *before* trusting any snapshot of it

```bash
kubectl -n <ns> exec deploy/<app> -- sh -c 'find /<datadir> -xdev -type f | wc -l; df -k /<datadir>'
kubectl -n <ns> get snapshot.kopiur.home-operations.com <name> \
  -o jsonpath='{.status.phase}{" "}{.status.snapshot.kopiaSnapshotID}{" "}{.status.stats}{"\n"}'
```

**`.status.stats` is the check that this drill was created by missing.**
`{"filesNew":0,"sizeBytes":0}` means the snapshot is empty and no restore of it can prove
anything about data fidelity, no matter how green everything looks. See
[finding 1](#finding-1-the-stage-1-pilot-volume-is-empty).

### 2. Check the mover identity matches the workload that owns the files

```bash
# Prefer measured FILE ownership over the pod's declared runAsUser (Stage 3: plex/tdarr/
# calibre-web-automated run as 0 while owning files 2000:2000; hermes pins no runAsUser).
kubectl -n <ns> exec deploy/<app> -- sh -c 'find /<datadir> -xdev -printf "%u:%g %m %p\n" | head'
kubectl -n <ns> exec deploy/<app> -- sh -c 'find /<datadir> -xdev -type f ! -perm -o=r | head'
kubectl -n <ns> get deploy <app> -o jsonpath='{.spec.template.spec.securityContext}{"\n"}'
```

Set `KOPIUR_PUID`/`KOPIUR_PGID` to the uid/gid that owns the files. The component defaults
(`1000`) fail closed on any file without a world-read bit when ownership differs. kopiur's
admission webhook warns about this at apply time - **do not dismiss that warning**. See
[finding 2](#finding-2-a-mover-identity-mismatch-fails-the-backup-outright).

### 3. Check the drill names are free, and projection is what will feed the mover

```bash
kubectl -n <ns> get restore.kopiur.home-operations.com <drill-name>   # must 404
kubectl -n <ns> get pvc <drill-name>                                  # must 404
kubectl get restore.kopiur.home-operations.com,pvc,pod -A -l fm.homeops/restore-drill  # must be empty
# Between runs there must be NO standing repository credential in the workload ns.
# During a run you may briefly see <snapshot>-creds-N copies; that is projection working.
kubectl -n <ns> get secret | grep -E 'kopiur|-creds-' || true
```

Do **not** expect a standing `kopiur` / `kopiur-*-secret` Secret in the workload namespace -
those pilot copies are gone. A kopiur mover Job still runs **in the workload namespace** and
still loads credentials with `envFrom`, which is namespace-local, but the operator now mints
the Secret for the length of the run and reaps it. A hand-written drill `Restore` therefore
**must** set `credentialProjection.enabled: true` (step 6) or the CRs reconcile clean and the
mover fails at run time with nothing to authenticate with. Full contract:
`kubernetes/components/kopiur/Readme.md` "Credentials".

### 4. Capture the live checksum manifest, twice, bracketing the snapshot

A live volume is being written to while you drill it, so "compare the restore to live" is not
well defined unless you say *which* live. Bracket it:

```bash
MANIFEST='cd /<datadir> && find . -xdev -path ./lost+found -prune -o -type f -print \
  | LC_ALL=C sort | while IFS= read -r f; do sha256sum "$f"; done'

kubectl -n <ns> exec deploy/<app> -c app -- sh -c "$MANIFEST" > L1-live.txt   # BEFORE snapshot
#   ... take the snapshots (step 5) ...
kubectl -n <ns> exec deploy/<app> -c app -- sh -c "$MANIFEST" > L2-live.txt   # AFTER  snapshot

comm -12 <(sort L1-live.txt) <(sort L2-live.txt) > STABLE.txt   # unchanged across the window
```

`STABLE.txt` is the **stable set**: files whose content did not change while the snapshot was
being taken. Every one of them *must* come back byte-identical; a mismatch there is a genuine
fidelity failure and not drift. Files outside the stable set are legitimately allowed to differ.

Two portability notes: `-xdev` is required so `find` does not descend into `emptyDir`/ConfigMap
volumes mounted *inside* the data directory (sabnzbd mounts a ConfigMap at `/config/scripts`,
autobrr an `emptyDir` at `/config/log`); and `lost+found` is `root:root 0700`, unreadable by the
app's own uid and recreated by `mkfs` on any restore target, so prune it on both sides.

### 5. Take a snapshot per destination (only if the volume is not already covered)

```yaml
apiVersion: kopiur.home-operations.com/v1alpha1
kind: Snapshot
metadata: { name: <app>-<dest>-verify, namespace: <ns> }
spec:
  policyRef: { name: <app>-<dest> }
  description: "on-demand verification"
```

Then confirm `filesNew` and `sizeBytes` are what you expect from step 1 before going further.

### 6. Create a scratch `Restore` per destination

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
  # Required on every hand-written Restore: no standing repo credentials live in app
  # namespaces any more. Without this the CR goes green and the mover fails at run time.
  # See kubernetes/components/kopiur/Readme.md "Credentials".
  credentialProjection:
    enabled: true
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
    onMissingSnapshot: Fail                      # fail-closed
  mover:
    podSecurityContext:                          # MUST match the app - see finding 2
      runAsUser: 2000
      runAsGroup: 2000
      fsGroup: 2000
    cache:
      mode: Ephemeral
      capacity: 2Gi
```

`source.fromPolicy` is preferred over `source.snapshotRef` for a drill: it creates no reference
of any kind to a `Snapshot` CR, so there is no path by which the drill's own lifecycle can reach
snapshot data. Confirm afterwards that `offset: 0` resolved to the snapshot you expected by
matching `.status.resolved.kopiaSnapshotID` - that gives you `snapshotRef`'s precision without
its coupling.

### 7. Wait, and verify which snapshot each restore actually read

```bash
kubectl -n <ns> wait --for=jsonpath='{.status.phase}'=Completed \
  restore.kopiur.home-operations.com/<drill-name-ceph> \
  restore.kopiur.home-operations.com/<drill-name-r2> --timeout=300s
kubectl -n <ns> get restore.kopiur.home-operations.com <drill-name> \
  -o jsonpath='{.status.phase}{" "}{.status.resolved.kopiaSnapshotID}{"\n"}'
```

The mover pod has a `k8tz` init container, so use `kubectl logs job/<name> -c mover` rather than
a bare `logs`.

### 8. Mount both restored volumes read-only in one pod and compare

Both restored PVCs are RWO but nothing else holds them, so one pod can mount both - which lets
you diff the destinations against each other as well as against live.

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
  securityContext: { runAsUser: 2000, runAsGroup: 2000, runAsNonRoot: true }   # match the app
  containers:
    - name: verify
      image: docker.io/library/busybox:1.36
      command: ["sh", "-c", "sleep 1800"]
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

Run the same `$MANIFEST` command from step 4 against `/restore-ceph` and `/restore-r2`, then:

```bash
diff <(sort STABLE.txt) <(sort R-ceph.txt)    # must be empty
diff <(sort STABLE.txt) <(sort R-r2.txt)      # must be empty
diff <(sort R-ceph.txt) <(sort R-r2.txt)      # must be empty
sort R-ceph.txt | sha256sum                   # the manifest digest, for the record
```

Also record the file count and total byte count next to the digest:

```bash
find /restore-<dest> -xdev -path '*/lost+found' -prune -o -type f -print | wc -l
find /restore-<dest> -xdev -path '*/lost+found' -prune -o -type f -exec cat {} + | wc -c
```

> **Beware the empty manifest.** `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
> is the sha256 of the empty string. If both sides show it, your manifests matched because both
> were **empty**, which proves nothing. Printing the count and byte total alongside the digest is
> what makes an empty match impossible to mistake for a successful comparison. That is exactly
> the trap the first attempt below fell into.

And compare permissions, not only content:

```bash
kubectl -n <ns> exec deploy/<app>                  -- sh -c 'cd /<datadir>    && ls -lan <files> | awk "{print \$1,\$3,\$4,\$NF}"'
kubectl -n <ns> exec kopiur-restore-drill-verify   -- sh -c 'cd /restore-ceph && ls -lan <files> | awk "{print \$1,\$3,\$4,\$NF}"'
```

### 9. Prove the live claim was never touched

```bash
kubectl -n <ns> get job <drill-name> \
  -o jsonpath='{range .spec.template.spec.volumes[*]}{.name}=>{.persistentVolumeClaim.claimName}{"\n"}{end}'
```

Every mover Job must mount only its own drill PVC. Combine with the step 0 re-run.

### 10. Clean up - drill artifacts only

**Confirm what each deletion can reach before running it.**

```bash
# Restore CRs: no finalizers, no ownerReferences -> deletion cannot cascade anywhere.
kubectl -n <ns> get restore.kopiur.home-operations.com <drill-name> \
  -o jsonpath='finalizers={.metadata.finalizers} ownerRefs={.metadata.ownerReferences}{"\n"}'
# Snapshot CRs: carry kopiur.home-operations.com/snapshot-cleanup and OWN their kopia data.
kubectl -n <ns> get snapshot.kopiur.home-operations.com \
  -o custom-columns='NAME:.metadata.name,SNAPID:.status.snapshot.kopiaSnapshotID,OWNERREFS:.metadata.ownerReferences'
```

Then delete, in this order:

```bash
kubectl -n <ns> delete pod kopiur-restore-drill-verify
kubectl -n <ns> delete restore.kopiur.home-operations.com <drill-name-ceph> <drill-name-r2>
kubectl -n <ns> delete pvc <drill-name-ceph> <drill-name-r2>     # only if still present
```

Deleting the `Restore` CRs first is what makes the rest simple: they own their mover Jobs, so GC
reaps Job -> pod -> the pod's generic-ephemeral `*-kopia-cache` PVC, and releasing the mover pod
is also what lets a drill PVC finish deleting. Deleting the PVCs *first* leaves them stuck in
`Terminating` behind `kubernetes.io/pvc-protection` until the completed mover pod goes away -
harmless, but `kubectl delete --wait` hangs, which is what happened on the first attempt here.

Final sweep - must return nothing:

```bash
kubectl get restore.kopiur.home-operations.com,pvc,pod,job -A -l fm.homeops/restore-drill
```

**Never delete an unlabelled object.** In particular never delete a `Snapshot` CR that has a
`kopiaSnapshotID`: with `deletionPolicy: Delete` (the default) that deletes the backup data out
of the repository. Upstream ADR-0006 records a real incident where ownerReference GC fired
600-700 concurrent snapshot deletions at one repository. A **failed** Snapshot CR
(`SNAPID <none>`) holds no data and is safe to delete, which is how the failed attempts in
finding 2 were cleared.

## Results

### Attempt 1 - `downloads/autobrr`: mechanism proven, fidelity impossible

The Stage 1 pilot. Both restores completed and resolved to exactly the expected snapshots:

| Destination | Snapshot restored | Expected (Stage 1) | Match | Apply -> `Completed` |
|---|---|---|:--:|---:|
| **ceph** | `8ff00143e56f37faa572f0ef79a7a06c` | `8ff00143…a06c` | yes | 19:23:13Z -> 19:23:23Z, **10s** |
| **r2** | `0a55e125322f1786663b353788d5d135` | `0a55e125…d135` | yes | 19:25:33Z -> 19:25:45Z, **12s** |

Both restored volumes contained **0 files, 0 bytes**, matching the live claim, with the empty
`log` directory and the root directory's setgid `root:1000` mode reproduced identically. The
sha256 manifest digest agreed across ceph, r2 and live - at `e3b0c442…b855`, the digest of the
empty string. See [finding 1](#finding-1-the-stage-1-pilot-volume-is-empty).

This attempt is retained in the record because it is a valid **mechanism** test - it is the
proof that the restore path, the R2 credentials, and `target.pvc` all work - and because a drill
that hides why its first attempt proved nothing is less useful than one that says so.

### Attempt 2 - `downloads/sabnzbd-config`: the fidelity proof

2,062 files / 2.06 GiB of real config data; claim name (`sabnzbd-config`) differs from the app
name (`sabnzbd`), so this also exercised the `${KOPIUR_CLAIM:-${APP}}` override that autobrr
never did.

First run of both destinations **failed** - see
[finding 2](#finding-2-a-mover-identity-mismatch-fails-the-backup-outright). After matching the
mover identity to the workload (`KOPIUR_PUID`/`KOPIUR_PGID: 2000`):

| Destination | Snapshot | `filesNew` | `sizeBytes` | Restore resolved to | Apply -> `Completed` |
|---|---|---:|---:|---|---:|
| **ceph** | `da9290a7b5f2e30b8144459ed60386a8` | 2062 | 2 208 506 538 | `da9290a7…86a8` | 19:46:27Z -> ≤19:47:53Z, **≤86s** |
| **r2** | `3597d0ea386278da00ff7457079b7fc3` | 2062 | 2 208 506 538 | `3597d0ea…7fc3` | 19:46:27Z -> ≤19:47:53Z, **≤86s** |

Both restores were applied together and waited on together, so 86s is an upper bound covering
both, not a per-destination measurement.

**Content comparison.** The live manifest was captured at 19:39:17Z and again at 19:46:04Z,
bracketing the snapshots at 19:45:4xZ. All 2062 files were byte-identical between the two live
captures, so the **stable set was the entire volume** - there were no files legitimately allowed
to differ, which makes this an unusually strict comparison.

| Comparison | Result |
|---|---|
| live stable set vs **ceph** restore | **identical** - every path, every sha256 |
| live stable set vs **r2** restore | **identical** - every path, every sha256 |
| **ceph** restore vs **r2** restore | **identical** - the two destinations agree byte-for-byte |
| file count (live / ceph / r2) | 2062 / 2062 / 2062 |
| total bytes (ceph / r2) | 2 208 506 538 / 2 208 506 538, matching `sizeBytes` exactly |
| manifest digest (all three) | `5f748bb724937dabd5c5030135c772d50a6056b38221fcc3dd04356fdb5b4e6f` |

**Permission comparison.** Mode and numeric ownership were also reproduced exactly on both
destinations, across all eleven `sabnzbd.ini*` files - including the five `0600` and two `0660`
files that the identity mismatch had made unreadable:

```
-rw-rw-r-- 2000 2000 sabnzbd.ini          -rw------- 2000 2000 sabnzbd.iniEpDANg
-rw-rw-r-- 2000 2000 sabnzbd.ini.bak      -rw------- 2000 2000 sabnzbd.iniHpnibH
-rw------- 2000 2000 sabnzbd.iniEAbhLb    -rw-rw---- 2000 2000 sabnzbd.inialCDKK
-rw-rw---- 2000 2000 sabnzbd.iniEbGjeJ    -rw------- 2000 2000 sabnzbd.inibdaNcG
-rw------- 2000 2000 sabnzbd.inicgFcbA    -rw-rw-r-- 2000 2000 sabnzbd.inidihDpF
-rw-rw-r-- 2000 2000 sabnzbd.inigOEpkO
```

### Live claims untouched - how we know

| Check | `autobrr` | `sabnzbd-config` |
|---|---|---|
| PVC uid / PV, before and after | `897b4178…` / `pvc-6f0f5288…` unchanged | `b67944c2…` / `pvc-4bfd61af…` unchanged |
| Deployment replicas | `1/1` throughout | `1/1` throughout |
| App pod | started 2026-08-26T10:57:30Z, **0 restarts** | started 2026-08-26T10:57:32Z, **0 restarts** |
| Mover Job volumes | only its own drill PVC | only its own drill PVC |
| Live-side reads | `kubectl exec` into the running pod only - read-only commands, never a mount | same |

kopiur's own staging is what makes this structurally safe rather than merely careful: each
`SnapshotPolicy` took its **own CSI `VolumeSnapshot`** of `sabnzbd-config`
(`sabnzbd-ceph-stage2-verify-snap`, `sabnzbd-r2-stage2-verify-snap`, both `readyToUse: true`)
and the mover mounted a clone of that, `readOnly: true`. The live claim is never mounted by
kopiur at all.

One trap worth naming: the verify pod's `/restore-ceph` and the live pod's data volume both
appeared as `/dev/rbd8`. RBD device numbering is per-node and the pods were on different nodes;
the distinct PV names are the real proof, not the device path.

### Ceph health

Identical before, during and after. Unchanged by the drill:

```
HEALTH_WARN 1 OSD(s) experiencing slow operations in BlueStore; 1 MDSs report slow metadata IOs;
  (muted: AUTH_EMERGENCY_CIPHERS_SET AUTH_INSECURE_CLIENT_KEY_TYPE
          AUTH_INSECURE_KEYS_ALLOWED AUTH_INSECURE_KEYS_CREATABLE)
```

`osd.3` slow BlueStore ops, `mds.ceph-filesystem-c` slow metadata IOs - the pre-existing
baseline, not a drill effect. Raw usage 30.86%, 12 TiB available. The four muted alerts are the
tracked CephX items (`docs/ceph-cluster-changelog.md`).

## Finding 1: the Stage 1 pilot volume is empty

`downloads/autobrr` keeps all of its state in the shared `postgres-17` CNPG cluster
(`AUTOBRR__DATABASE_TYPE=postgres`). Its 5Gi `ceph-block` claim holds **zero files** and has done
for the 328 days it has existed. Four independent sources agree, none of them this drill:

| Evidence | Result |
|---|---|
| live pod, `find /config -xdev -type f \| wc -l` | `0` (only the `log` mount point dir) |
| live pod, `df -k /config` | `12` KiB used of 5 074 592 KiB |
| kopiur `Snapshot` CRs, `.status.stats`, both destinations | `{"filesNew":0,"sizeBytes":0}` |
| VolSync restic mover logs, all **three** destinations | `processed 0 files, 0 B in 0:00` |

**Consequences worth carrying forward:**

- Stage 1's gate ("kopiur `Snapshot` reaches `Succeeded`") is satisfiable by a volume that moves
  no data. A `Succeeded` snapshot of 0 bytes is not evidence that backup works. The
  `.status.stats` check in step 1 above is the cheap fix.
- VolSync has been storing three copies of an empty volume for 328 days. Whether `autobrr`
  should be backed up at all is a separate captain call, deliberately not actioned here.
- `autobrr` remains onboarded to kopiur. It is a working dual-backup volume and a valid
  mechanism test; it is simply not a fidelity subject.

## Finding 2: a mover identity mismatch fails the backup outright

With the component's default `KOPIUR_PUID`/`KOPIUR_PGID` of `1000`, **both destinations failed**
on `sabnzbd-config`:

```
snapshot create failed (class PermissionDenied): ... Found 7 fatal error(s) while snapshotting
Error when processing "sabnzbd.iniEAbhLb": unable to open file: ... permission denied
```

The 7 files are exactly those with **no world-read bit** - five `0600` and two `0660` owned by
group `2000`. The other four `sabnzbd.ini*` files are `0664` and were readable through the
"other" bit. sabnzbd runs as `2000:2000`.

**Three things make this worth recording rather than just fixing:**

1. **kopiur failed closed.** It wrote no snapshot at all rather than a partial one - `phase:
   Failed`, `kopiaSnapshotID` empty. The admission webhook had also warned at apply time, naming
   the exact risk: *"the backup may fail with permission denied or silently skip unreadable
   files"*. It failed rather than skipped, which is the safe half of that warning.
2. **VolSync does not fail on the same claim, and the reason has a consequence.** VolSync's
   component carries the identical `VOLSYNC_PUID:-1000` default and `sabnzbd` does not override
   it, yet its movers report `Successful`, `processed 2061 files, 2.057 GiB`. The difference is
   the mount: kopiur mounts the staged source `readOnly: true` (`sources[].readOnly` defaults
   true) and **kubelet does not apply `fsGroup` to a read-only mount**, so the mover's gid never
   gains access. VolSync mounts its staged clone writable, so kubelet's `fsGroup` walk rewrites
   group ownership and permissions before restic reads it. That is why kopiur, with a matched
   identity and no such rewrite, reproduced the **original** `0600` modes exactly.
3. **The fix is per-app and must not be forgotten.** Any app whose pod `securityContext` is not
   `1000` needs `KOPIUR_PUID`/`KOPIUR_PGID` set to match, or the mover cannot read its files.
   `kubernetes/apps/main/downloads/sabnzbd.yaml` carries the pair with the reasoning inline.
   Step 2 of the procedure is the pre-flight check.

## Cleanup

All drill objects removed; the sweep returns nothing:

```
$ kubectl get restore.kopiur.home-operations.com,pvc,pod,job -A -l fm.homeops/restore-drill
No resources found
```

**Deliberately retained** - four `Snapshot` CRs, each owning real kopia data:

| Snapshot CR | kopia snapshot | What it is |
|---|---|---|
| `autobrr-ceph-stage1-verify` | `8ff00143…a06c` | Stage 1 first backup (empty volume) |
| `autobrr-r2-stage1-verify` | `0a55e125…d135` | Stage 1 first backup (empty volume) |
| `sabnzbd-ceph-stage2-verify` | `da9290a7…86a8` | Stage 2 first backup, 2.06 GiB |
| `sabnzbd-r2-stage2-verify` | `3597d0ea…7fc3` | Stage 2 first backup, 2.06 GiB |

Also retained: both apps' `SnapshotPolicy`/`SnapshotSchedule` pairs and their standing
`*-kopiur-dst` `Restore` objects. The standing Restores sit `Pending`, which is their correct
steady state for the whole parallel run - `target.populator` means "waiting to be claimed", and
every claim's `dataSourceRef` still points at VolSync until Stage 5. Do not "tidy" them.

The two **failed** `sabnzbd-*-stage2-verify` Snapshot CRs from the first attempt were deleted
after confirming `kopiaSnapshotID` was empty, so no backup data was involved.

## What this drill proved, and did not prove

**Proved:**

- **Byte-level restore fidelity from both destinations**, over 2062 files / 2.06 GiB, by per-file
  sha256 against a bracketed live baseline - content, file count, total bytes, modes and
  ownership all identical. This is the Stage 2 acceptance gate, and it is met.
- The **offsite** R2 repository restores independently of the local Ceph one. R2 was never
  exercised by the 2026-08-23 VolSync drill, so this is the first restore proof from that
  destination by either engine.
- `target.pvc` creates a brand-new PVC and never addresses a live claim; `source.fromPolicy`
  with `offset: 0` resolves to the expected snapshot, verified by kopia snapshot ID all four
  times.
- The `${KOPIUR_CLAIM:-${APP}}` override works where claim name ≠ app name.
- kopiur's staging never mounts the live claim - each policy takes its own CSI `VolumeSnapshot`.
- The Stage 1 namespace-local credential copy serves the **restore** path, not only backup.
- Deleting drill objects cannot reach snapshot data, with the mechanism recorded.
- kopiur backing up a claim does not disturb VolSync's objects on that same claim: all six
  `ReplicationSource`s across both pilot apps stayed `Successful` throughout, and nothing in
  this drill touched them.
- **VolSync simultaneity on both onboarded volumes.** After the kopiur snapshots, VolSync
  completed successful runs on the same claims. Observed live from
  `.status.lastSyncTime` / `.status.latestMoverStatus.result` (not inferred from schedule):

  | `ReplicationSource` | kopiur snapshot on that claim | VolSync `lastSyncTime` | `latestMoverStatus.result` |
  |---|---|---|---|
  | `sabnzbd-ceph` | 2026-08-30T19:45:46Z | **2026-08-30T20:31:24Z** | Successful |
  | `autobrr-ceph` | 2026-08-30T18:53:22Z | **2026-08-30T20:46:07Z** | Successful |

  Both VolSync completions are strictly after the matching kopiur snapshot. The Stage 1
  "both systems working simultaneously" thread is therefore closed as observed, not pending.
  Re-check with:

  ```bash
  kubectl -n downloads get replicationsource \
    -o custom-columns='NAME:.metadata.name,LASTSYNC:.status.lastSyncTime,RESULT:.status.latestMoverStatus.result' \
    | grep -E 'autobrr|sabnzbd'
  ```

**Did not prove:**

- Restore of a large PVC. 2.06 GiB restored in under ~86s; `immich` (100Gi) and `syncthing-data`
  (100Gi) will be substantially slower and should be measured, not extrapolated.
- Restore of a CephFS / RWX claim. Both attempts covered `ceph-block`/RWO only.
- `target.populator` mode against a real claim - the Stage 5 mechanism, deliberately out of scope.
- Retention/pruning behaviour, `Maintenance`, or restore from any snapshot other than the newest
  (`offset: 0` throughout).
- Anything about the rest of the fleet at drill time. Only two volumes were onboarded to kopiur
  then; this drill is not fleet coverage. Stage 3 later put 29 of 31 VolSync claims on kopiur
  alongside VolSync - still not a fleet-wide restore proof (status owner:
  [`kubernetes/components/kopiur/Readme.md`](../../kubernetes/components/kopiur/Readme.md)).

## Follow-ups

1. **Add the `.status.stats` non-zero check to the Stage 1/Stage 3 onboarding gate.** A
   `Succeeded` snapshot of an empty volume currently passes it. *Stage 3 recorded the lesson
   (empty/near-empty volumes stay onboarded but a green snapshot proves nothing until
   `.status.stats` is non-zero). Continuous fleet monitoring is done (2026-08-31):
   `KopiurBackupEmpty` in `kubernetes/apps/base/system/kopiur/app/prometheusrule.yaml`
   pages on `kopiur_policy_last_backup_files == 0`, pinned by
   `scripts/ci/backup-silent-failure-alerting-test.py`. The onboarding CI pin is still
   GitOps-contract only, not a live stats gate.*
2. **Add the mover-identity check to the Stage 3 rollout.** **Done in Stage 3** - every
   onboarded claim pins a measured `KOPIUR_PUID`/`KOPIUR_PGID` (from file ownership, not
   `runAsUser`), enforced by `scripts/ci/kopiur-stage3-test.py`. Still open: whether the
   component should adopt `mover.inheritSecurityContextFrom.pvcConsumer` - which the webhook
   itself recommends - so the identity is derived rather than hand-maintained per app. That is a
   component-wide change and was deliberately **not** made in Stage 2 or 3.
3. **Reconcile the 2061 vs 2062 file count.** kopiur records 2062 files for `sabnzbd-config`,
   matching a live count taken as the app's own uid; VolSync's restic reports 2061. The
   difference may be nothing more than restic's counting convention, but it has not been
   investigated and should not be assumed benign.
4. **Consider whether VolSync's writable staging is a fidelity concern of its own.** kopiur
   reproduces the original modes; whether a VolSync restore does was not tested here and is a
   separate drill.
5. **Re-examine whether `downloads/autobrr` needs volume backup at all** (finding 1).
6. Measure a large-PVC and a CephFS/RWX restore before Stage 5 retires anything in those classes.
