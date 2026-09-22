# comfyui retirement - 2026-09-15

`comfyui` (`kubernetes/apps/base/ai/comfyui/`) and its ToolHive MCP server
`comfyui-mcp` were removed from the cluster.

Captain decision, 2026-09-15: *"we are not using it remove it we can add it
later"*, with both follow-up branches explicitly confirmed - **delete the
volumes too**, and **remove `comfyui-mcp` along with it**.

Unlike the `agentmemory` retirement, this one is not effectively manifest-only:
it destroys 142Gi of provisioned storage. That is the captain-approved part.

## Why

- The HelmRelease had been `spec.suspend: true` at `replicas: 0` since
  2026-06-13 (PR #1380) - parked for 93 days, because ComfyUI and `vllm`
  compete for the single Arc Pro B70 and time-slicing collapses chat decode
  ~38x (`docs/ai/b70-llm-serving-tuning.md`).
- `comfyui-mcp` was nonetheless `Ready 1/1` for 92 days, registered in the
  `mcp-tools` group and pointed at `COMFYUI_HOST=comfyui.ai:8188` - a backend
  scaled to zero. It was an advertised agent tool that could not work.
- Four of the six kopiur schedules fired `KopiurBackupEmpty` continuously.
  Measured against live Alertmanager on 2026-09-14, those four
  (`comfyui-{input,custom-nodes}-{ceph,r2}`) were **every** firing
  `KopiurBackupEmpty` in the cluster, so this removal clears the alert
  entirely rather than just reducing it.
- Real usage of the volumes was negligible: models 268 MiB on a 100Gi claim,
  custom-nodes 64 MiB, input 52 MiB, user 112 MiB.

## What is removed, and what that destroys

Manifests:

- `kubernetes/apps/base/ai/comfyui/` (HelmRelease, the `comfyui-output` PVC,
  Kustomization).
- `kubernetes/apps/main/ai/comfyui.yaml` - **four** Flux Kustomizations: the
  app plus the three kopiur backup carve-outs (`comfyui-user-kopiur`,
  `comfyui-custom-nodes-kopiur`, `comfyui-input-kopiur`).
- `kubernetes/apps/base/ai/toolhive/mcp-servers/comfyui-mcp/` and its entry in
  that directory's `kustomization.yaml`, plus its two `healthChecks` entries
  in `kubernetes/apps/main/ai/toolhive.yaml` (7 active MCP servers -> 6).
- The `comfyui` entry in `kubernetes/apps/main/ai/kustomization.yaml`, and the
  namespace-wide `kopiur.home-operations.com/privileged-movers` annotation
  patch - see "Privileged-mover grant" below.
- The `comfyui` exclusion in the `GatusServiceDown` alert
  (`kubernetes/apps/base/monitoring/gatus/app/prometheusrule.yaml`).

  **Correction, 2026-09-22: this did not stop the check.** The claim above -
  that the Gatus endpoint would disappear once the HelmRelease and its
  HTTPRoute were pruned - was wrong. `Service/comfyui` and
  `HTTPRoute/comfyui` in the `ai` namespace survived the Helm
  uninstall/Flux prune as orphaned live objects (still carrying
  `meta.helm.sh/release-name: comfyui` but no owning `HelmRelease` or Helm
  release secret - verified live: `kubectl -n ai get helmrelease comfyui`
  returns `NotFound`, `kubectl -n ai get secrets -l owner=helm` has no
  comfyui entry). The HTTPRoute's `gatus.home-operations.com/endpoint`
  annotation is exactly what makes the gatus-sidecar rediscover it on every
  reconcile (confirmed in its logs: `updated endpoint ... namespace=ai
  name=comfyui url=https://comfyui.sklab.dev/`, most recently
  2026-09-20T14:03:16Z, well after this doc's original merge), so Gatus has
  kept polling `https://comfyui.sklab.dev/` every minute and getting `503`
  (`Service/comfyui` has no ready endpoints, since the Deployment is gone),
  and `GatusServiceDown{key="ai_comfyui"}` has been firing continuously
  since 2026-09-16.

  This was live cluster drift, not a Git problem - no manifest in this repo
  ever declared either orphaned object, so there was nothing to delete via a
  PR. **Fixed operationally, 2026-09-22**: verified live that neither object
  carried an `ownerReference`, a `HelmRelease`, a `Kustomization`, or a Helm
  release secret; had no backing pods or `Endpoints`; and no other
  `HTTPRoute` referenced `Service/comfyui` - then ran `kubectl -n ai delete
  httproute comfyui` and `kubectl -n ai delete service comfyui`. The
  gatus-sidecar logged `removed endpoint ... namespace=ai name=comfyui
  reason=deleted` within seconds, the `ai_comfyui` entry dropped out of
  Gatus's endpoint list, `gatus_results_endpoint_success{key="ai_comfyui"}`
  stopped being scraped, and both the Prometheus `GatusServiceDown` rule and
  the Alertmanager alert for `key=ai_comfyui` cleared on the next evaluation
  cycle - confirmed via the Prometheus rules API and the Alertmanager API,
  both showing zero comfyui alerts. No namespace, PVC, or Secret was
  touched.

Data destroyed by the Flux prune (verified live 2026-09-14):

| PVC | Size | StorageClass | Owner | Reclaim |
|---|---|---|---|---|
| `comfyui-models` | 100Gi | `ceph-block` | Helm | `Delete` |
| `comfyui-output` | 25Gi | `ceph-filesystem-rwx` | Flux inventory | `Delete` |
| `comfyui-custom-nodes` | 10Gi | `ceph-block` | Helm | `Delete` |
| `comfyui-input` | 5Gi | `ceph-block` | Helm | `Delete` |
| `comfyui-user` | 2Gi | `ceph-block` | Helm | `Delete` |

Both StorageClasses are `reclaimPolicy: Delete` and all five **PVs** carry
`persistentVolumeReclaimPolicy: Delete`, so the underlying Ceph RBD images and
the CephFS subvolume are destroyed, not released. None of the four Helm-owned
PVCs carries `helm.sh/resource-policy: keep`, so `helm uninstall` deletes them;
`comfyui-output` is in the `comfyui` Kustomization's own `status.inventory` and
is pruned directly. All four owning Kustomizations are `prune: true`.

**This is irreversible.**

## What survives

The kopia backup snapshots. `kubernetes/components/kopiur` pins
`deletion.onPolicyDelete: Retain` and `deletion.onScheduleDelete: Retain`, so
the Flux prune removes the `SnapshotPolicy`/`SnapshotSchedule`/`Restore` CRs
and Kubernetes GC cascade-deletes the 33 `Snapshot` CRs (8 ceph + 3 r2 for each
of `comfyui-user`, `comfyui-custom-nodes`, `comfyui-input`), while their kopia
snapshots stay in the ceph and r2 repositories and are rediscovered as
`origin: discovered`. **Expect the CR count to drop to zero; that is the
documented success path, not data loss** (`kubernetes/components/kopiur/Readme.md`).

Two consequences worth recording:

- `comfyui-user` held the captain-authored workflows and settings. After this
  merge those exist **only** in the retained kopia snapshots.
- GFS retention is driven by the `SnapshotPolicy`. With the policies gone
  nothing prunes these snapshots either, so they persist indefinitely until
  someone expires them deliberately - the same standing loose end as the
  retired VolSync restic repositories
  (`docs/backups/volsync-retired-repository-expiry.md`).

`comfyui-models` and `comfyui-output` were never onboarded to any backup
engine (captain decision 2026-09-12 scoped coverage to the three volumes
holding non-regenerable state), so their contents are gone outright.

## Privileged-mover grant

`kubernetes/apps/main/ai/kustomization.yaml` carried a namespace-wide
`kopiur.home-operations.com/privileged-movers: "true"` annotation patch, added
2026-09-12 solely so comfyui's three root-owned claims could run a root kopiur
mover. It is removed with them: no remaining `ai` claim requests a root mover
(`hermes` 10000, `opencode` and `repo-wiki` 1000), so the grant had no
consumer left, and a standing privilege with no consumer is a liability.

`home-automation` keeps its own identical annotation - that one is
matter-server's and is untouched. `scripts/ci/kopiur-stage4-test.py` pins only
the `home-automation` overlay, so it is unaffected.

## Shared infrastructure deliberately NOT changed

Files that merely *name* comfyui but whose objects other apps depend on:

- **`kubernetes/apps/base/rook-ceph/rook-ceph/cluster/cephfs-rwx-subvolumegroup.yaml`**
  - the `csi-rwx` subvolume group backs the whole `ceph-filesystem-rwx`
  StorageClass. The CR is untouched; only its inventory comment changed
  (4 RWX PVCs -> 3). Deleting this CR would break every RWX volume in the
  cluster.
- **`kubernetes/apps/base/system/generic-device-plugin/app/config/config.yaml`**
  - the `b70` device group is `vllm`/`vllm-embed`/`tdarr-node` infrastructure.
  Left **byte-identical on purpose**: this file is a `configMapGenerator`
  source with the name-suffix hash enabled, so even a comment-only edit
  changes the ConfigMap name and rolls the GPU device-plugin DaemonSet. Its
  two comfyui mentions are historical rationale and are not worth that churn.
- **`kubernetes/apps/base/system/intel-device-plugin-operator/gpu/helmrelease.yaml`**
  - `allowIDs: "0xa7a0"` scopes this pool to the Raptor Lake iGPU and is
  unrelated to comfyui. Comment only.
- **`kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`** - comfyui appeared
  only in vllm's own context-sizing rationale. Comment only.

The three comment-only edits above were verified to render byte-identically:
`kustomize build` output for each directory is unchanged before and after.

`kubernetes/apps/base/ai/hermes/` mentions ComfyUI twice as a prose example of
"something scaled vllm to 0"; the mechanism still applies, so those are left.

## CI gates that had to move with the app

Four `scripts/ci/*-test.py` gates hardcoded comfyui and would have gone red on
a manifest-only removal - the trap recorded in `AGENTS.md` ("Retiring an app
can break a `scripts/ci/*-test.py` gate that hardcodes its manifest path, and
`flate` will not warn you"):

- `igpu-xe-allowids-test.py` - `B70_CONSUMERS` mapped `comfyui` to its
  HelmRelease path; the test would have failed on a missing file.
- `kopiur-stage3-test.py` - three entries in `EXPECTED_IDENTITY` and three in
  `NEVER_VOLSYNC`.
- `grafana-mcp-deploy-test.py` - `comfyui-mcp` in `ACTIVE_SERVERS`, which is
  cross-checked against the rendered build *and* the toolhive `healthChecks`.
- `kopiur-stage4-test.py` - inspected and **not** changed: it pins the
  `home-automation` privileged-mover contract only.

`task flux:test:all` passes either way, because these tests are not part of it.

## Revival

Re-add the manifests. Model checkpoints are re-downloadable through the
ComfyUI UI/Manager; `comfyui-output` renders regenerate. The captain-authored
`comfyui-user` settings must be restored from the retained kopia snapshots -
restore them before the claim is recreated empty, since a fresh PVC will not
pull from the repository on its own.
