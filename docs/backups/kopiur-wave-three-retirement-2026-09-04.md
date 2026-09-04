# kopiur Stage 5 wave three - VolSync retired from the last 19 eligible volumes - 2026-09-04

> ## ⚠️ LIVE VERIFICATION IS OUTSTANDING. NOTHING HERE HAS BEEN APPLIED TO THE CLUSTER.
>
> **Status: pure GitOps change; live execution and verification deferred to post-merge**, following
> the [wave-two precedent](kopiur-wave-two-retirement-2026-09-02.md) rather than the
> [2026-09-01 pilot](kopiur-stage5-pilot-retirement-2026-09-01.md), which suspended four
> Kustomizations and executed the removal by hand so it could be proven before merge.
>
> That choice is deliberate and is the one judgement call in this change that is not purely
> mechanical, so it is stated rather than assumed. The pilot's method does not scale to this wave:
> proving 19 retirements before merge means **19 suspended Flux Kustomizations across six
> namespaces**, held suspended until the PR lands, during which none of those apps reconciles from
> `main` - and the pilot's own banner records what happens if one is resumed early (the retirement
> is silently undone, with every Kustomization still reporting `Ready`). The captain already moved
> off that method for wave two at a fifth of this scale. **Nothing is suspended and no manual step
> is needed to complete the retirement.** What is owed is the check, not an action.
>
> | after Flux reconciles | expected |
> |---|---|
> | all 19 PVCs `Bound`, data intact, **same `metadata.uid` and same bound PV** | table in [Verification](#verification-what-must-be-checked-after-flux-reconciles) |
> | VolSync `ReplicationSource` fleet-wide | **66 → 9** (19 claims × 3 destinations) |
> | VolSync `ReplicationDestination` fleet-wide | **26 → 7** |
> | VolSync `ExternalSecret` (`*-volsync-*`) | **66 → 9**, and 66 → 9 cache PVCs by cascade |
> | kopiur `SnapshotPolicy` / `SnapshotSchedule` / `Restore` | **unchanged at 58 / 58 / 29** |
>
> That section is the **gate, not a record**. A deviation on the first row means stop, not repair.

This finishes the VolSync → kopiur migration. 27 of the fleet's 30 claims now have exactly one
backup engine; the three that do not are deliberate carve-outs, not leftovers.

Authorising evidence, all already on `main` before this was written - evidence that authorises an
irreversible step has to be committed, not pending:

- [`kopiur-restore-proof-2026-09-01.md`](kopiur-restore-proof-2026-09-01.md) - **every one of these
  19 claims is a destination-identical PASS row.** Not a status check: each row is a real restore
  into a fresh scratch PVC, compared by per-file sha256.
- [`kopiur-r2-restore-cache-gate-2026-09-02.md`](kopiur-r2-restore-cache-gate-2026-09-02.md) - the
  restore-cache sizing model, and the r2 proof that closed finding 2 for the large size class.
- [`kopiur-populator-drift-2026-09-02.md`](kopiur-populator-drift-2026-09-02.md) and
  [`kopiur-wave-two-reproof-2026-09-02.md`](kopiur-wave-two-reproof-2026-09-02.md) - the
  standing-populator audit that closed drift at 0 of 30 against `main`.

Captain decision 2026-09-04 ("finish the kopiur migration"), with the risk tiering below chosen so
that a surprise stops one tier rather than the fleet.

## Bottom line

| | |
|---|---|
| Volumes retired here | **19**, in three tiers, as three commits |
| Volumes retired in total | **27 of 30** (4 pilot + 4 wave two + 19 here; `autobrr` left the fleet with its app) |
| Volumes still dual-engine | **3** - `selfhosted/paperless-ngx`, `paperless-ngx-media`, `syncthing-data` |
| VolSync objects removed | 57 `ReplicationSource`, 19 `ReplicationDestination`, 57 `ExternalSecret` (+57 `Secret` and 57 cache PVCs by cascade) |
| kopiur objects changed | **none** - 0 added, 0 changed, 0 removed across the whole rendered tree |
| Claims removed from any Flux inventory | **0** - all 19 swap ownership to `components/kopiur/pvc` |
| Backup data deleted | **none** - no `Snapshot` CR is touched and no restic repository is written to |

## Prerequisites: confirmed, not re-derived

Spot-checked live at 2026-09-04T11:07Z before any edit.

| prerequisite | check | result |
|---|---|---|
| All 19 restore-proven on both destinations | rows 1,2,4,6,7,9,10,13,14,16,17,18,19,21,22,23,24,29 of the fleet proof (+ `home-assistant` row 15) | **PASS**, all destination-identical |
| Populator drift closed | live `mover.cache.capacity` and `credentialProjection` of all 19 `*-kopiur-dst` vs Git | **0 drift**; `credentialProjection.enabled: true` on all 19 |
| r2 restore-cache gate closed | live snapshot `sizeBytes` vs live cache, against `min(sizeBytes, ~6.2 GiB)` | **PASS**, table below |
| ceph index-blob compaction fixed | `parameters.epoch.minDuration` | `4h`, live, unchanged |
| Nothing mid-flight | kopiur `Snapshot` phases | 379 `Succeeded`, 14 `Discovered`, 1 `Failed` |

The single `Failed` snapshot is `ai/opencode-ceph-20260831055425` from 2026-08-31 - historical, not
blocking, already recorded in the cache-gate document, and `opencode-ceph` has taken successful
snapshots since (most recently 2026-09-04T09:57:56Z). **0 snapshots were `Running` or `Pending`**,
so nothing is blocking a later scheduled backup under `concurrencyPolicy: Forbid`.

## The 19, the tiers, and the restore-cache exposure

Snapshot sizes are the newest `ceph` snapshot at 2026-09-04T11:07Z. Usable cache is the request ×
0.974, the ratio measured on the 2026-09-02 gate run. Mover identity is read from the **live**
`SnapshotPolicy`, never inferred from a pod's `runAsUser`.

| tier | claim | claim | snapshot | files | cache | usable | snap/usable | mover |
|---|---|--:|--:|--:|--:|--:|--:|---|
| A | `database/pgadmin` | 2Gi | 0.93 GiB | 3 | 2Gi | 1.95 GiB | **48%** | `5050:5050` |
| A | `downloads/bazarr-config` | 5Gi | 0.12 GiB | 17 | 2Gi | 1.95 GiB | 6% | `2000:2000` |
| A | `downloads/lidarr-config` | 8Gi | 0.13 GiB | 482 | 2Gi | 1.95 GiB | 7% | `2000:2000` |
| A | `downloads/radarr-config` | 5Gi | 1.33 GiB | 6,086 | 10Gi | 9.74 GiB | 14% | `2000:2000` |
| A | `downloads/readarr-config` | 5Gi | 0.30 GiB | 4,204 | 2Gi | 1.95 GiB | 15% | `2000:2000` |
| A | `downloads/sonarr-config` | 5Gi | 0.06 GiB | 124 | 2Gi | 1.95 GiB | 3% | `2000:2000` |
| A | `home-automation/esphome-config` | 5Gi | 1.4 MiB | 46 | 2Gi | 1.95 GiB | 0.1% | `2000:2000` |
| A | `home-automation/matter-server` | 1Gi | 1.5 MiB | 161 | 2Gi | 1.95 GiB | 0.1% | `0:0` |
| A | `home-automation/zigbee2mqtt-data` | 5Gi | 824 KiB | 37 | 2Gi | 1.95 GiB | 0.04% | `2000:2000` |
| A | `media/tdarr-config` | 5Gi | 1.72 GiB | 17,364 | 10Gi | 9.74 GiB | 18% | `2000:2000` |
| A | `selfhosted/changedetection-config` | 1Gi | 0.04 GiB | 3,103 | 2Gi | 1.95 GiB | 2% | `1000:1000` |
| B | `ai/hermes` | 25Gi | 10.05 GiB | 66,206 | 16Gi | 15.58 GiB | **64%** | `10000:10000` |
| B | `ai/opencode` | 20Gi | 0.15 GiB | 4,749 | 5Gi | 4.87 GiB | 3% | `1000:1000` |
| B | `media/calibre-web-automated` | 20Gi | 0.29 GiB | 23 | 2Gi | 1.95 GiB | 15% | `2000:2000` |
| B | `media/plex` | 20Gi | 4.27 GiB | 21,669 | 10Gi | 9.74 GiB | **44%** | `2000:2000` |
| C | `home-automation/home-assistant` | 5Gi | 0.01 GiB | 79 | 2Gi | 1.95 GiB | 0.6% | `1000:1000` |
| C | `selfhosted/linkwarden` | 5Gi | 0.18 GiB | 67 | 2Gi | 1.95 GiB | 9% | `1000:1000` |
| C | `selfhosted/n8n` | 8Gi | 0.47 GiB | 7,725 | 2Gi | 1.95 GiB | 24% | `1000:1000` |
| C | `selfhosted/syncthing` | 1Gi | 2.0 MiB | 21 | 2Gi | 1.95 GiB | 0.1% | `1000:1000` |

**Nothing was raised, and nothing needed raising.** The highest ratio is `ai/hermes` at 64%, which
is the one value in the fleet that has been *proven* rather than modelled. The highest among the
2Gi-default claims is `database/pgadmin` at 48% - identical to the 48% the 2026-09-02 fleet audit
measured and adjudicated "ok", so it has not moved, and it is additionally bounded: a 2Gi claim
cannot grow far past a 2Gi cache. `media/tdarr` and `downloads/radarr` still carry the 10Gi they
were raised to on 2026-09-02, and `media/plex` and `ai/opencode` their existing 10Gi and 5Gi.

**Three claims sit at a cache that structurally cannot be outgrown** - `matter-server`,
`changedetection-config` and `syncthing`, all 1Gi claims against a 2Gi cache. That is the same
argument that authorised `ntfy` and `obsidian-livesync` in wave two, and the exact argument
`syncthing-data` fails.

### Tiering: what it is, and one place it is arguable

The tiering is a **sequencing device, not three evidence standards**. Every one of the 19 cleared
the same gate. It exists so that if something surprising happens the blast radius is one tier.

Reviewed against the restore-proof evidence, and two rows are worth naming:

- **`selfhosted/changedetection-config` is in tier A but reads as tier C by content.** Its watch
  definitions in `changedetection.json` are user-authored and not reconstructible. It stays in
  tier A because the risk is immaterial rather than because the content is: 1Gi claim, 38 MiB,
  byte-identical both-destination proof, and a cache that cannot be outgrown. Flagged rather than
  silently re-tiered.
- **`ai/opencode` and `media/calibre-web-automated` are in tier B by CLAIM size, not content** -
  0.15 GiB and 0.29 GiB respectively. Tier B is "the claims the r2 cache work was about", and a
  20Gi claim can grow into that category even when it has not yet.

Everything else lands where the evidence puts it. `media/tdarr` is the largest tier-A claim by file
count (17,364) but its hand-authored flows are versioned in `docs/tdarr/flow-nodes/`, so it is
genuinely reconstructible. `downloads/radarr-config` holds Radarr *database* state - the
movie-to-quality-profile assignment recyclarr never touches - which is why it is retired on a
byte-identical proof rather than on a regenerability argument.

## What was deliberately NOT retired, and why

Re-measured 2026-09-04; all three verdicts stand unchanged.

| claim | measured now | verdict |
|---|---|---|
| `selfhosted/paperless-ngx` | 32 files / 22,041,575 B | **permanent** captain carve-out. Irreplaceable scanned documents; stays dual-engine indefinitely. |
| `selfhosted/paperless-ngx-media` | 1 file / **0 B** | not ready. The claim is empty because paperless holds zero documents, so its proof is the sha256 of nothing - precisely the Stage 1 mistake. A 50Gi claim behind a 5Gi cache. |
| `selfhosted/syncthing-data` | 5 files / 531 B | not ready. A bare scaffold of `.stfolder` markers; 15Gi of intended capacity behind a 5Gi cache it would cross the first time it holds real data. |

**`selfhosted/syncthing` and `selfhosted/syncthing-data` are different claims** and share one
overlay file. The 1Gi config claim is retired here; the 15Gi data claim is not.
`kopiur-stage5-test.py` selects Flux Kustomizations by name rather than by position specifically so
these cannot be confused.

Neither of the two "not ready" claims is being argued for here. Both become eligible by the same
route: hold real data, take a proof against it, and confirm the cache clears
`min(sizeBytes, ~6.2 GiB)`.

## What retiring mechanically means

Per claim: drop `components/volsync`, drop the `volsync` (system) `dependsOn`, delete every
`VOLSYNC_*` substitute key, **add `components/kopiur/pvc`**, and state `KOPIUR_CAPACITY` at the
live claim size.

### The claim is the whole hazard

`components/volsync/pvc.yaml` is the **only** manifest that emits an app's PVC, and every app
overlay runs `prune: true`. Dropping the Component without a replacement takes the claim out of the
Flux inventory and Flux garbage-collects it - **deleting the app's data volume** for a change meant
only to remove a backup engine.

That is not a theoretical risk here; it is 19 volumes including a Home Assistant configuration and
an n8n credential-encryption key. It is why this change was verified by rendering the whole tree
before and after, and asserting on the object sets rather than on the diff:

```
FINAL vs main: 1998 objects before, 1865 after
  removed  133  =  57 ReplicationSource + 57 ExternalSecret + 19 ReplicationDestination
  added      0
  PersistentVolumeClaims removed from inventory:  0
  PersistentVolumeClaims present-and-changed:    19
  kopiur objects added / changed / removed:       0 / 0 / 0
  surviving ReplicationSource:                    9  (the three carve-outs × 3)
```

Every one of the 19 changed PVCs was checked individually: `ssa: IfNotPresent` present, the `force`
label **absent**, `dataSourceRef` repointed at that app's `Restore/${APP}-kopiur-dst`, and
`resources.requests.storage`, `storageClassName` and `accessModes` byte-identical to the values the
live claim carries.

### `ssa: IfNotPresent` is load-bearing, and `force` would delete the volume

A bound PVC's spec is immutable except `resources.requests` and `volumeAttributesClassName`.
`dataSourceRef` is not in that list, so applying the kopiur claim to an existing volsync-created
claim is rejected outright, and *omitting* the field is the same forbidden change because SSA would
remove it. `IfNotPresent` keeps the object in the inventory (never pruned) while skipping the apply.

`kustomize.toolkit.fluxcd.io/force: enabled` would "fix" that conflict by **deleting and recreating
the object** - here, the data volume. It is absent from `components/kopiur/pvc/pvc.yaml` and
`kopiur-stage5-test.py` asserts its absence on every retired claim.

**The live claims therefore keep an inert `dataSourceRef` pointing at a now-deleted
`ReplicationDestination`. That is correct**: `dataSourceRef` is consulted once, by the provisioner,
and never again for a bound claim. Do not clean it up.

### Two overlays carry a different shape, and that is deliberate

`database/pgadmin` and `media/calibre-web-automated` keep the kopiur **backup** half in its own
`wait: false` Kustomization and move only the **claim** onto the app's, so their `components:` list
is `[kopiur/pvc]` alone. That split was introduced when those claim Kustomizations still set
`wait: true` (Flux would have assessed the standing `Restore`, which is `Ready=False` for its whole
life by design - `AwaitingPvcDataSourceRef`). The claim side is now `wait: false` with explicit
workload `healthChecks` on the Deployment, so the split is **no longer required by wait:true** - it
is retained deliberately to keep this change minimal. Collapsing the kopiur half back inline is a
possible follow-up, not something this change does.

`kopiur-stage5-test.py` records that split explicitly in its `RETIRED` table (`backup_ks`) and
reads each half's substitute map from its own Kustomization, rather than special-casing it inside
the assertions. It also pins the greenfield contract below: claim `wait: false` + workload
`healthChecks`, and the backup Kustomization must **not** `dependsOn` the claim Kustomization.

### Greenfield split-shape deadlock (found in review)

On the live retirement path the Bound PVC is `IfNotPresent`-skipped and already Ready, so ordering
did not matter. On a greenfield apply or namespace rebuild the original shape was a hard deadlock:

```
*-kopiur dependsOn <app>
  -> <app> waits for Deployment Available
    -> Deployment mounts the populator-backed PVC
      -> PVC Bound waits for Restore / <app>-kopiur-dst
        -> Restore is created by *-kopiur
```

The cycle edge is the **dependsOn**, not `wait: true`. Both Deployments mount the claim
(`database/pgadmin` mounts PVC `pgadmin`; `media/calibre-web-automated` mounts PVC
`calibre-web-automated`), so a healthCheck on the Deployment is still transitively blocked by the
same Pending PVC. Moving the gate from `wait: true` to healthChecks-on-Deployment changes *which*
object blocks, not that it blocks - **part 1 alone would not have fixed the deadlock**.

What breaks the cycle is removing the reverse edge: `pgadmin-kopiur` no longer dependsOn `pgadmin`,
and `calibre-web-automated-kopiur` no longer dependsOn `calibre-web-automated`. Both keep
`dependsOn kopiur-repository` (the admission webhook is `failurePolicy: Fail`). The Restore can
then apply independently, the PVC binds, the Deployment starts, and the claim Kustomization goes
Ready.

Part 1 is kept on its own merits as a health-signal improvement, not as the deadlock fix:
`wait: true` makes Flux ignore `spec.healthChecks` entirely and assess the whole inventory instead
(AGENTS.md: healthChecks must target the workload, and wait must stay false). These two apps were
the fleet's exception to a rule this repo already documents.

Inverting the dependency (claim dependsOn its `*-kopiur` Kustomization) was **considered and
declined**: it would make a claim depend on its own backup configuration. Removing an edge adds no
dependency and preserves that property. Risk of applying the backup KS first: it renders only
SnapshotPolicy/SnapshotSchedule/Restore, so a policy may name a PVC that is not there yet and has
nothing to snapshot until the claim appears.

**The greenfield / DR apply path remains untested by this task** - see open follow-up 5. The fix is
reasoned from the dependency graph and the mount evidence, not demonstrated against a namespace
rebuild.

### `${VOLSYNC_CLAIM:-*app}` - the dangling-alias trap, defused

Wave two found and recorded this and left it live in four HelmReleases. `*app` in
`existingClaim: ${VOLSYNC_CLAIM:-*app}` is a **bare token, not a YAML anchor reference** - kustomize
emits it verbatim, so the moment the overlay stops defining `VOLSYNC_CLAIM` the render becomes
`existingClaim: *app` and `flate` fails the whole Kustomization with `unknown anchor 'app'
referenced`. Three of the four are retired here and are renamed to `${KOPIUR_CLAIM:-<claim>}` with
the default corrected to the literal name; the fourth, `paperless-ngx`, keeps `VOLSYNC_CLAIM`
because it stays dual-engine.

`selfhosted/changedetection` carried a **different and quieter** version of the same defect:
`existingClaim: "${VOLSYNC_CLAIM}"` with no `:-default` at all, which would have rendered an
**empty** claim name rather than failing loudly. `home-automation/{home-assistant,matter-server}`
and `ai/opencode` had safe defaults and are renamed for hygiene, not repair.

## The kopiur half does not change at all

Verified structurally rather than asserted: rendering the whole tree before and after finds **0
kopiur objects added, changed or removed**. Every `SnapshotPolicy`, `SnapshotSchedule` and standing
`Restore` in the fleet is byte-identical. Backups continue on exactly the schedules and identities
they already ran on.

## CI gates updated

| gate | change |
|---|---|
| `kopiur-stage3-test.py` | `RETIRED_CLAIMS` +19 (now 27), asserted **both ways** - in-set means kopiur-only, not-in-set means dual-engine |
| `kopiur-stage5-test.py` | `RETIRED` +19; table restructured to a `NamedTuple` keyed `<ns>/<app>` carrying the optional `backup_ks` split; `RAISED_CACHE` +hermes/plex/opencode; `EXPECTED_RETIRED_COUNT` 7 → 26; `overlay()` now selects a Kustomization **by name** |
| `kopiur-stage4-test.py` | matter-server's "keeps components/volsync" assertion **inverted** to "must not carry it, must carry kopiur/pvc". The root-mover pin the file exists for is untouched - retirement removes an engine, not an identity, and `0:0` matters *more* with no second engine to mask a mistake |
| `kopiur-timezone-test.py` | dual-engine floor 22 → 3. The timezone contract itself is checked over all kopiur claims and is unweakened; only the cross-engine collision assertions narrow, and a collision is only possible on a dual-engine claim |
| `selfhosted-backup-identity-test.py` | changedetection, n8n, linkwarden, syncthing move to `RETIRED_APPS`. **This was a real gap**: left in `DUAL_ENGINE_APPS` the test kept passing by rendering a VolSync mover for claims that have none |
| `syncthing-data-capacity-test.py` | the config claim's 1Gi assertion follows the size onto `KOPIUR_CAPACITY`, plus a new no-`VOLSYNC_*`-survived check |
| `corrupt-claim-recreation-contract-test.py` | empty-latestImage render repointed from `ai/opencode` onto `selfhosted/paperless-ngx`, so the trap is asserted against a claim that can still hit it; opencode's own pin inverts to require the retirement plus the recreated 20Gi surviving into `KOPIUR_CAPACITY` |

Mutation-tested, all three failing as intended: dropping `components/kopiur/pvc` from a retired
overlay, adding it to a still-dual-engine one, and stating a wrong `KOPIUR_CAPACITY`.

`flate` reports `302 passed` with the same 4 pre-existing chart-values warnings, unchanged from
`main`. Note that `flate` alone would **not** have caught a missing `components/kopiur/pvc`: the
render simply loses a PVC, which is not an error. The stage5 gate is what catches it.

## Verification: what must be checked after Flux reconciles

Manifests rendering is not evidence that a retirement is safe. This is the gate, and it has **not**
been run.

Pre-change baseline, captured live 2026-09-04T11:07Z:

| measure | before |
|---|--:|
| `ReplicationSource` fleet-wide | **66** |
| `ReplicationDestination` fleet-wide | **26** |
| `ExternalSecret` matching `*-volsync-*` | **66** |
| `volsync-src-*-cache` PVCs | **66** |
| kopiur `Snapshot` fleet-wide | 394 (379 Succeeded / 14 Discovered / 1 Failed) |
| kopiur `SnapshotPolicy` / `SnapshotSchedule` / `Restore` | 58 / 58 / 29 |

Checks, in order of importance:

1. **All 19 PVCs still exist, are `Bound`, and carry the SAME `metadata.uid` and the same bound
   PV.** This matters more than every other check combined - a wrong retirement deletes the volume.
   A changed uid means the claim was recreated: **stop immediately.**

   | tier | claim | expected `metadata.uid` | expected PV | size |
   |---|---|---|---|--:|
   | A | `database/pgadmin` | `a95443ef-bbde-4476-8095-4fb55d9afb64` | `pvc-94a9d0dc-05b9-439d-8a87-b0277f8b08bc` | 2Gi |
   | A | `downloads/bazarr-config` | `a3fc8b2d-4cd3-4aae-92a1-deb91c40cc10` | `pvc-2334815e-4f42-41ba-a60a-289bd45a18b2` | 5Gi |
   | A | `downloads/lidarr-config` | `a44df757-f4b7-4234-a4c3-177d0e81aecf` | `pvc-43e789e9-078b-4724-b016-41cbee017140` | 8Gi |
   | A | `downloads/radarr-config` | `b0103b28-1dd5-48e5-9484-092786859375` | `pvc-07ff0c57-2001-4ff0-8676-9a892fe024bc` | 5Gi |
   | A | `downloads/readarr-config` | `0377adb8-0df5-461e-ad2e-8d43e331d8ed` | `pvc-09cd4f73-3619-4c96-9434-fbcb9b15ab74` | 5Gi |
   | A | `downloads/sonarr-config` | `6f66bd8a-fe38-463b-82dd-51eb9eaeaa62` | `pvc-932c82f4-b018-44aa-b7e7-78e1a72f46ac` | 5Gi |
   | A | `home-automation/esphome-config` | `714336ea-b2b7-49a8-ba5f-9de3f6767f58` | `pvc-aa72edbd-4705-4342-82ed-8f7729a16d72` | 5Gi |
   | A | `home-automation/matter-server` | `50db13f3-8ae2-4e15-8700-795a5a31fb21` | `pvc-528a26f4-e510-4ac1-be84-110642d055bc` | 1Gi |
   | A | `home-automation/zigbee2mqtt-data` | `b95f4af3-e22e-4432-986a-0ceab8b3217d` | `pvc-416ecbc0-0097-4d72-8315-8cc43ab8d0af` | 5Gi |
   | A | `media/tdarr-config` | `e6451687-03c8-4bad-9a2c-53ad239101be` | `pvc-90f58035-898f-4dd6-a9e5-07776f0b21c2` | 5Gi |
   | A | `selfhosted/changedetection-config` | `806524ca-1a29-4440-a941-ffe127d574ee` | `pvc-2fec5d34-2ed3-47a0-b554-e8d43a8e21fb` | 1Gi |
   | B | `ai/hermes` | `bfe697fc-5bd7-4b83-8d47-36f6a4587794` | `pvc-7a69d4d8-4832-473d-b211-14872d8e4f85` | 25Gi |
   | B | `ai/opencode` | `37f07d0f-5476-48c3-bc05-b94859628431` | `pvc-a173b28b-2125-471b-b6bd-9d468600eaf2` | 20Gi |
   | B | `media/calibre-web-automated` | `47471f4c-61d1-4858-9653-3ac2bc3fc3c3` | `pvc-cd6288ea-e008-49d8-8828-ce0df5b045de` | 20Gi |
   | B | `media/plex` | `edec9098-5716-4e83-8f3c-3a02c778e4cc` | `pvc-f088c770-207a-4da3-8488-db49f1383ecb` | 20Gi |
   | C | `home-automation/home-assistant` | `6e96580b-7d0e-469a-8cc2-fa52ab410910` | `pvc-8e248137-ba3b-4da5-9b73-f4e05712fd2d` | 5Gi |
   | C | `selfhosted/linkwarden` | `b86e3101-78d5-4be5-af0e-b0f55304dfb4` | `pvc-44539791-46a9-4698-9386-5bd583fd27c5` | 5Gi |
   | C | `selfhosted/n8n` | `0fc44c82-a4b9-447d-9e69-af0c593a7e3c` | `pvc-2df7b6dd-382a-444b-9afe-e1d09d33ddc2` | 8Gi |
   | C | `selfhosted/syncthing` | `4b53387f-258f-4431-93f5-3bff770d18ee` | `pvc-ac7a230a-8267-4175-bc9c-24268c12fbd9` | 1Gi |

   ```bash
   kubectl get pvc -A -o custom-columns=\
   NS:.metadata.namespace,NAME:.metadata.name,PHASE:.status.phase,\
   CAP:.status.capacity.storage,UID:.metadata.uid,PV:.spec.volumeName
   ```

2. **`ReplicationSource` is 66 − 57 = 9, and `ReplicationDestination` 26 − 19 = 7.** The nine
   survivors must be exactly `paperless-ngx`, `paperless-ngx-media` and `syncthing-data`, three
   destinations each. The 57 cache PVCs and 57 `Secret`s go by cascade (the `ReplicationSource` is
   its cache PVC's `controller: true` owner; ESO's `creationPolicy: Owner` owns the target Secret),
   so no manual cleanup is needed.

   Note the `ReplicationDestination` count lands on 7 rather than 3: four orphans from
   already-removed apps (`ai/openclaw-dst`, `downloads/cross-seed-dst`,
   `downloads/qbittorrent-dst`, `media/calibre-web-dst`) predate this change and are untouched by
   it. They are worth cleaning up separately; they are not a symptom of this one.

3. **kopiur is untouched**: `SnapshotPolicy` / `SnapshotSchedule` / `Restore` still 58 / 58 / 29,
   and each retired claim still has both destinations.

4. **The `Snapshot` census moves only by ordinary scheduled activity.** A raw census count is not
   itself a safety invariant on this cluster - schedules and GFS retention move it continuously -
   so the invariant that carries meaning is that **every snapshot that disappeared is attributed**
   to a scheduled run or a named retention prune in the operator log.

5. **The apps still run and can read their data.** All 19 workloads were `Running` at baseline.
   Compare against the file counts in the exposure table above.

6. **A scheduled kopiur backup fires and succeeds on both destinations after the change.** All 38
   latest snapshots (19 claims × 2 destinations) were `Succeeded` with non-zero stats immediately
   beforehand.

**If anything about a claim looks wrong - not `Bound`, wrong size, wrong uid, app failing to start
- stop and report. Do not attempt a repair.**

## Open follow-ups this wave leaves

1. **`media/plex`'s 10Gi restore cache has never been r2-exercised.** It is predicted safe by the
   2026-09-02 measurement (4.27 GiB against 9.74 GiB usable, so the 1:1 regime tops out well below
   the limit), and the size class is proven at 16Gi on `ai/hermes` and at this exact 10Gi on
   `media/tdarr` - but plex itself has not been run. This is the strongest open item in the wave:
   it costs one ~7-minute drill restore, and plex is now single-engine.
2. **The standing fleet-wide 10Gi cache default remains an open captain decision.** Because the
   plateau is a property of kopia's budget rather than of the claim, a single 10Gi component
   default would cover every claim present and foreseeable, and would remove this class of problem
   permanently at no real storage cost (`mode: Ephemeral` is thin-provisioned and per-run). Raised
   on 2026-09-02, restated here, still not implemented.
3. **`database/pgadmin` is a permanent gap in `pvc-mover-readable-check`** - `pods/exec` is not
   bound in the `database` namespace by design. It is now single-engine, so nothing else watches
   that claim's mover readability either.
4. **Nine `scripts/ci/*-test.py` files carry a shebang without the exec bit.** Pre-commit's
   `check-shebang-scripts-are-executable` only sees changed files, so the mismatch is invisible
   until someone touches one - which is how it surfaced here. Only the one file this change touched
   was fixed; the rest are left for a dedicated cleanup rather than dragged into a backup PR.
5. **The greenfield / DR apply path for the two split-shape apps remains untested by this task.**
   Review found and fixed the Restore/PVC dependsOn cycle on `database/pgadmin` and
   `media/calibre-web-automated` (see "Greenfield split-shape deadlock" above), but nothing here
   exercised a namespace rebuild. The fix is reasoned from the dependency graph and the mount
   evidence, not demonstrated. A deliberate greenfield apply of those two overlays is still owed.

## Rollback

Revert one tier commit, or all three. Each is self-contained: the overlays, the CI gates that pin
them, and nothing else. Flux recreates that tier's `ReplicationSource`s, `ReplicationDestination`s
and `ExternalSecret`s on the next reconcile, and the claims revert to being emitted by
`components/volsync/pvc.yaml` - which, being the same name with the same `dataSourceRef` the live
claim already carries, applies cleanly.

Reverting tier C alone leaves tiers A and B green, and vice versa; the gates were updated per tier
for exactly that reason.

No restic repository content was deleted, so a reverted app resumes backing up into its existing
repositories rather than starting from scratch. **No kopiur `Snapshot` CR is touched by this
change or by reverting it**, which is the load-bearing asymmetry: deleting a `ReplicationSource`
never touches the restic repository, while deleting a kopiur `Snapshot` deletes its kopia data
through a finalizer.
