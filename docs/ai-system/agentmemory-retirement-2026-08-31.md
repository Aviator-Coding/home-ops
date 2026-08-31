# agentmemory retirement - 2026-08-31

`agentmemory` (the shared in-cluster memory service, `kubernetes/apps/base/ai/agentmemory/`)
was retired from the cluster. Manifest-only retirement: Flux prunes every
Kubernetes object the app's Kustomization owned, including its 5Gi `ceph-block`
PVC (`reclaimPolicy: Delete` - the underlying Ceph RBD volume is destroyed, not
just the PVC object). What Flux never owned - the three restic repositories
(Ceph/MinIO/R2) and the kopiur `ceph` `ClusterRepository`'s snapshots - are
all outside this Kustomization's inventory, so none of them were touched. That
is the revival path.

## Why

Full evidence in `data/homeops-agentmemory-replacement-scout/report.md`
(captain's evaluation, 735 lines). Summary:

- **100% of routine traffic was a broken Gatus health check.** 1,440
  requests/day, one per minute, `GET /agentmemory` -> HTTP 404 every time (the
  route only serves `/agentmemory/<endpoint>`, and no
  `gatus.home-operations.com/endpoint` annotation overrode the auto-discovered
  check - broken since at least 2026-07-04). The only non-Gatus traffic in 14
  days was a single 7-hour window from the captain's workstation.
- **The declared in-cluster dependant did not use it.** `hermes` had
  `dependsOn: agentmemory` and `memory.provider: agentmemory` live, and wrote
  9 observations in 16 days.
- **It could not be stabilised at any memory limit.** The store is held by
  `iii` 0.11.2, a Rust runtime that loads the entire store into an in-process
  `HashMap` with no eviction. The cold-start resident floor is a fixed
  multiple of store size and rose 1,491 -> 2,206 MiB in 12 hours during the
  one real usage burst; extrapolated, the floor alone passes 8 GiB after
  roughly 12,000 more observations, at which point the pod cannot start
  regardless of limit. Under real write load it was OOM-killed every 37-56
  minutes.

## What was removed

- `kubernetes/apps/base/ai/agentmemory/` (HelmRelease, ExternalSecret,
  Kustomization) and the overlay `kubernetes/apps/main/ai/agentmemory.yaml`
  (Flux Kustomization - both VolSync and kopiur backup components, both
  engines' schedules).
- Its entry in `kubernetes/apps/main/ai/kustomization.yaml`.
- Its HTTPRoute (`agentmemory.${SECRET_DOMAIN}` on `envoy-internal`) - part of
  the deleted HelmRelease. This also removes the Gatus check: it was always
  auto-discovered from this route (`--auto-httproute`), never hand-written, so
  there is nothing else to delete for it to stop.
- The ToolHive `memory` `MCPServer` (`kubernetes/apps/base/ai/toolhive/mcp-servers/agentmemory-mcp/`)
  and its `mcp-tools` group membership - it was a stdio->HTTP proxy shim onto
  the now-deleted central service, with no independent state or measured tool
  traffic of its own.
- Hermes' `dependsOn: agentmemory` (`kubernetes/apps/main/ai/hermes.yaml`),
  its `agentmemory-plugin.yaml` memory-provider ConfigMap, the plugin volume
  mount and `AGENTMEMORY_URL`/`AGENTMEMORY_PROJECT` env in its HelmRelease,
  and the shared `AGENTMEMORY_SECRET` in its ExternalSecret.

`agentgateway`'s only "reference" was a comment listing agentmemory as one of
several apps sharing a 5m Kustomization timeout - not a functional dependency
(confirmed in the evaluation report, section 1.2).

## Hermes' post-removal memory behaviour

**Before this change, silently deleting only the `dependsOn` line would have
left Hermes silently degraded, not fixed.** The memory plugin's `is_available()`
only validates the configured URL's syntax - it never probes the service - so
Hermes would have reported the provider available while every call failed
closed: `_api()` catches the connection/DNS error and returns `None`, so
`system_prompt_block()` returns empty context and `handle_tool_call()` for
`memory_save` still returns `{"success": true}` even though nothing was
written. That is worse than a clean failure: an agent (and a human) would
believe memory was working.

Instead, Hermes' `memory.provider` was switched to `holographic`
(`kubernetes/apps/base/ai/hermes/app/resources/config.yaml`) - a local SQLite
fact store (FTS5 search, trust scoring, entity resolution, HRR-based
compositional retrieval) already shipped inside the Hermes image at
`/opt/hermes/plugins/memory/holographic`, confirmed present via
`kubectl -n ai exec deploy/hermes -c app -- ls /opt/hermes/plugins/memory/holographic`.
Requirements: none (SQLite is always available; NumPy is optional, only for
the HRR algebra). No new infrastructure, no shared network service, no
embeddings endpoint.

Consequence: Hermes gets `fact_store` / `fact_feedback` tools instead of
`memory_recall` / `memory_save`. Memory is now scoped to Hermes' own
`/opt/data/memory_store.db` on its existing backed-up PVC - it can no longer
be shared with workstation agents or the ToolHive `memory` MCP server the way
agentmemory was, but that sharing was never real usage (see "Why" above).

Hermes pod (`hermes-6d5c495c9f-75jm4`) was confirmed `2/2 Running`, `0`
restarts, before and after this change; the config change only takes effect
on the next pod restart (`copy-config` initContainer + `reloader` on the
ConfigMap), which is expected to happen automatically on this PR's merge.

## Data retained (revival path)

**PVC pruning is expected and matches this repo's established retirement
convention** (see `docs/ai-system/retired-2026-08-22.md`: "Flux prunes any
PVC its Kustomization created ... which is why the restic repos matter").
The store's live content was not deleted as part of this task - only the
Kubernetes objects Flux owns are removed by this PR; the PVC itself is
destroyed on merge+reconcile by Flux's `prune: true`, and that destruction is
the established, accepted mechanism for retiring an app in this repo, not a
new risk introduced here.

Four independent copies of the store survive outside Flux's ownership,
measured immediately before this change (2026-08-31, ~22:00 UTC - the store
had no writes since ~04:00 UTC the same day, so these are effectively final):

| Engine | Destination | Identifier | Captured | Size |
|---|---|---|---|---|
| kopiur | Ceph (`ClusterRepository/ceph`) | Snapshot CR `ai/agentmemory-ceph-20260831215039`, kopia snapshot ID `b383822fe09da5adeaf99997bc977845` (hostname `ai`, username `agentmemory-ceph`, source `/pvc/agentmemory`) | 2026-08-31T21:52:36Z | 716,155,105 bytes, 4,990 files |
| VolSync | Ceph | restic repo `s3:http://rook-ceph-rgw-ceph-objectstore.rook-ceph.svc:80/volsync/agentmemory` | lastSyncTime 2026-08-31T20:21:43Z | - |
| VolSync | MinIO | restic repo `s3:https://nas.sklab.dev:9000/volsync/agentmemory` | lastSyncTime 2026-08-31T22:21:44Z | - |
| VolSync | R2 | restic repo `s3:https://604128bd389364580b3208006c949072.r2.cloudflarestorage.com/volsync/agentmemory` | lastSyncTime 2026-08-31T06:40:44Z | - |

The kopiur Snapshot CR above is **not** deleted by this change - its
`SnapshotPolicy`/`SnapshotSchedule` are removed (pruned with this app's
Kustomization), but both are pinned `deletion.onPolicyDelete: Retain` /
`onScheduleDelete: Retain` in `kubernetes/components/kopiur/ceph/{snapshotpolicy,snapshotschedule}.yaml`,
so the orphaned Snapshot CR and the kopia snapshot it owns through its
finalizer are retained indefinitely. It was not deleted, is not scheduled for
deletion, and must never be deleted by hand (a kopiur `Snapshot` CR owns its
kopia snapshot through a finalizer - deleting the CR deletes the backup).

VolSync's three `ReplicationSource`s are pruned with the rest of the app, but
per this repo's established fact, deleting a `ReplicationSource` never
touches its restic repository - the three repositories above and their
retained snapshots survive untouched.

**To revive:** re-add the manifests this PR removes, then restore the PVC
through either engine's restore flow -
`kubernetes/components/volsync/Readme.md` ("Restore Operations") for VolSync,
or `docs/backups/kopiur-restore-drill-2026-08-30.md` /
`kubernetes/components/kopiur/Readme.md` for kopiur. `git revert` restores
manifests only, never PVC contents.

## Verified after removal

- Repo-wide `grep -rn agentmemory` (excluding `.git/`) after this change hits
  only: dated/historical decision records and incident logs that describe
  *past* state accurately (`docs/ai-gpu-changelog.md`,
  `docs/ai/b70-llm-serving-tuning.md`, `docs/ai/b70-second-card-decision.md`,
  `docs/hardware-incidents.md`,
  `docs/network/envoy-gateway-internal-domains-analysis-2026-07.md` - the last
  already carries its own "do not treat as current" caveat and is in fact the
  original 2026-07-04 record of the broken Gatus check this retirement makes
  moot; `docs/ai-system/retired-2026-08-22.md` also names agentmemory once, in
  its own Verification section, accurately describing the `ai` pod list as it
  stood on 2026-08-22); live-infrastructure comments explaining *why* a still-standing
  routing/exclusion decision was made, where agentmemory was the historical
  example that motivated it but the config itself serves a broader purpose
  (`kubernetes/apps/base/ai/agentgateway/app/{backends/vllm.yaml,httproute-unified.yaml,rules/cost.yaml}`,
  `kubernetes/apps/main/ai/agentgateway.yaml` (a comment naming agentmemory as
  one of several apps that share this Kustomization's 5m timeout value - never
  a functional dependency, confirmed in the evaluation report section 1.2),
  `kubernetes/apps/base/ai/litellm/app/httproute-internal.yaml`,
  `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`,
  `kubernetes/apps/base/network/envoy-gateway/app/prometheusrule.yaml`,
  `kubernetes/apps/base/system/volsync/app/prometheusrule.yaml`,
  `docs/ai-system/agentgateway/09-advanced-features.md`,
  `docs/ai-system/litellm/fallbacks.md`); one dated measurement in
  `kubernetes/components/volsync/Readme.md` ("29 dual-engine claims" at the
  2026-08-31 DST-timezone fix, now 28) describing a fixed historical count,
  not current state; this doc itself; and this change's own commit history.
  None of these assert agentmemory is still deployed.
- `kubectl -n ai get pod -l app.kubernetes.io/name=agentmemory` returns
  nothing once Flux reconciles this PR (pre-merge: still running, 8 OOM
  restarts, matching the evaluation report's model).
- The Gatus check and its 1,440 daily 404s stop once the HTTPRoute is pruned
  - there is no separate hand-written check to remove (confirmed: no
  `agentmemory` entry in `kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml`).
