# kopiur Stage 5 wave two - VolSync retired from four more volumes - 2026-09-02

> ## ⚠️ LIVE VERIFICATION IS OUTSTANDING. NOTHING HERE HAS BEEN APPLIED TO THE CLUSTER.
>
> **Status: pure GitOps change; live execution and verification formally deferred to post-merge by
> captain decision (2026-09-02).** Unlike the
> [2026-09-01 pilot](kopiur-stage5-pilot-retirement-2026-09-01.md) - which suspended four
> Kustomizations and executed the removal by hand so it could be proven *before* merge - this
> change touches nothing on the cluster and is left for Flux to apply on merge.
>
> **Nothing is suspended and no manual step is needed to complete the retirement.** What is owed is
> the check, not an action: the numbers below have not been observed yet, only predicted from a
> baseline measured live at 2026-09-02T23:39Z.
>
> | after Flux reconciles | expected |
> |---|---|
> | all 4 PVCs `Bound`, data intact, **same `metadata.uid`** | `prowlarr-config`, `autobrr`, `ntfy`, `obsidian-livesync` |
> | VolSync `ReplicationSource` fleet-wide | **78 → 66** (4 claims × 3 destinations) |
> | VolSync `ReplicationDestination` fleet-wide | **30 → 26** |
> | kopiur `SnapshotPolicy` | **unchanged at 60** |
> | kopiur `SnapshotSchedule` / `Restore` | **unchanged at 60 / 30** |
>
> Full procedure, per-claim uids and per-app file counts:
> [Verification](#verification-what-must-be-checked-after-flux-reconciles). That section is the
> **gate, not a record**. A deviation on the first row means stop, not repair.

Eight of the fleet's 30 claims now have exactly ONE backup engine. This is the second wave, and
the first that includes content nobody can regenerate.

Authorising evidence: [`kopiur-wave-two-reproof-2026-09-02.md`](kopiur-wave-two-reproof-2026-09-02.md)
(merged as `ebc185e9`, PR #1546), whose part 4 gives a per-claim retirement verdict for six thin
claims and finds four ready. Captain decision 2026-09-02. That evidence was on `main` before
anything here was written - evidence that authorises an irreversible step has to be committed,
not pending.

## Bottom line

| | |
|---|---|
| Volumes retired here | 4 - `downloads/prowlarr-config`, `selfhosted/ntfy`, `downloads/autobrr`, `selfhosted/obsidian-livesync` |
| Volumes retired in total | **8 of 30** (4 pilot 2026-09-01 + these 4) |
| Volumes still dual-engine | **22** |
| VolSync objects removed | 12 `ReplicationSource`, 4 `ReplicationDestination`, 12 `ExternalSecret` (+ 12 `Secret` and 12 cache PVCs by cascade) |
| kopiur objects changed | **none** - the rendered `components/kopiur/backup` output is byte-identical for all four (proof below) |
| Backup data deleted | **none** - no `Snapshot` CR is touched and no restic repository is written to |
| Claims disturbed | none intended - the claim moves from `components/volsync` to `components/kopiur/pvc`, `ssa: IfNotPresent`, never applied to the existing bound claim |
| Restore-cache raises | **none** - all four sit far below the measured cliff; see [cache exposure](#restore-cache-exposure-and-why-nothing-was-raised) |

## The four volumes, and why each one

The wave-two report re-measured six thin claims and gave each a verdict. Four came back ready.
Two did not, and are deliberately **not** here.

### `downloads/prowlarr-config` - 713 files / 54.1 MiB, 5Gi claim, mover `3002:3000`

**Why it is safe to lose a second engine.** Prowlarr is an indexer proxy; the claim holds its
SQLite config, indexer definitions and cached Definitions. The indexers themselves are
re-addable and the *arr apps hold their own copies of what prowlarr syncs to them.

**Why it leads this wave.** It has the strongest proof of the six, and it was the last ambiguous
row in the fleet restore proof. The 2026-09-02 re-drill closed it from a matched
verification-snapshot pair (`prowlarr-{ceph,r2}-w2ver`, both 713 files / 56,777,475 bytes): both
restores returned 713 files / 56,777,475 bytes with **identical content digest**
(`576321fa…3578`) **and identical mode+uid+gid digest** (`31d7ffd5…9530`), zero stable-set gaps
and zero ceph-vs-r2 differences. Destination-identical in content *and* metadata is the highest
bar any claim in this fleet has cleared.

It is also the only claim in this wave with continuous write churn, which is what made the
earlier proof ambiguous. The re-drill handled it with a churn probe first, then both verification
snapshots taken back-to-back inside the gap between scheduled `ceph` bursts.

### `selfhosted/ntfy` - 2 files / 184 KiB, 2Gi claim, mover `1000:1000`

**Why it is safe.** Small, but the proof covers **100% of what the claim holds** - both files, a
`2700` `attachments` directory included - restored identically from both destinations in content
*and* mode/uid/gid. Live matches the snapshot exactly: `auth.db` (126,976 B under the `lib`
subPath) plus `cache.db` (61,440 B under `cache`) is 188,416 B, the snapshot's `sizeBytes` to the
byte. The backup is complete, not silently short.

`auth.db` is real non-regenerable state - ntfy's users and ACLs. That is precisely why
completeness of the proof matters here more than its size: there is no argument that "the
important part might be missing", because there is no part that is not covered.

**Cache safety is structural.** The claim is 2Gi against a 2Gi cache, so it cannot grow past
what a restore can hold without someone also resizing the claim.

### `downloads/autobrr` - 1 file / 2,179 B, 5Gi claim, mover `2000:2000`

**Why it is safe.** The claim holds `config.toml` and nothing else. autobrr's real application
state - filters, releases, indexer credentials - lives in the shared `postgres-17` CNPG cluster,
which is backed up independently and is entirely unaffected by retiring this claim. The overlay's
`dependsOn: postgres-cluster-17` is the visible form of that.

**Its place in the migration.** autobrr is where kopiur started: the Stage 1 pilot, the first
volume ever onboarded alongside VolSync on 2026-08-30. It is also the volume that taught the
fleet its first lesson - it held **zero** files at the time, so Stage 1's "Snapshot `Succeeded`"
gate was met by a backup that moved no data, and the fidelity subject had to be moved to
`sabnzbd-config` for Stage 2. A later write-access fix let autobrr create its `config.toml`, and
the wave-two proof restored that single file identically from both destinations.

### `selfhosted/obsidian-livesync` - 8 files / 561 KiB, 2Gi claim, mover `5984:5984`

**This one was escalated and decided deliberately, not swept in with the rest.**

The claim is a real Obsidian vault - a CouchDB database holding genuinely irreplaceable user
notes. The wave-two report flagged it as *"ready on the evidence"* but *"the same class of content
as the captain's `paperless-ngx` carve-out, so worth a deliberate decision rather than an
automatic one"*. Firstmate surfaced that before dispatching this work and **recommended keeping
the vault dual-engine**, on the same reasoning the captain used for `paperless-ngx`: one extra
backup run buys a second independent restore path on something that cannot be rebuilt.

**The captain said retire, and said it again after the objection.** That is recorded here as a
decision rather than inferred from the evidence. `selfhosted/paperless-ngx` remains permanently
dual-engine; that carve-out is unchanged.

**Proof quality.** 8 files / 561 KiB - 100% of the claim - restored identically from both
destinations in content and in mode/uid/gid, with metadata identical to live. Complete rather
than thin: the 8 files are a working CouchDB database (`_dbs.couch`, `_nodes.couch`, the shard
files), not a scaffold.

**Cache safety is structural**, exactly as for `ntfy`: 2Gi claim, 2Gi cache.

### What was deliberately NOT retired, and why

| excluded | reason |
|---|---|
| `selfhosted/paperless-ngx` | **Permanent captain carve-out.** Scanned documents; stays dual-engine indefinitely. Not reconsidered here. |
| `selfhosted/syncthing-data` | **Not ready.** Its 5 files / 531 B are a bare scaffold (4 `.stfolder` markers) - the proof covers nothing meaningful. A 15Gi claim behind a 4.87 GiB usable cache, which it would cross the first time it holds real synced content. |
| `selfhosted/paperless-ngx-media` | **Not ready.** 1 file / **0 bytes**, because paperless holds zero documents. This is the volume that will hold the irreplaceable scanned originals its sibling is protected for - a 50Gi claim behind a 4.87 GiB cache. |
| the other 22 dual-engine claims | Not assessed in wave two. Each needs its own restore proof and its own captain decision. |

## What retiring mechanically means

Unchanged from the pilot. Per volume:

| removed | how | count across these 4 |
|---|---|--:|
| `ReplicationSource` × 3 (ceph, minio, r2) | Flux prune, once the Component goes | 12 |
| `ReplicationDestination` `${APP}-dst` | same | 4 |
| `ExternalSecret` × 3 (`${APP}-volsync-{ceph,minio,r2}`) | same | 12 |
| `Secret` × 3 (`${APP}-volsync-*-secret`) | **cascade** - ESO `creationPolicy: Owner` sets an ownerReference | 12 |
| cache PVC × 3 (`volsync-src-${APP}-{ceph,minio,r2}-cache`, 2Gi each) | **cascade** - the `ReplicationSource` is their `controller: true` owner | 12 (24 GiB) |

The `volsync` (system) `dependsOn` also goes, because the Kustomization no longer renders a
VolSync object and would otherwise be waiting on an unrelated app.

**Deleting a `ReplicationSource` never touches the restic repository.** That is the property that
makes the VolSync side safe to remove, and it is the exact opposite of kopiur's, where a
`Snapshot` CR owns its kopia snapshot through a finalizer. No kopiur CR is deleted by this change.

### The claim is the whole hazard

`kubernetes/components/volsync/pvc.yaml` is the **only** manifest that emits an app's PVC, and
every app overlay runs `prune: true`. Removing that Component without a replacement takes the
claim out of the Flux inventory and Flux garbage-collects it - **deleting the app's data volume as
an ordinary prune**, for a change that was only ever meant to remove a backup engine.

`kubernetes/components/kopiur/pvc/` closes that, and each retired overlay lists both:

```yaml
  components:
    - ../../../../../components/kopiur
    - ../../../../../components/kopiur/pvc
```

### `dataSourceRef` is immutable - re-measured on all four claims

The pilot measured this once, on `sabnzbd-config`. It was re-measured here on each of the four
claims being retired, with a **server-side dry-run apply of exactly what
`components/kopiur/pvc` renders** - i.e. precisely the operation Flux would perform if the file
did not carry `ssa: IfNotPresent`:

```console
$ kustomize build kubernetes/components/kopiur/pvc | <substitute> \
    | kubectl apply -n downloads --server-side \
        --field-manager=kustomize-controller --dry-run=server -f -
The PersistentVolumeClaim "prowlarr-config" is invalid:
* spec: Invalid value: {}: must match dataSourceRef
* spec: Forbidden: spec is immutable after creation except resources.requests and
  volumeAttributesClassName for bound claims
```

Identical output for `downloads/autobrr`, `selfhosted/ntfy` and
`selfhosted/obsidian-livesync` (2026-09-02, read-only dry-run). So on all four:

- Flux **must** skip the apply. `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent` is what makes it
  do so, while keeping the object in the inventory so it is never pruned.
- **`kustomize.toolkit.fluxcd.io/force: enabled` must never appear on this file.** That label
  tells Flux to resolve an immutable-field conflict by deleting and recreating the object. Here
  the object is the data volume. `kopiur-stage2-test.py` and `kopiur-stage5-test.py` both assert
  its absence.

### The live claims keep an inert `dataSourceRef` - that is correct

All four still point at their now-deleted `${APP}-dst` `ReplicationDestination`:

| claim | live `dataSourceRef` |
|---|---|
| `downloads/prowlarr-config` | `ReplicationDestination/prowlarr-dst` |
| `downloads/autobrr` | `ReplicationDestination/autobrr-dst` |
| `selfhosted/ntfy` | `ReplicationDestination/ntfy-dst` |
| `selfhosted/obsidian-livesync` | `ReplicationDestination/obsidian-livesync-dst` |

`dataSourceRef` is consulted once, by the provisioner, and never again for a bound claim. Do not
try to clean it up: there is no manifest that both names the kopiur populator and applies cleanly
to an existing claim. A **rebuilt** claim is created fresh from `components/kopiur/pvc` and is
seeded from kopiur.

## The kopiur half does not change at all

Measured rather than assumed. `components/kopiur/backup` was rendered under each app's substitute
map **before and after** this change and compared:

```
prowlarr             kopiur backup render identical: True
autobrr              kopiur backup render identical: True
ntfy                 kopiur backup render identical: True
obsidian-livesync    kopiur backup render identical: True
```

The only new substitute key is `KOPIUR_CAPACITY`, and `KOPIUR_CAPACITY` /
`KOPIUR_ACCESSMODES` / `KOPIUR_STORAGECLASS` are read by `components/kopiur/pvc/pvc.yaml` **only**
- nothing in `components/kopiur/backup` consumes them. So this change introduces **no populator
drift**: every `SnapshotPolicy`, `SnapshotSchedule` and standing `Restore` continues to match
what Git says, and none of them needs the delete-and-recreate that a changed `KOPIUR_*` value
would otherwise require (see
[`kopiur-populator-drift-2026-09-02.md`](kopiur-populator-drift-2026-09-02.md)).

## Capacity: stated, not defaulted

Each retired overlay states `KOPIUR_CAPACITY` explicitly, matched to the live claim:

| claim | live capacity | component default | stated |
|---|--:|--:|--:|
| `downloads/prowlarr-config` | 5Gi | 5Gi | `5Gi` |
| `downloads/autobrr` | 5Gi | 5Gi | `5Gi` |
| `selfhosted/ntfy` | **2Gi** | 5Gi | `2Gi` |
| `selfhosted/obsidian-livesync` | **2Gi** | 5Gi | `2Gi` |

The two 2Gi claims are why this is a hard requirement rather than a style note: leaving the key
out would silently provision a rebuilt claim at 2.5x the size of the one it replaced - and for
those two it would also **break their cache-safety argument**, which depends on the claim staying
no larger than its cache. `kopiur-stage5-test.py::capacity_is_stated_not_defaulted` enforces it
for every retired volume, so nobody has to remember which apps are the exceptions.

Under `ssa: IfNotPresent` this value is create-time-only for a claim that already exists. That is
exactly what makes a wrong one dangerous: it stays unexercised until the day someone rebuilds the
claim, which is the worst possible day to discover it.

## Restore-cache exposure, and why nothing was raised

The pilot raised `sabnzbd`'s cache from 2Gi to 10Gi on retirement. Nothing is raised here, and
that is a decision rather than an omission.

The [cache gate](kopiur-r2-restore-cache-gate-2026-09-02.md) model is: required cache =
`min(snapshot sizeBytes, ~6.2 GiB)`, it is a **cliff rather than a slope**, and a failed `Restore`
is terminal and never retries. Against ~1.95 GiB of usable cache on a 2Gi setting:

| claim | PVC | cache | now | headroom | verdict |
|---|--:|--:|--:|--:|---|
| `downloads/prowlarr-config` | 5Gi | 2Gi | 54.1 MiB | 2.7% used | exposed only above ~1.95 GiB - a 36x growth |
| `downloads/autobrr` | 5Gi | 2Gi | 2,179 B | ~0% | implausible for a single TOML file |
| `selfhosted/ntfy` | 2Gi | 2Gi | 184 KiB | ~0% | **structurally safe** - cache ≥ max possible claim content |
| `selfhosted/obsidian-livesync` | 2Gi | 2Gi | 561 KiB | ~0% | **structurally safe** - same |

sabnzbd was raised because it held 2.06 GiB against the same 2Gi - already **over** the usable
cache. None of these four is near that. Compare the two claims wave two rated *not ready*:
`paperless-ngx-media` (50Gi behind 4.87 GiB) and `syncthing-data` (15Gi behind 4.87 GiB) would
cross their caches the first time they are used as intended, which is a large part of why they
stay dual-engine.

**The residual exposure on the two `downloads` claims is real but unreached, and it is not closed
here.** The standing fleet-wide answer - a single 10Gi component default, which the ~6.2 GiB
plateau being a property of kopia rather than of any claim makes sufficient for every claim in the
fleet - remains an open captain decision recorded in
[`kopiur-populator-drift-2026-09-02.md`](kopiur-populator-drift-2026-09-02.md). It would require
recreating all 30 populators once, which is why it is not folded into a retirement PR.

`kopiur-stage5-test.py::restore_cache_matches_the_measured_gate` now asserts this in both
directions: a retired volume either carries the raise its own measurement asked for, or it sits at
the component default. A cache that quietly grows fails CI instead of passing silently.

## A latent bug this change exposed

`selfhosted/ntfy` and `selfhosted/obsidian-livesync` both had

```yaml
existingClaim: ${VOLSYNC_CLAIM:-*app}
```

in their HelmRelease. **That default is a dangling YAML alias, not an anchor reference.** kustomize
emits the token verbatim, so the moment `VOLSYNC_CLAIM` stopped being defined, Flux's envsubst
produced `existingClaim: *app` and `flate` failed the entire Kustomization:

```
✗ Kustomization selfhosted/ntfy  substitute: unmarshal doc: go-yaml load error
  in composer at L87.C32: unknown anchor 'app' referenced
```

It had never been exercised, because every one of these overlays always set `VOLSYNC_CLAIM`. Both
are renamed to `${KOPIUR_CLAIM:-<claim>}` with the default corrected to the literal claim name,
following the same rename `ai/repo-wiki` took in the 2026-09-01 pilot.

**Four more overlays still carry the identical shape** - `selfhosted/{linkwarden,n8n,paperless-ngx,syncthing}`
- and are left alone because they remain dual-engine, so their defaults stay unexercised. Whoever
retires any of them next will hit exactly this, and the fix is the same one line.

## CI gates updated

Five gates encode which claims have two engines and which have one. `flate` catches none of it.

| gate | change |
|---|---|
| `kopiur-stage3-test.py` | `RETIRED_CLAIMS` gains the four. **Asserted both ways** - a claim that goes single-engine without being listed fails, and a listed claim that still renders VolSync fails. This is the coherence proof for the whole change. |
| `kopiur-stage5-test.py` | `RETIRED` gains the four with their live capacities, so each rendered PVC is checked for `ssa: IfNotPresent`, absence of the `force` label, a `dataSourceRef` naming its kopiur `Restore`, and a matching capacity. The sabnzbd-only cache assertion becomes a two-way `RAISED_CACHE` table. |
| `kopiur-stage1-test.py` | autobrr was the Stage 1 pilot, so its dual-engine assertions **inverted** rather than disappeared. `components/volsync` itself must stay untouched (22 claims still use it), so it is still rendered and asserted - under a synthetic env, because the live pilot map no longer has the keys and would silently assert nothing. |
| `kopiur-timezone-test.py` | `MIN_DUAL_ENGINE_CLAIMS` 26 → 22. The timezone contract itself is still checked over all 30 kopiur claims, which is what keeps a retired claim's pin from becoming invisible once it has nothing to collide with. |
| `selfhosted-backup-identity-test.py` | `ntfy` and `obsidian-livesync` move to `RETIRED_APPS`. The obsidian `5984` pin moves to the **kopiur** mover, where it binds harder. |

That last one is worth stating plainly. The pin was written for a real latent defect: obsidian's
VolSync mover ran at the unmatched `1000:1000` component default and only *happened* to read the
volume because every file is mode 644/755. With VolSync gone the defect does not disappear, it
sharpens - **kopiur stages its source read-only, gets no kubelet `fsGroup` fixup, and fails the
backup CLOSED on the first unreadable file**, with no second engine left to mask a wrong identity.
The same reasoning applies to autobrr's `2000:2000`.

## Verification: what must be checked after Flux reconciles

Manifests rendering is not evidence that a retirement is safe. This is the gate, and it has
**not** been run - the change is committed for Flux to apply on merge, and live verification was
formally deferred to post-merge by captain decision on 2026-09-02, with firstmate owning the run.

Pre-change baseline, captured live 2026-09-02T23:39Z:

| measure | before |
|---|--:|
| `ReplicationSource` fleet-wide | **78** |
| `ReplicationDestination` fleet-wide | **30** |
| kopiur `Snapshot` fleet-wide | 334 |
| kopiur `SnapshotPolicy` / `SnapshotSchedule` / `Restore` | 60 / 60 / 30 |

Checks, in order of importance:

1. **All four PVCs still exist and are `Bound`, with the same `metadata.uid` and the same bound
   PV.** This is the check that matters more than every other one combined - a wrong retirement
   deletes the volume.

   ```bash
   kubectl get pvc -n downloads  prowlarr-config autobrr
   kubectl get pvc -n selfhosted ntfy obsidian-livesync
   ```

   Expected uids: `7eb53bd3-b2a3-49db-8690-c8d79b99ee79` (prowlarr-config),
   `897b4178-f856-4303-8692-c288f53b04a5` (autobrr),
   `66856a99-420c-45a2-8c79-d60bf7b1817a` (ntfy),
   `0eb569ae-ad00-4258-9e15-76b13736127d` (obsidian-livesync). A changed uid means the claim was
   recreated - **stop immediately**.

2. **VolSync `ReplicationSource` count is 78 − 12 = 66**, and `ReplicationDestination` 30 − 4 = 26.
   The 12 cache PVCs and 12 `Secret`s should be gone by cascade.

3. **kopiur is untouched**: `SnapshotPolicy` / `SnapshotSchedule` / `Restore` still 60 / 60 / 30,
   and each of the four claims still has both destinations.

4. **The `Snapshot` census moves only by ordinary scheduled activity.** A raw census count is not
   itself a safety invariant on this cluster - schedules and GFS retention move it continuously -
   so the invariant that carries meaning is that **every snapshot that disappeared is attributed**
   to a scheduled run or a named retention prune in the operator log.

5. **The apps still run and can read their data.** Measured live before the change, for comparison:

   | app | mount | files | note |
   |---|---|--:|---|
   | prowlarr | `/config` | 713 | matches the snapshot exactly |
   | autobrr | `/config` | 1 | `config.toml`, 2,179 B |
   | ntfy | `lib` + `cache` subPaths | 2 | 126,976 + 61,440 = 188,416 B = snapshot `sizeBytes` |
   | obsidian-livesync | `/opt/couchdb/data` | 8 | CouchDB database files |

6. **A scheduled kopiur backup fires and succeeds on both destinations after the change.** All
   eight latest snapshots were `Succeeded` with non-zero stats immediately beforehand:

   | claim | ceph | r2 |
   |---|---|---|
   | prowlarr-config | 713 files / 56,811,243 B | 713 / 56,777,475 |
   | autobrr | 1 / 2,179 | 1 / 2,179 |
   | ntfy | 2 / 188,416 | 2 / 188,416 |
   | obsidian-livesync | 8 / 574,930 | 8 / 574,930 |

**If anything about a claim looks wrong - not `Bound`, wrong size, wrong uid, app failing to
start - stop and report. Do not attempt a repair.**

## Rollback

Revert the four commits (one per app, deliberately, so a revert can be surgical). Flux recreates
that app's three `ReplicationSource`s, its `ReplicationDestination` and its three
`ExternalSecret`s on the next reconcile, and the claim reverts to being emitted by
`components/volsync/pvc.yaml` - which, being the same name with the same `dataSourceRef` the live
claim already carries, applies cleanly.

No restic repository content was deleted, so a reverted app resumes backing up into its existing
repositories rather than starting from scratch.
