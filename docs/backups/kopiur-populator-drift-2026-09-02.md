# kopiur standing-populator drift audit and closure - fleet-wide, 2026-09-02

> **Status: audit, sizing and evidence. Nothing was retired by this exercise.**
> No `ReplicationSource`, `Snapshot`, `SnapshotPolicy`, `SnapshotSchedule` or `ClusterRepository`
> was created, deleted, patched or suspended. All 26 dual-engine claims remain dual-engine and
> the four Stage 5 pilot volumes remain kopiur-only. The cluster-wide `Snapshot` census was
> **276 before and 276 after** every step below.

This closes the defect found by accident on 2026-09-02, immediately after PR #1543 merged:
`kubernetes/apps/main/ai/hermes.yaml` declared `KOPIUR_CACHE_CAPACITY: 16Gi` on `main` while
the live `Restore/hermes-kopiur-dst` still carried **`5Gi`** - the exact value the
[r2 restore cache gate](kopiur-r2-restore-cache-gate-2026-09-02.md) had just proven
*insufficient* to restore that claim.

Sibling documents:

- [`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md) -
  the cache-sizing model this task applies, and the document that predicted the two
  under-provisioned claims fixed here.
- [`kopiur-restore-drill-2026-08-30.md`](kopiur-restore-drill-2026-08-30.md) - the drill
  procedure the proof in [part 3](#part-3---the-r2-restore-proof) follows.
- [`kopiur-restore-proof-2026-09-01.md`](kopiur-restore-proof-2026-09-01.md) - the fleet
  restore proof, and the evidence standard this run is built to.

## Verdict

**Populator drift is now closed fleet-wide against what `main` declares: 0 of 30.**

The audit was not a clean negative. `ai/hermes` was **not** the only drifted populator - a
second, independently caused and materially worse one was found on `downloads/autobrr`, which
no one was looking for. Both are fixed and verified live.

| | |
|---|---|
| populators audited | **30 of 30** |
| drifted as found | **2** - `ai/hermes`, `downloads/autobrr` |
| drifted after this work, vs `main` | **0** |
| drifted after this work, vs this branch | 2 - `media/tdarr`, `downloads/radarr`, by construction; see [post-merge prerequisite](#post-merge-prerequisite) |
| claims resized in Git | 2 - `media/tdarr`, `downloads/radarr`, both 2Gi -> 10Gi |
| r2 restores proven | 1 - `media/tdarr` at 10Gi, content-verified |
| Snapshot CRs deleted | **0** |

The honest limit on that verdict is stated in full in the
[post-merge prerequisite](#post-merge-prerequisite): the two claims this PR resizes cannot be
made live before it merges, because Flux only reconciles `main`.

## The mechanism, restated

`kubernetes/components/kopiur/ceph/restore.yaml` carries
`kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`. That label is **load-bearing and correct** -
it is what stops Flux fighting the immutable `dataSourceRef` on a bound claim - but it means
Flux applies the object **once at creation and never reconciles it again**.

So the repository and the cluster can disagree permanently while `flux get ks` reports
`Ready: True` and `Applied revision: main` for the owning Kustomization. Every signal in the
cluster is green. Nothing in CI sees it: `flate` validates the built manifest, not what is
live.

Three template fields are exposed to this: `mover.cache.capacity`
(`${KOPIUR_CACHE_CAPACITY:-2Gi}`), `mover.podSecurityContext` (`${KOPIUR_PUID/PGID:-1000}`),
and any field *added to the template later* - which is how the second defect arose.

## Part 1 - the 30-row audit

Method: for each of the 30 kopiur-covered claims, the live `Restore` object's actual spec was
compared against the **resolved** value the overlay in Git produces - the overlay's own
`postBuild.substitute` map applied to the component template, not the raw `${VAR}` text. The
component defaults are `2Gi` and `1000:1000`.

`substituteFrom` was ruled out as a source: the `cluster-secrets` Secret was read directly and
carries exactly one key, `SECRET_DOMAIN`. Every `KOPIUR_*` value therefore comes from the
overlay's inline `substitute` map, and nowhere else.

Fields compared: `mover.cache.capacity`, `mover.cache.mode`, `mover.podSecurityContext`
(`runAsUser`/`runAsGroup`/`fsGroup`), `credentialProjection.enabled`,
`source.fromPolicy.name`, `source.fromPolicy.offset`, `policy.onMissingSnapshot`,
`repository.name`, and `target` mode.

The git and live sets matched exactly - 30 declared, 30 live, no object on one side only.

| # | claim | git | live **as found** | verdict as found |
|---:|---|--:|--:|---|
| 1 | `ai/hermes-kopiur-dst` | 16Gi | **5Gi** | **DRIFT - cache** |
| 2 | `ai/opencode-kopiur-dst` | 5Gi | 5Gi | agree |
| 3 | `ai/repo-wiki-kopiur-dst` | 2Gi | 2Gi | agree |
| 4 | `database/pgadmin-kopiur-dst` | 2Gi | 2Gi | agree |
| 5 | `downloads/autobrr-kopiur-dst` | 2Gi | 2Gi | **DRIFT - identity `2000:2000` vs live `1000:1000`, and `credentialProjection` absent** |
| 6 | `downloads/bazarr-kopiur-dst` | 2Gi | 2Gi | agree |
| 7 | `downloads/lidarr-kopiur-dst` | 2Gi | 2Gi | agree |
| 8 | `downloads/prowlarr-kopiur-dst` | 2Gi | 2Gi | agree |
| 9 | `downloads/radarr-kopiur-dst` | 2Gi | 2Gi | agree (under-provisioned - [resized here](#part-2---sizing)) |
| 10 | `downloads/readarr-kopiur-dst` | 2Gi | 2Gi | agree |
| 11 | `downloads/recyclarr-kopiur-dst` | 2Gi | 2Gi | agree |
| 12 | `downloads/sabnzbd-kopiur-dst` | 10Gi | 10Gi | agree |
| 13 | `downloads/sonarr-kopiur-dst` | 2Gi | 2Gi | agree |
| 14 | `home-automation/esphome-kopiur-dst` | 2Gi | 2Gi | agree |
| 15 | `home-automation/home-assistant-kopiur-dst` | 2Gi | 2Gi | agree |
| 16 | `home-automation/matter-server-kopiur-dst` | 2Gi | 2Gi | agree (root mover `0:0`, as declared) |
| 17 | `home-automation/zigbee2mqtt-kopiur-dst` | 2Gi | 2Gi | agree |
| 18 | `media/calibre-web-automated-kopiur-dst` | 2Gi | 2Gi | agree |
| 19 | `media/plex-kopiur-dst` | 10Gi | 10Gi | agree |
| 20 | `media/seerr-kopiur-dst` | 2Gi | 2Gi | agree |
| 21 | `media/tdarr-kopiur-dst` | 2Gi | 2Gi | agree (under-provisioned - [resized here](#part-2---sizing)) |
| 22 | `selfhosted/changedetection-kopiur-dst` | 2Gi | 2Gi | agree |
| 23 | `selfhosted/linkwarden-kopiur-dst` | 2Gi | 2Gi | agree |
| 24 | `selfhosted/n8n-kopiur-dst` | 2Gi | 2Gi | agree |
| 25 | `selfhosted/ntfy-kopiur-dst` | 2Gi | 2Gi | agree |
| 26 | `selfhosted/obsidian-livesync-kopiur-dst` | 2Gi | 2Gi | agree (`5984:5984`, CouchDB) |
| 27 | `selfhosted/paperless-ngx-kopiur-dst` | 2Gi | 2Gi | agree |
| 28 | `selfhosted/paperless-ngx-media-kopiur-dst` | 5Gi | 5Gi | agree |
| 29 | `selfhosted/syncthing-data-kopiur-dst` | 5Gi | 5Gi | agree |
| 30 | `selfhosted/syncthing-kopiur-dst` | 2Gi | 2Gi | agree |

Every other compared field agreed on all 30: `cache.mode: Ephemeral`,
`source.fromPolicy.offset: 0`, `policy.onMissingSnapshot: Continue`, `repository.name: ceph`,
`target.populator`, and `credentialProjection.enabled: true` on 29 of 30 - `autobrr` being the
exception below.

### The second defect: `downloads/autobrr`

This one matters more than the cache drift that started the task, and it was found only
because the audit compared **every** templated field rather than just the one already known
to be wrong.

`autobrr-kopiur-dst` was created `2026-08-30T18:50:01Z` - the **Stage 1 pilot**, the first
claim ever onboarded, and 2 days older than the other 29 (`2d15h` vs `2d6h` at audit time).
It was therefore created *before* two later changes to the component template, and
`IfNotPresent` meant neither ever reached it:

| field | git (`main`, unchanged for days) | live as found | consequence |
|---|---|---|---|
| `mover.podSecurityContext` | `2000:2000` | `1000:1000` | mover identity does not match the claim's files |
| `credentialProjection.enabled` | `true` | **absent** | mover cannot obtain repository credentials at all |

Either alone breaks the restore path; together they make it certain. `autobrr`'s overlay
records that its files are owned `2000:2000`, and per kopiur trap 0 a non-matching identity
**fails closed** - kopiur stages the source read-only and gets no `fsGroup` fixup. The missing
`credentialProjection` is the failure mode documented in trap 1: the CR reconciles perfectly
clean and the mover fails at run time, because no standing repository credential lives in an
app namespace any more.

This is not a new discovery so much as a confirmed one: the component's own
`ceph/restore.yaml` comment already named `downloads/autobrr` as "the remaining one" needing
recreation after `sabnzbd-kopiur-dst` was recreated during the Stage 5 pilot. The audit turned
that known TODO into a measured fact and closed it.

## Part 2 - sizing

Both claims flagged by the cache gate document were re-measured live rather than cited, using
each claim's **newest r2 snapshot** and its live populator capacity. Usable capacity is
request x 0.974, the ratio measured on the gate run's 16Gi volume.

| claim | populator cache | usable | newest r2 snapshot | ratio | action |
|---|--:|--:|--:|--:|---|
| `media/tdarr` | 2Gi | 1.95 GiB | 1.6956 GiB (1,820,653,922 B) | **87%** | -> **10Gi** |
| `downloads/radarr` | 2Gi | 1.95 GiB | 1.36 GiB | **70%** | -> **10Gi** |

The reasoning is the gate document's finding 1, and it is **a cliff, not a slope**. During a
restore the kopia cache grows ~1:1 with the bytes written into the target until it reaches
kopia's own internal budget - measured as a ~6.2 GiB plateau - and only then holds flat. So:

    required cache = min(snapshot sizeBytes, ~6.2 GiB)

While a claim's snapshot stays *under* its cache the limit is never reached and any value
works. The first time a growing volume crosses its capacity, the requirement does not rise
gently to 2.1 GiB - it jumps straight to the full plateau, and the restore fails
**terminally**. A failed kopiur `Restore` never retries; recovery means creating a new one.

**Be precise about what that means for these two claims today: 2Gi is not proven insufficient,
and almost certainly still works.** Both snapshots are still under their current usable cache,
so neither is failing now and no failure could be demonstrated without first growing the
volume. This change is **preventive**. Its value is that the failure it prevents is one that
could only ever be discovered *during an actual disaster*, on the one path left after the
local copy is gone. 10Gi (9.74 GiB usable) clears the ~6.2 GiB plateau outright, so neither
claim can reach the cliff again regardless of how it grows.

Cost is not a reason to under-size. `mode: Ephemeral` renders a **generic ephemeral PVC**, not
an emptyDir - directly observed during this run as
`tdarr-kopiur-drill-20260902-r2-z5psz-kopia-cache`, 10Gi, `ceph-block`, RWO, created with the
mover pod and deleted with it. It is thin-provisioned, so it costs what a run actually writes.

## Part 3 - the r2 restore proof

Subject: **`media/tdarr`**, the tighter of the two resized claims and the one closest to the
cliff. Destination: **r2**, the offsite repository - the one that matters for DR and the one
the cache gate is about. Cache capacity under test: **exactly the 10Gi this PR introduces**.

A matching **ceph** restore was run alongside it, to give the r2 tree an independent
comparison partner from a second repository.

Both used hand-written scratch `Restore` objects with `target.pvc` (which provisions a
brand-new PVC and **cannot address a live claim at all**), `source.fromPolicy` (which creates
no reference of any kind to a `Snapshot` CR), `policy.onMissingSnapshot: Fail` (fail-closed,
unlike the standing populator's `Continue`), and their own `credentialProjection.enabled: true`
- required on every hand-written `Restore`, because `IfNotPresent` on the component never
reaches an object created by hand.

### What each restore read

| | r2 | ceph |
|---|---|---|
| `Restore` | `tdarr-kopiur-drill-20260902-r2` | `tdarr-kopiur-drill-20260902-ceph` |
| resolved `kopiaSnapshotID` | `c359a17c3878fbd1ad45c687a0b0c75b` | `462da7aee2c9ef09a68367247c8109c4` |
| matching `Snapshot` CR | `tdarr-r2-20260901194208` | `tdarr-ceph-20260902090819` |
| snapshot taken | 2026-09-01T19:42:08Z | 2026-09-02T09:08:19Z |
| snapshot `filesNew` / `sizeBytes` | 17,281 / 1,820,653,922 | 17,281 / 1,821,117,163 |
| phase | `Completed` | `Completed` |

`offset: 0` resolving to the intended snapshot was confirmed by matching the restore's
`.status.resolved.kopiaSnapshotID` against the `Snapshot` CR's own `logTail`
(`Snapshot created: c359a17c…`), which gives `snapshotRef`'s precision without its coupling.

The r2 mover ran 10:52:17Z -> 10:58:07Z, **5m50s**, with no cache pressure of any kind.

### Content verification

Both restored volumes were mounted **read-only** in a single non-root pod (uid/gid 2000,
matching the app) and measured there. `LC_ALL=C` was exported for every sort and comparison.

| measure | r2 restore | ceph restore |
|---|--:|--:|
| files | **17,281** | **17,281** |
| directories (incl. root) | 6,775 | 6,775 |
| symlinks | 0 | 0 |
| total bytes | **1,820,653,922** | **1,821,117,163** |
| entries (`-mindepth 1`) | 24,055 | 24,055 |
| per-file sha256 tree digest | `7ef0f7a88358d74d855de09e921396111fea77fab4120203e92bed7893c0ce78` | `1a7216a2f12ba5b1ecea7c83612396b0ad29906c6578cf7697dc96ded69d059e` |
| mode+uid+gid manifest digest | `89fb74fe018c79d58a08a15583211a0d992df6815db7ed72d823e447ebd88732` | `43def21d70fdd012cdb23e6bc6e062743ff449ac2889350ab20720ab33f9731a` |

**The r2 restore reproduced its snapshot exactly**: 17,281 files against `filesNew` 17,281,
and 1,820,653,922 bytes against `sizeBytes` 1,820,653,922 - byte for byte, not approximately.
The ceph restore did the same against its own snapshot's 1,821,117,163.

### Cross-destination comparison

The two trees are restores of **different snapshots 13h26m apart**, so they are expected to
differ by whatever the app wrote in that window. Comparing them keyed on raw path bytes:

| | count | |
|---|--:|---|
| paths only in r2 | 1 | `./Tdarr/Backups/Backup-version-2.85.01-date-03-August-2026-…zip` |
| paths only in ceph | 1 | `./Tdarr/Backups/Backup-version-2.86.01-date-02-September-2026-…zip` |
| common paths, content differs | 3 | `./Tdarr/DB2/SQL/database.db`, `.db-shm`, `.db-wal` |
| common entries compared | 24,054 | |
| **mode / uid / gid differences** | **0** | |

**Every difference is explained and none indicates fidelity loss.** The three differing files
are Tdarr's live SQLite database and its WAL sidecars, which change continuously. The one
unique path each way is a single dated backup archive that Tdarr rotated between the two
snapshots - the filenames carry their own dates and are self-evidencing. **17,278 of 17,281
files are byte-identical across two independent repositories restored on different days.**

Zero mode or ownership differences across all 24,054 common entries confirms the read-only
staging property that makes a kopiur restore mode-faithful: 17,008 files at `0644`, 272 at
`0666`, 6,773 directories at `0775`, all owned `2000:2000`. The single non-`2000:2000` entry
is `./lost+found` (`0:2000`), created by `mkfs` on the fresh restore-target PVC - filesystem
provisioning, not restored content.

## Establishing that deleting a `Restore` is safe

The hard safety boundary on this task required establishing, from the CRD and the operator's
own behaviour, whether `Restore` owns kopia data through a finalizer - **not** inferring it
from the object's name or from what a sibling kind does. Four independent legs, in order of
strength:

**1. The CRD schema has no data-lifecycle surface for `Restore`.** A kind whose deletion can
destroy repository data needs an API to express that, and the two kinds that own data have
exactly that API:

| kind | `spec` keys governing repository data on delete |
|---|---|
| `Snapshot` | `deletionPolicy` (`Delete`/`Retain`/`Orphan`) - *"Lifecycle of the underlying kopia snapshot when its `Snapshot` CR is deleted"*; plus `onScheduleDelete` |
| `SnapshotPolicy` | `deletion.onPolicyDelete` (`Retain`/`Delete`) - *"Consulted by the Snapshot finalizer…"* |
| **`Restore`** | **none.** Full key set: `credentialProjection`, `failurePolicy`, `mover`, `options`, `policy`, `repository`, `source`, `target` |

There is no field by which deleting a `Restore` could express an effect on repository data,
because the kind has no such concept.

**2. Data flow is read-only.** A `Restore`'s `source` names a *policy* to read from and its
`target` names a PVC to write into. It has no field that names a snapshot to remove.

**3. Live finalizer census across every kopiur kind** - the data-owning kinds carry cleanup
finalizers and `Restore` carries none:

| kind | objects | carrying a finalizer |
|---|--:|---|
| `Snapshot` | 276 | **276** (`kopiur.home-operations.com/snapshot-cleanup`) |
| `SnapshotPolicy` | 60 | **60** (`kopiur.home-operations.com/policy-cleanup`) |
| `SnapshotSchedule` | 60 | 0 |
| `ClusterRepository` | 2 | 0 |
| **`Restore`** | **30** | **0** |

No `Restore` carries `ownerReferences` either, in any direction - so no GC cascade can reach
one, and none can cascade out of one.

**4. Operator behaviour, observed directly on an active restore.** Absence of a finalizer on
30 idle objects would still leave open the possibility that kopiur attaches one only while a
restore is running. It does not: the scratch `Restore` was inspected **mid-restore**, with its
mover pod `Running` and a kopia session open against r2, and carried
`finalizers=<none> ownerReferences=<none>`. Contrast `Snapshot`, which carries
`snapshot-cleanup` from creation.

**Empirical confirmation.** The cluster-wide `Snapshot` census was **276 before any deletion,
276 after deleting both stale populators, and 276 after deleting two completed scratch
`Restore` objects and their PVCs**. Both populator deletions returned immediately rather than
hanging in `Terminating`, which is itself the observable signature of having no finalizer.

Precedent: `sabnzbd-kopiur-dst` was recreated exactly this way during the Stage 5 pilot, and
the component's own `ceph/restore.yaml` comment documents delete-and-recreate as *the*
remedy for a stale standing `Restore`.

Nothing in this exercise deleted, patched or suspended a `Snapshot`, `SnapshotPolicy`,
`SnapshotSchedule`, `ClusterRepository` or `ReplicationSource`.

## What was changed live, and what merge still owes

Two populators were closed by **pure GitOps convergence** - `main` already declared the
correct values for both, so deleting the stale object and letting Flux recreate it introduced
no hand-written state at all:

| object | as found | after delete + `flux reconcile` | verified |
|---|---|---|---|
| `ai/hermes-kopiur-dst` | cache **5Gi** | cache **16Gi** | recreated `2026-09-02T10:51:02Z` |
| `downloads/autobrr-kopiur-dst` | `1000:1000`, no `credentialProjection` | **`2000:2000`**, `credentialProjection.enabled: true` | recreated `2026-09-02T10:51:05Z` |

Both were re-read after a further `flux reconcile` and still carry the intended values, with
`ssa: IfNotPresent` back in place. Both report
`Ready=False` / `AwaitingPvcDataSourceRef` - the **correct passive populator resting state**,
not a fault, and deliberately not "fixed".

### Post-merge prerequisite

`media/tdarr` and `downloads/radarr` **cannot be made live by this PR**, and this is a
property of GitOps rather than an omission. Flux reconciles `main`; the 10Gi values exist only
on this branch. Deleting those two populators now would simply have Flux recreate them at the
`2Gi` still declared on `main` - the same trap the hermes fix had to sequence around.

So, **after this merges**, one command closes it:

```bash
kubectl -n media      delete restore.kopiur.home-operations.com tdarr-kopiur-dst
kubectl -n downloads  delete restore.kopiur.home-operations.com radarr-kopiur-dst
flux reconcile ks tdarr  -n media
flux reconcile ks radarr -n downloads
# then verify - this is the check that caught the original defect:
kubectl -n media     get restore tdarr-kopiur-dst  -o jsonpath='{.spec.mover.cache.capacity}{"\n"}'  # 10Gi
kubectl -n downloads get restore radarr-kopiur-dst -o jsonpath='{.spec.mover.cache.capacity}{"\n"}'  # 10Gi
```

This is a **prerequisite for retiring either claim**, not tidiness: until it runs, a rebuilt
claim would bind a populator sized 2Gi.

## Part 4 - catching this class automatically

`ssa: IfNotPresent` is correct and must stay. The gap is not the label; it is that **nothing
compares the standing populators against Git**, and the defect was found by accident.

Two things make a narrow check unusually cheap here. The drift is only ever introduced at one
moment - when a `KOPIUR_*` variable changes, or a field is added to the component template -
and the resolved expected value is a pure function of the overlay's `substitute` map, which
CI can already read. Nothing needs cluster access to know what the value *should* be.

**Recommended, roughly 40 lines, not built here** (this task was asked to propose rather than
build): extend `scripts/ci/kopiur-stage3-test.py`, which already parses every overlay and
already pins `EXPECTED_IDENTITY` per claim. Add an assertion that any overlay changing a
`KOPIUR_*` value consumed by `components/kopiur/ceph/restore.yaml` also carries a
recreate note - or, more simply, emit the delete-and-reconcile commands for exactly the
changed claims as CI output on such a PR. That converts a silent divergence into a checklist
item on the PR that causes it, which is where it is cheapest to act on.

**Deliberately not proposed:** a general Git-versus-live drift detector. It needs cluster
credentials in CI, it duplicates what Flux does for every other object, and the blast radius
of the class is three fields on one template.

**Worth considering separately** (a component-wide change, out of scope here): the gate
document's observation that because the ~6.2 GiB plateau is a property of kopia rather than of
any claim, **a single component default of 10Gi would cover every claim in the fleet**,
present and foreseeable, at no real storage cost. That would remove the sizing half of this
problem permanently - though it would also require recreating all 30 populators once, which is
precisely why it is a captain decision and not a side effect of this task.

## Safety and cleanup evidence

| check | result |
|---|---|
| `Snapshot` census, before -> after | **276 -> 276** |
| `Snapshot` CRs deleted | 0 |
| `ReplicationSource` objects touched | 0 - and **78 remain**, which is exactly 26 dual-engine claims x 3 destinations, arithmetic confirmation that nothing was retired |
| `SnapshotPolicy` / `SnapshotSchedule` / `ClusterRepository` touched | 0 |
| live `media/tdarr-config` PVC uid | `e6451687-03c8-4bad-9a2c-53ad239101be`, unchanged |
| live `media/tdarr-config` PV | `pvc-90f58035-898f-4dd6-a9e5-07776f0b21c2`, unchanged, `Bound` |
| drill leftovers (`-l fm.homeops/restore-drill`) | none - `No resources found` |
| drill PVCs | deleted |
| `Snapshot` CRs left `Running` | **none** (275 `Succeeded`, 1 `Failed`) |
| schedules on all 4 touched claims | `Ready=True`, `ScheduleRunnable=True`, not suspended, not stalled |

The one `Failed` snapshot is `ai/opencode-ceph-20260831055425` from **2026-08-31**, two days
before this work and unrelated to it. That claim has produced four consecutive `Succeeded`
ceph snapshots since, the most recent at 2026-09-02T09:55:54Z, so it is a stale historical
record rather than an active fault.

`concurrencyPolicy: Forbid` was never engaged: this exercise created only `Restore` objects
and never a `Snapshot`, so no scheduled backup could have been blocked by it.

## A measurement trap found during this run

**busybox `find -exec … +` silently truncates, and exits 0 while doing it.** Building the
mode/ownership manifest inside the busybox verify pod, `find . -mindepth 1 -exec stat … {} +`
returned **33 of 24,055 entries** with no error and a zero exit status - which, taken at face
value, would have looked like a near-empty restored tree rather than a broken measurement.
busybox `xargs` separately lacks both `-a` and `-d`, and busybox `find` has no `-printf`.

Use `-exec … \;` (one fork per entry, space-safe, correct) and **always cross-check a
manifest's line count against an independently obtained `find | wc -l`** before drawing any
conclusion from it. Both manifests in this document were verified that way: 24,055 = 24,055.
