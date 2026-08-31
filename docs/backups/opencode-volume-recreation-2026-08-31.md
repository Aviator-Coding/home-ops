# `ai/opencode` volume recreation - 2026-08-31

> Measured record of the first execution of
> [`corrupt-claim-recreation-runbook.md`](corrupt-claim-recreation-runbook.md). The
> procedure lives in that document; this one is the evidence. All times UTC unless the
> quoted tool output says otherwise.

## Result

**PASS.** The corrupt RBD image was destroyed and the claim rebuilt from the restic ceph
repository. Both engines back it up again, a clone of a **new** backup mounts cleanly, and
every `opencode` alert cleared.

| gate | result |
|---|---|
| data back | 4749/4749 files, **4744 byte-identical**; 5 differ, all app-written at startup |
| VolSync ceph | ✅ snapshot `5d72f28a`, 4749 files, 154.131 MiB |
| VolSync minio | ✅ snapshot `81f18d92`, 4749 files, 154.131 MiB |
| VolSync r2 | ✅ synced `11:39:31Z` |
| kopiur ceph | ✅ snapshot `b2fdf535020b18f89572e819d297d436`, `filesNew: 4749`, `sizeBytes: 161589393` |
| clone of a **new** backup mounts | ✅ `/dev/rbd15`, **zero** fsck errors, 4749 files |
| alerts | ✅ 0 active alerts matching `opencode` |
| drift | ✅ nothing suspended; no not-ready Kustomization; all scratch objects deleted |

## The finding that changed the plan

The task assumed deleting the PVC would let Flux "recreate it, repopulated from the last
good backup". **It would not have.** Reading `opencode-dst` before touching anything:

```
status.lastSyncTime:   2026-08-27T10:20:07Z
status.latestImage:    volsync-opencode-dst-dest-20260827062006
status.latestMoverStatus.logs:
    created restic repository c1a34b6f38 at s3:.../volsync/opencode
    No eligible snapshots found
    === No data will be restored ===
```

`opencode` was onboarded on 2026-08-27 when its restic repository did not yet exist, so
the one-shot `restore-once` restored **nothing** and `latestImage` is a snapshot of an
**empty 20 GiB volume**. Because `${APP}-dst` carries `ssa: IfNotPresent`, Flux never
re-runs it, so that empty image would still have been the populator's source four days
later. Deleting the PVC alone would have produced an empty volume with PVC `Bound`, app
`Running` and Flux `Ready` - a silent total data loss.

Corollary worth keeping: **the 4,749 files were never restored content.** They are what
the app wrote during its own first boot (06:19-06:22 local on 2026-08-27), which is why
they all carry timestamps from that window.

The fix was to delete `opencode-dst` **together with** the PVC so Flux recreated it as a
new object, firing `restore-once` for the first time against the now-populated repository.
That avoids patching `spec.trigger.manual` on the app's own destination, which
`AGENTS.md` forbids and which `IfNotPresent` would have made permanent drift.

## Step 1 - inventory of the live claim

Taken from inside the running app pod at `/home/opencode`, before any change.

| | value |
|---|---|
| regular files | **4749** |
| directories | 950 |
| symlinks | 8 |
| total bytes | **161 617 941** (154.131 MiB) |
| sha256 manifest digest | `9f400f6d6b99f25c039b763d5458b8ec4fb0347e9149baa3e88ba92a28fafc55` |
| ownership | 5705 × `opencode:opencode`, 1 × `root:opencode` (`lost+found`) |
| modes | 4698 × `644`, 948 × `2755`, 42 × `755`, 8 × `777`, 6 × `444`, 3 × `600`, 1 × `2770` |

Top level: `.cache` 4.2M, `.config` 62.3M, `.local` 560K, `.npm` 90.7M, `projects` 12.1M
(`projects/home-ops`, a git checkout), plus `.gitconfig`, `.git-credentials`, `lost+found`.

**Credential file:** `/home/opencode/.git-credentials` is present -
`mode=600 owner=opencode:opencode size=128`. Its contents were never read, printed,
logged or committed. Only this metadata line and its per-file sha256 (a hash, not content,
held only in the local manifest) were recorded.

The live claim's file count and byte total matched the newest restic snapshot exactly
(`processed 4749 files, 154.131 MiB`), which is how the repository was confirmed complete
before anything was deleted.

## Step 2 - pre-check: proving the restore before destroying anything

A scratch `ReplicationDestination` (`opencode-precheck-dst`, never the app's own) restored
`snapshot 4f8214f8` from the ceph repository into a fresh PVC, and a clone of the
resulting snapshot was mounted in a throwaway pod.

- The clone **mounted cleanly** - the operation that had been failing on `rbd11/13/14/16/17/18/21`.
- 4749 files, 161 617 920 bytes.
- **4748 of 4749 files byte-identical** to the live claim.
- The single difference was `./.cache/opencode/models.json`, a catalogue cache the running
  app rewrites; `./lost+found` was absent.

That result made the recreation safe to attempt. The scratch destination and its snapshot
were kept as a fallback until the real recreation succeeded, then deleted.

## Step 3 - execution

A final `VolumeSnapshot` of the corrupt claim was taken first, as instructed, and
**succeeded** (`opencode-final-pre-recreation-20260831`, `readyToUse: true`, 6 s). It was
released during cleanup: a snapshot of the corrupt image is a decoy rather than a safety
net, because cloning it is exactly what fails. The real fallbacks were restic snapshot
`4f8214f8` and the pre-check snapshot, both verified by mount.

Out-of-band actions, each necessary because Flux cannot express "destroy and rebuild this
volume":

| action | why |
|---|---|
| `flux suspend ks opencode` | stop Flux re-applying `pvc.yaml` between the PVC and destination deletes |
| `kubectl scale deploy opencode --replicas=0` | RWO claim; `pvc-protection` blocks deletion while a pod holds it |
| delete 4 wedged mover **Jobs** | owned by their controllers and recreated on the next run; destroys no backup data |
| delete staged clone PVCs + VolumeSnapshots | clones of the corrupt image; must precede the PVC so ceph-csi deletes the parent cleanly |
| delete the 3 `ReplicationSource`s | VolSync re-staged new clones within seconds of each delete, relooping; deleting an RS never touches the restic repository, and Flux recreates them on resume |
| delete PVC `opencode` | the point of the exercise; `ceph-block` is `reclaimPolicy: Delete`, so PV `pvc-73016488…` and RBD image `csi-vol-d10db6b4…` were genuinely destroyed |
| delete `opencode-dst` | the empty-`latestImage` problem above |
| `flux resume` + `reconcile ks` + `reconcile hr --force` | rebuild everything from Git and return `replicas` through Helm rather than by hand |

Nothing was left suspended and no hand-patched field survives. `replicas` is chart-owned
(confirmed in the Helm release manifest) so the forced upgrade genuinely removed the
scale-down rather than leaving it as drift.

The rebuilt destination restored `snapshot 4f8214f8` into a **new** PV
`pvc-a173b28b-2125-471b-b6bd-9d468600eaf2`, and the app returned `1/1 Running`.

## Step 4 - verification

### 1. Data is back

| | files | dirs | bytes |
|---|---:|---:|---:|
| before | 4749 | 950 | 161 617 941 |
| after | 4749 | 949 | 161 618 344 |

**4744 of 4749 files are byte-identical.** Every difference is accounted for:

- **5 files differ**, all written by the app on startup:
  `.cache/opencode/models.json` (catalogue cache), `.gitconfig` (rewritten by the
  entrypoint), `.local/share/opencode/log/opencode.log`,
  `.local/share/opencode/opencode.db` and `.db-shm` (SQLite state). These changed between
  the 20:45Z backup and the cutover; they are not data loss.
- **`lost+found` is absent** (hence 949 rather than 950 directories). restic never stores
  it and VolSync restores with `--delete`, which removes the one `mkfs` created.
- **Modes gained a group-write bit throughout**: `644→664`, `755→775`, `2755→2775`,
  `600→660`, `444→664`. This is the documented VolSync restore fingerprint - the mover
  stages its destination writable, so kubelet's recursive `fsGroup: 1000` walk relaxes
  every mode before restic writes. It notably takes **`.git-credentials` from `0600` to
  `0660`**. Benign here (group `opencode` is gid 1000, the app's own identity, and it is
  the only principal on the volume) but it is a real, permanent relaxation and is stated
  rather than glossed. Ownership is unchanged and uniform: 5705 × `opencode:opencode`.

### 2. kopiur backs up again (ceph)

`opencode-ceph-20260831095617` → `Succeeded`, snapshot
`b2fdf535020b18f89572e819d297d436`, `status.stats: {filesNew: 4749, sizeBytes: 161589393}`
- non-zero and matching the live claim. (`sizeBytes` trails the live byte count by the
app's own ongoing log/db writes.)

### 3. VolSync backs up again

```
ceph  → Successful   processed 4749 files, 154.131 MiB   snapshot 5d72f28a
minio → Successful   processed 4749 files, 154.131 MiB   snapshot 81f18d92
r2    → Successful   synced 2026-08-31T11:39:31Z
```

All three destinations, not just the required one.

### 4. A clone of a NEW backup mounts cleanly

The scratch destination was re-triggered against the newest snapshot - `restoring snapshot
5d72f28a`, i.e. a backup taken **after** the recreation - and a clone of the resulting
snapshot was mounted:

```
/dev/rbd15   19.5G   169.9M   19.3G   1%  /data
files=4749   dirs=949   bytes=161617920
fsck-error-lines: 0
```

This is the direct disproof of the original failure: cloning-and-mounting is precisely
what produced `Resize inode not valid` on seven different devices, and it now succeeds.
Against the live claim the restored copy differs in the same 5 continuously-written app
files and nothing else.

### 5. Alerts cleared

Alertmanager `/api/v2/alerts?active=true` returns **0** alerts matching `opencode`.
Before: `VolSyncSyncStalledCeph`, `VolSyncSyncStalledMinio`, `VolSyncVolumeOutOfSync` ×2
(all `critical`), plus `KubeJobNotCompleted` and `KubeContainerWaiting` ×4.

## Residual state

One artifact is deliberately left in place: kopiur `Snapshot`
`opencode-ceph-20260831055425`, phase **`Failed`**. It is the run that was wedged on the
corrupt volume. It holds **no** kopia snapshot (`.status.snapshotID` empty), so it owns no
backup data, but the standing rule is that a kopiur `Snapshot` CR is never deleted -
its finalizer is what would delete real backup data - so it was left for kopiur's own
`failedJobsHistoryLimit: 10` to age out.

Worth knowing for next time: that CR sat `Running` with a `Pending` mover pod after its
staging PVC was removed, and its Job carries `activeDeadlineSeconds: 172800`. Since the
schedule is `concurrencyPolicy: Forbid`, a CR stuck `Running` **blocks every subsequent
scheduled kopiur backup for that claim**. In this run kopiur detected the missing staging
PVC and moved the CR to `Failed` within about four minutes, immediately starting the
replacement run that succeeded - so no intervention was needed. Do not assume the 48-hour
deadline is the clearing time, and do check the phase rather than waiting.

## Cause

Still unknown, and deliberately not investigated - the task was replacement, not diagnosis.
Two hypotheses had already been disproved before this work (volume expansion; the restore
path, since `ai/repo-wiki` was created in the same restore event on the same storage class
and backs up fine). The corrupt image was destroyed rather than retained, so it is no
longer available for forensics. Data points this run adds, should it recur:

- The last clone that mounted was VolSync's ceph run at **2026-08-30T20:45Z**; the first
  failure followed within hours. The damage appeared in a live, mounted, lightly-used
  image, not at creation.
- The ext4 error is specifically the **resize inode** (inode 7), which only online resize
  touches - yet no PVC in the fleet shows a request/actual capacity mismatch.
- The live image read fine throughout: 4749 files hashed cleanly off it, and its restic
  backups were complete and restorable. Only *clones of its snapshots* were unmountable.
