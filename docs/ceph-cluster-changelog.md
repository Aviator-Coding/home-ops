# Ceph Cluster Change Log

Track record of **deliberate configuration and topology changes** to the Rook-Ceph
cluster — what changed, why, the evidence behind it, and how to roll it back. Mirrors
[`hardware-incidents.md`](./hardware-incidents.md) but for *changes we make on purpose*
rather than hardware failures. The goal is that when something breaks we can answer
"what did we change recently, and why?" without spelunking git.

- **Hardware failures** → [`hardware-incidents.md`](./hardware-incidents.md)
- **The 2026-06-01 deep performance review & rationale** → [`ceph-performance-review.md`](./ceph-performance-review.md)
- **This file** → chronological log of applied changes + a verified backlog of proposed ones

> ⚠️ All Ceph config is GitOps. Changes go through
> `kubernetes/apps/rook-ceph/rook-ceph/cluster/helmrelease.yaml` (or the operator chart),
> **never** ad-hoc `ceph config set` — Rook reverts drift on reconcile. Record every change
> here when you merge it.

---

## Current baseline (2026-08-16, GitOps)

| Layer | Value |
|-------|-------|
| **Rook** | v1.20.6 (operator + cluster chart tags in `operator/ocirepository.yaml` and `cluster/ocirepository.yaml`; see 2026-08-23 CephX rotation entry) |
| **Ceph** | v20.2.4 **Tentacle** (GitOps `cephImage.tag`; RGW gateways rolled to it 2026-08-23T12:15:27Z, verified live 2026-08-23). Carries the CVE-2026-54330 SigV4 fix, which also broke every minio-go write until `rgw_sigv4_insecure` was set - **see the 2026-08-23 `rgw_sigv4_insecure` entry before touching RGW or bumping this tag**. Default `csi` subvolumegroup still broken; RWX stays on `ceph-filesystem-rwx` (see 2026-08-21 entry) |
| **Talos** | `talos/machineconfig.yaml.j2` installer pin reconciled to **v1.13.9**, matching tuppr `TalosUpgrade` **v1.13.9** (kernel has `CONFIG_CEPH_FS=y`, `CONFIG_BLK_DEV_RBD=y`, `CONFIG_CEPH_LIB=y` — CephFS + krbd **built-in**). Changelog does not claim a running version without live `kubectl get nodes`. |
| **Kubernetes** | `talos/machineconfig.yaml.j2` kubelet/control-plane images reconciled to **v1.36.3**, matching tuppr `KubernetesUpgrade` **v1.36.3**. Changelog does not claim a running version without live `kubectl version`. |
| **Cluster FSID** | `6562d9b0-883a-4e55-8b5d-899eaa7e0d10` |
| **Topology** | 3 nodes (talos-1/2/3), 6 OSDs, all NVMe, `failureDomain: host`, size=3 |
| **Network** | `provider: host`, `requireMsgr2: true`, no dedicated cluster/replication network |

### Key tunables in effect (and why)

| Setting | Value | Rationale |
|---------|-------|-----------|
| `osd_memory_target` | 10 GiB | read-cache headroom (pod limit 14Gi); replaced a hard `bluestore_cache_size=2GB` that disabled autotuning |
| `osd_mclock_profile` | `high_client_ops` | prioritise client IO over background work on latency-sensitive consumer NVMe |
| `osd_max_scrubs` / scrub window | `1` / 01:00–07:00 | gentle, off-peak deep-scrubs (was ~23 concurrent cluster-wide) |
| `osd_recovery_max_active` | `3` | don't swamp homelab hardware during recovery |
| `mds_cache_memory_limit` | 8 GiB | metadata-heavy CephFS ops (pod limit 10Gi) |
| compression (all pools) | `none` | data is already-compressed media + RBD; zstd burned CPU + write latency for ~nothing |
| `bulk: true` (block + cephfs-data0) | — | let the PG autoscaler provision a full PG complement and grow gradually |
| **CephFS mounter** | **kernel client** | ceph-csi-operator `Driver` CR `cephFsClientType: autodetect` → kernel on Talos 6.x (OperatorConfig default is `kernel`); verified live (`/proc/mounts` shows `type ceph`, msgr2/3300). The legacy chart `forceCephFSKernelClient` value is **inert** under Rook v1.20 csi-operator mode (dead line removed 2026-06-14) |
| `kernelMountOptions` | `ms_mode=prefer-crc` | set via `cephClusterSpec.csi.cephfs.kernelMountOptions` (CephCluster CR); makes the kernel client negotiate msgr2 (cluster has `requireMsgr2: true`) — **in effect now** (visible in `/proc/mounts`) |

### OSD / drive inventory

| OSD | Node | Model | Size | DRAM | PLP |
|-----|------|-------|------|------|-----|
| osd.0 | talos-1 | Samsung 980 PRO | 2 TB | yes | no |
| osd.3 | talos-1 | Lexar NM790 | 4 TB | **no (HMB)** | no |
| osd.6 | talos-2 | Samsung 990 PRO | 2 TB | yes | no |
| osd.5 | talos-2 | Lexar NM790 | 4 TB | **no (HMB)** | no |
| osd.2 | talos-3 | Samsung 980 PRO | 2 TB | yes | no |
| osd.4 | talos-3 | Lexar NM790 | 4 TB | **no (HMB)** | no |

**Mon stores** live on `openebs-hostpath` on each node's WD_BLACK SN770M system disk —
now on firmware **731150WD** (the 731100WD HMB bug that corrupted mon RocksDB is *resolved*;
see [hardware-incidents.md 2026-06-14](./hardware-incidents.md)). No drive has power-loss
protection; the three Lexar NM790 are DRAM-less and remain the fragile OSDs.

---

## How to add an entry

When you merge a Ceph config/topology change, prepend an entry (newest first):

```markdown
## [YYYY-MM-DD] Short title  (PR #NNN)

| Field | Value |
|-------|-------|
| **Change** | what was changed (file + key) |
| **Why** | trigger / goal |
| **Risk** | what could break, blast radius |
| **Rollback** | exact revert |
| **Verify** | command(s) proving it worked |

Notes / evidence / sources.
```

---

## Change log

### [2026-08-23] Set `rgw_sigv4_insecure` to unbreak S3 writes on Ceph v20.2.4  (PR pending, branch `fm/homeops-ceph-s3-write-outage`)

| Field | Value |
|-------|-------|
| **Change** | `cephClusterSpec.cephConfig.client.rgw.rgw_sigv4_insecure: "true"` in `cluster/helmrelease.yaml`. Config-only; RGW picks it up at runtime, **no daemon restart**, no image change. |
| **Why** | Ceph **v20.2.4** is the CVE-2026-54330 release. Its fix makes RGW's SigV4 verifier reject any request carrying a header that was sent but is absent from `SignedHeaders`. The CVE is about unsigned `x-amz-*` headers, but the fix **also required `content-type` to be signed**. minio-go's streaming signer always drops `content-type` from `SignedHeaders` (`pkg/signer/request-signature-streaming.go`, `ignoredStreamingHeaders`), which is the path every minio-go `PutObject` over plain HTTP takes - i.e. all of restic/VolSync. Live break **2026-08-23T12:15:27Z** when Rook rolled the RGW gateways from v20.2.3 to v20.2.4: every S3 PUT returned 403 while GET/HEAD kept working, and 30 of 33 `*-ceph` ReplicationSources failed on `client.PutObject: Access Denied` at lock creation. Ceph stayed `HEALTH_OK` throughout - this was never a cluster-health problem. |
| **Evidence** | `debug_rgw 20` on a gateway caught the exact denial: `Signature rejected: 'content-type' supplied but not in CanonicalHeaders.` The failing requests signed `content-md5;host;x-amz-content-sha256;x-amz-date;x-amz-decoded-content-length` - every `x-amz-*` header **was** signed, so they are provably not the CVE's attack class. Four hand-signed probes with the volsync OBC key isolated it: no content-type -> 200, content-type **unsigned** -> **403**, content-type **signed** -> 200, unsigned `x-amz-` header -> 403. |
| **Risk** | The flag gates the **whole** check block (`host`, `content-type`, and `x-amz-*`), so unsigned `x-amz-*` headers are accepted again and **CVE-2026-54330 is reopened while this is set**. Verified live: after the change the unsigned-`x-amz-` probe went 403 -> 200. Bounded by the S3 endpoint being on `envoy-internal` only (`s3.${SECRET_DOMAIN}`, LAN, no public route) and by no presigned URLs being issued. v20.2.4 ships **no** knob that relaxes only the content-type check, and rollback to v20.2.3 was ruled out, so this is the sole forward option. |
| **Rollback** | Remove the key (or set `"false"`). That restores full CVE-2026-54330 enforcement and immediately re-breaks every minio-go write, so only do it together with the image bump below. |
| **Verify** | `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph config dump \| grep sigv4` shows `client.rgw rgw_sigv4_insecure true`; RGW verb/status sample shows `PUT 200` and zero `PUT 403`; a manually triggered ReplicationSource completes (`status.lastManualSync` matches the token, `Synchronizing=False`). |

**Upstream / removal trigger.** Upstream agrees the content-type requirement was wrong and reverted it on `main` in [ceph/ceph#71192](https://github.com/ceph/ceph/pull/71192) ("Real S3 accepts requests where content-type wasnt signed, and SDKs rely on that... Rejecting these requests broke Mimir, Loki, Thanos and basically everything else built on thanos-io/objstore"), keeping the `host` + `x-amz-*` checks that are the actual CVE mitigation. #71192 merged to `main` **2026-08-21**, after the v20.2.4 build (`v20.2.4-20260818`), and had **no Tentacle backport** as of 2026-08-23 - `v20.2.4` was still the newest tag on quay.io, so no fixed forward image existed.

> **When a Tentacle release carrying #71192 ships (expect v20.2.5):** bump `cephImage.tag` to it **and** set `rgw_sigv4_insecure` back to `"false"` (or delete the key) in the same change, then re-verify that a PUT with an unsigned `content-type` still returns 200 and that an unsigned `x-amz-` header returns 403 again. Leaving the flag set after the image is fixed silently keeps the CVE reopened for no benefit.

**Renovate will not do that for you, and can actively hide it.** `cephImage.tag` carries a `# renovate: datasource=docker depName=quay.io/ceph/ceph` comment, but `quay.io/ceph/ceph` is **not** matched by the Rook-Ceph group in `.renovate/groups.json5` (that group matches the `rook-ceph` / `rook-ceph-cluster` *charts*). It therefore falls through to the generic rule in `.renovate/autoMerge.json5`, where a **patch** bump auto-merges with `automergeType: "branch"` - straight to the branch, **no PR to review**. So v20.2.4 -> v20.2.5 can land unattended, upgrading Ceph while leaving `rgw_sigv4_insecure: "true"` in place. Two follow-ups, neither done here:
>
> 1. Watch for the Tentacle backport of #71192 and pair the image bump with removing the flag, as above.
> 2. Consider a Renovate guard for `quay.io/ceph/ceph` (require a PR / dashboard approval rather than branch auto-merge), so a Ceph daemon version change is never unattended. **Note** that editing `.renovaterc.json5` or `.renovate/**.json5` fires `.github/workflows/renovate.yaml`'s `push` trigger as a live non-dry run - follow the disable/merge/dry-run/re-enable procedure in the root `AGENTS.md` before merging any such change.

### [2026-08-23] Rotate daemon CephX keys onto `aes256k`  (PR #1398 - Rook v1.20.6 live)

| Field | Value |
|-------|-------|
| **Change** | New `cephClusterSpec.security.cephx.daemon` block in `cluster/helmrelease.yaml`: `keyType: aes256k`, `keyRotationPolicy: KeyGeneration`, `keyGeneration: 2`. `security.cephx.csi` is deliberately **not** set (stays at the chart default `aes`). |
| **Why** | Ceph v20.2.4 (merged in #1392) raises `AUTH_INSECURE_SERVICE_KEY_TYPE` and `AUTH_INSECURE_SERVICE_TICKETS` - both **ERR** level, CVE-2025-30156 - for CephX keys still on the legacy `aes` cipher. All daemon keys are also still on their original generation 1, most minted under `keyCephVersion: 19.2.3-0` and never rotated. |
| **Risk** | Rook rotates daemon keys with a gradual rolling restart of mon/mgr/osd/crash-collector/ceph-exporter. Upstream notes daemons keep the old key internally for **2-3 hours** while service tokens roll - that window is expected, not a failure. Because OSDs restart, the pre-reboot Ceph checklist in the root `CLAUDE.md` applies: confirm `HEALTH_OK` and run `task rook:check-osd-device-paths` before merging (Rook #17224 device-path drift). `osdMaxUpdatesInParallel: 1` already caps OSD churn at one at a time. **Client keys are untouched**: `csi-*` keys are consumed by the krbd / CephFS *kernel* clients and `aes256k` needs Linux >= 7.0, while Talos ships 6.18.44 - rotating them would break every RBD map and CephFS mount. |
| **Prerequisite** | **Rook >= 1.20.5** - the `aes256k` `keyType` enum does not exist in the v1.20.4 CRD (verified: `deploy/examples/crds.yaml` at v1.20.4 has zero `aes256k` matches, v1.20.5 has six). **Satisfied**: The cluster was upgraded to Rook v1.20.6 via PR #1397, providing the required CRD support for `aes256k`. |
| **Rollback** | Remove the `security` block. Note this does **not** un-rotate: `keyGeneration` is validated `self >= oldSelf` by the CRD and cannot be decreased, and keys already moved to `aes256k` stay there. Rollback only stops *further* rotation. |
| **Verify** | Post-merge: `kubectl -n rook-ceph get cephcluster rook-ceph -o jsonpath='{.status.cephx}'` shows `keyGeneration: 2` for the daemon entries; `ceph health detail` no longer lists `AUTH_INSECURE_SERVICE_KEY_TYPE` / `AUTH_INSECURE_SERVICE_TICKETS`; cluster drops `HEALTH_ERR` -> `HEALTH_WARN` (the residual WARN is the client keys still on `aes`). Pre-merge, render-only: `kustomize build kubernetes/apps/rook-ceph/rook-ceph/cluster --load-restrictor LoadRestrictionsNone`, `task flux:test:all`, and `helm template` of the v1.20.6 cluster chart all render the block (confirmed 2026-08-23). |

Future rotations reuse the same block: bump `keyGeneration` to current+1. Rotating the CSI keys *within* the `aes` type (same policy, separate `security.cephx.csi.keyGeneration`, with `keepPriorKeyCountMax` for a soft cutover) is a separate follow-up; moving them to `aes256k` waits on a Talos release with kernel >= 7.0, which as of 2026-08-23 does not exist even in pre-release.

### [2026-08-22] Remove Ceph mgr dashboard - close security-audit findings F2/F3  (commits `b91ae846`, `33725557`)

| Field | Value |
|-------|-------|
| **Change** | `cluster/helmrelease.yaml`: `dashboard.enabled` `true` → `false`; removed the now-unused `urlPrefix`/`ssl`/`prometheusEndpoint` dashboard keys and the `mgr/dashboard/server_addr`/`mgr/dashboard/server_port` `cephConfig` lines (`mgr/crash/warn_recent_interval` untouched); `mgr.modules` `insights` entry `enabled: true` → `enabled: false` (had no consumer left once the dashboard was gone). `cluster/httproute.yaml`: removed the `rook-ceph-dashboard` HTTPRoute (`rook.${SECRET_DOMAIN}` → `rook-ceph-mgr-dashboard:7000`); `rook-ceph-s3` HTTPRoute unchanged. `operator/externalsecret.yaml` deleted (synced `rook-ceph-dashboard-password` from 1Password, only consumer was the dashboard) and dropped from `operator/kustomization.yaml`. |
| **Why** | Closes security-audit findings F2/F3. Captain decision 2026-08-23: remove rather than partially mitigate - Grafana/VictoriaMetrics already cover observability, so the dashboard added no capability worth the credential-storage exposure behind F2/F3. `rook` mgr module (disabled above for the crash-storm bug) and all osd/mds/rgw config are untouched by this change. |
| **Risk** | GitOps-only; does not touch credentials already stored in the mon config store (e.g. `MULTICLUSTER_CONFIG` token, `RGW_API_*` keys) - that live cleanup happens separately after this merges and Flux reconciles. No other manifest references the removed route/secret (grepped clean). |
| **Rollback** | Restore `dashboard.enabled: true` plus the removed `urlPrefix`/`ssl`/`prometheusEndpoint` keys, the `mgr/dashboard/server_addr`/`server_port` `cephConfig` lines, and `insights` `enabled: true`; restore the `rook-ceph-dashboard` HTTPRoute in `cluster/httproute.yaml`; restore `operator/externalsecret.yaml` and its `operator/kustomization.yaml` reference. |
| **Verify** | `kustomize build kubernetes/apps/rook-ceph/rook-ceph/cluster --load-restrictor LoadRestrictionsNone` and `.../operator` both build; `task flux:test:all` clean (217 passed, confirmed 2026-08-22). |

The matching 1Password item (`rook-ceph`, `ROOK_DASHBOARD_PASSWORD`) lives in the captain's external vault - delete it manually once this merges; not automated here. Applied via GitOps only; no live `ceph`/`kubectl` mutation was made.

### [2026-08-22] OSD Guaranteed QoS  (commit `cc3c135a`)

| Field | Value |
|-------|-------|
| **Change** | OSD pod `resources.requests.memory` in `cluster/helmrelease.yaml` raised `6Gi` -> `14Gi` to match `limits.memory` (Guaranteed QoS); `cpu` unchanged. |
| **Why** | Deferred at the 2026-07-03 mon/logcollector Guaranteed-QoS change (below) pending the talos-1 RAM RMA - 2 OSDs x 14Gi reserved (28Gi) did not fit talos-1's single-stick ~47Gi window. RMA closed 2026-08-21, all 3 nodes confirmed 96GB/~94GiB usable: 6 OSD pods x 14Gi = 84Gi cluster-wide, 2 OSDs/node x 14Gi = 28Gi/node reserved, within the ~94GiB/node headroom. |
| **Risk** | Reserves 28Gi/node up front (vs the prior 12Gi) even while OSDs are idle; `osd_memory_target` (10Gi) already sits under the 14Gi limit so no behavior change is expected, but this is a live production mutation - Flux reconciling it rolls all 6 OSD pods one at a time (`osdMaxUpdatesInParallel: 1`). |
| **Rollback** | revert `resources.osd.requests.memory` to `6Gi` in `cluster/helmrelease.yaml`. |
| **Verify** | OSD pods show `request==limit` (14Gi), QoS class `Guaranteed`; `ceph status` returns to `HEALTH_OK` after the rolling restart. |

MDS stays Burstable - still blocked on an `mds_cache_memory_limit` rethink (unresolved, see [hardware-incidents.md](./hardware-incidents.md)).

### [2026-08-22] Extend CephCrashesDetected alert to match RECENT_MGR_MODULE_CRASH  (commit `422d69c9`)

| Field | Value |
|-------|-------|
| **Change** | `CephCrashesDetected` alert `expr` in `cluster/prometheusrules.yaml`: `ceph_health_detail{name="RECENT_CRASH"} == 1` → `ceph_health_detail{name=~"RECENT_CRASH\|RECENT_MGR_MODULE_CRASH"} == 1`; `description` annotation updated to mention mgr module crashes too. |
| **Why** | The alert only matched the `RECENT_CRASH` daemon-crash health check, not `RECENT_MGR_MODULE_CRASH` - a distinct Ceph health-detail check name. That gap is why `CephCrashesDetected` never fired through the crash storm behind the entry below (estimated week-long, ~39,000 crash events) before the underlying `rook` mgr module was disabled. The widened regex covers any mgr module crash going forward, not just the `rook` one hit this time. |
| **Risk** | None: widening a `ceph_health_detail{name=~...}` regex match only adds coverage to the existing rule; it doesn't change firing behavior for the `RECENT_CRASH` case already handled. |
| **Rollback** | Revert the `expr` back to the single `name="RECENT_CRASH"` match. |
| **Verify** | `kustomize build kubernetes/apps/rook-ceph/rook-ceph/cluster --load-restrictor LoadRestrictionsNone` and `task flux:test:all` render the widened rule (confirmed 2026-08-22). Live confirmation that `ceph_health_detail{name="RECENT_MGR_MODULE_CRASH"}` has matching series in Prometheus/VictoriaMetrics - i.e. that the widened rule would actually have fired - is a separate operational check against the live cluster, not covered by this render-only pass. |

Applied via GitOps only (Flux reconciles the PrometheusRule); no direct live-cluster mutation was made.

### [2026-08-22] Disable rook mgr module - stop crash storm blocking node upgrades  (commit `2014395e`)

| Field | Value |
|-------|-------|
| **Change** | `mgr.modules` in `cluster/helmrelease.yaml`: `rook` entry `enabled: true` → `enabled: false` |
| **Why** | Root-caused the 2026-08-21 `HEALTH_ERR Module 'crash' has failed` entry below: prometheus mgr module calls `node_proxy_fullreport()` on every scrape (15s), unimplemented by the rook orchestrator backend → `NotImplementedError` → crash record for module `rook`. Crash module's `do_post()` mutates `self.crashes` without `crashes_lock` (the only mutator missing it), racing the `serve()` thread's locked iteration → `RuntimeError` → `MGR_MODULE_ERROR` → `HEALTH_ERR` every ~15s, pinning cluster health and blocking every future Talos/Kubernetes node upgrade. Confirmed against the live mgr pod and upstream trackers: rook/rook#18124, ceph/ceph#71041, ceph/ceph#71180 - all open, no released fix as of Ceph Tentacle. |
| **Risk** | None found: nothing in this repo uses `ceph orch` (only `docs/ceph/toolbox.md` documents it as the wrong control plane here). Dashboard and every other mgr module stay functional. The Rook operator reconciles from its own CRDs, not through this mgr module. |
| **Rollback** | Set the `rook` entry in `mgr.modules` back to `enabled: true`. Revert once a Tentacle point release ships both upstream fixes. |
| **Verify** | `ceph mgr module ls` no longer dispatches to `rook` for orchestrator calls; `ceph crash ls-new` count/timestamps flat (was climbing ~4/min); `ceph -s` returns to `HEALTH_OK` within the 2h self-heal window (`mgr/crash/warn_recent_interval=7200s`, already set - no manual `ceph crash archive-all` needed). |

Applied via GitOps only (Flux reconciles the HelmRelease); no direct live-cluster mutation was made.

### [2026-08-21] Default `csi` subvolumegroup still broken on Ceph v20.2.3  (docs; no GitOps / PVC change)

| Field | Value |
|-------|-------|
| **Change** | Docs only. Live probe: Ceph v20.2.3 did **not** repair the default `csi` group's MDS `ceph.dir.subvolume` vxattr bug. Keep subvolumegroup `csi-rwx` and StorageClass `ceph-filesystem-rwx`. Do **not** migrate `downloads/shared-downloads`, `media/tdarr-temp`, or `ai/comfyui-output`. |
| **Why** | The 2026-06-13 entry said retire the workaround once v20.2.2+ ships and the default group is fixed. GitOps and every daemon are already on v20.2.3. v20.2.2/v20.2.3 notes mention a dashboard SMB-form subvolume-group corruption fix (PR #68103), which is a different bug from the Tentacle MDS vxattr regression (PRs #65779 / #65564). |
| **Risk** | Throwaway `create` against group `csi` returned `EINVAL` but still left an empty dir. Live RWX PVC data was not mounted, copied, or re-bound. |
| **Rollback** | n/a (no storage or GitOps change) |
| **Verify** | commands and results below |

**Cluster at probe time (toolbox `deploy/rook-ceph-tools`):**

- `ceph version` → `ceph version 20.2.3 (06c2f9c35b67055a8a6fb99d1be236b3c4832ace) tentacle`
- `ceph versions` → all 17 daemons on that build (3 mon / 2 mgr / 6 osd / 4 mds / 2 rgw)
- `ceph health` → `HEALTH_ERR Module 'crash' has failed: dictionary changed size during iteration` (mgr crash module; unrelated to MDS)
- `ceph mds stat` → `ceph-filesystem:2 {0=ceph-filesystem-a=up:active,1=ceph-filesystem-b=up:active} 2 up:standby-replay`
- Groups: `csi` (created 2025-10-05) and `csi-rwx` (created 2026-06-13)

**Default group `csi` (still broken):**

```text
ceph fs subvolume getpath ceph-filesystem csi-vol-901cd38e-4a3e-479f-8b39-cc634c692cc8 --group-name csi
# Error EINVAL: invalid value specified for ceph.dir.subvolume  (exit 22)

# same EINVAL on leftover probe names already in the group:
#   diag-test-1781359451, diag-retest, t-existing-p1
# and on info / rm / rm --force for those names

ceph fs subvolume create ceph-filesystem fm-verify-csi-1787308879 --group-name csi --size 1048576
# Error EINVAL: invalid value specified for ceph.dir.subvolume  (exit 22)
# BUT the name still appears in `ceph fs subvolume ls` (partial create)
```

libcephfs (toolbox, admin key): dir `/volumes/csi/fm-verify-csi-1787308879` exists, empty uuid child `b06dd9b4-f715-49fd-b8cd-290cc81844c6`, vxattr `ceph.dir.subvolume=0` (a real subvolume is `1`). `rmdir` → `PermissionDenied`. Toolbox `ceph-fuse` cannot mount (`fuse: device not found`). Leftover dir remains; same class as the pre-existing `diag-*` names. Do not `rm -rf` anything under `/volumes/csi/csi-vol-*`.

**Control group `csi-rwx` (healthy):**

```text
ceph fs subvolume getpath ceph-filesystem csi-vol-65d64298-ceeb-42c3-af19-628317eb2af6 --group-name csi-rwx
# /volumes/csi-rwx/csi-vol-65d64298-ceeb-42c3-af19-628317eb2af6/77f03695-b368-4a59-be2c-fa1d6e154f93

ceph fs subvolume create ceph-filesystem fm-verify-csi-1787308879 --group-name csi-rwx --size 1048576   # exit 0
ceph fs subvolume getpath ceph-filesystem fm-verify-csi-1787308879 --group-name csi-rwx                 # exit 0
ceph fs subvolume rm ceph-filesystem fm-verify-csi-1787308879 --group-name csi-rwx                      # exit 0
# csi-rwx still has exactly the original 3 CSI volumes (the 3 RWX PVCs)
```

**Still using `ceph-filesystem-rwx`:** `downloads/pvc` (`shared-downloads` 2Ti), `media/tdarr` (`tdarr-temp` 200Gi), `ai/comfyui` (`comfyui-output` 25Gi).

**Retirement rule:** image tag is not sufficient. Re-test with a throwaway `create`/`getpath`/`rm` against group `csi` on a future Ceph release; only migrate the 3 RWX PVCs after that probe is clean. Prefer a name you can leave behind if `create` EINVAL-leaks another empty dir.

### [2026-08-16] Rook v1.20.3 → v1.20.4  (PR #1316)

| Field | Value |
|-------|-------|
| **Change** | operator + cluster OCI tags `v1.20.4` |
| **Why** | Renovate chart bump |
| **Risk** | Operator/CSI roll; OSD updates stay serial (`storage.osdMaxUpdatesInParallel: 1`) |
| **Rollback** | pin both `ocirepository.yaml` tags back to v1.20.3 |
| **Verify** | `kubectl -n rook-ceph get ocirepository rook-ceph rook-ceph-cluster -o jsonpath='{.items[*].spec.ref.tag}'` |

Keep `operator/csi-driver-rbac.yaml` (Rook #17644). v1.20.4 still does not ship CSI driver ServiceAccounts in the operator chart; removing that file breaks RBD/CephFS attach. See the 2026-06-12 Rook v1.20.0 entry.

### [2026-08-11] Ceph v20.2.2 → v20.2.3  (PR #1293)

| Field | Value |
|-------|-------|
| **Change** | `cephImage.tag: v20.2.3` |
| **Why** | Tentacle point release |
| **Risk** | OSD image roll (serial). Does **not** retire the `csi-rwx` workaround; that needs a live `ceph fs subvolume create/getpath` against group `csi` first. |
| **Rollback** | pin `cephImage.tag` back to v20.2.2 |
| **Verify** | `kubectl -n rook-ceph get cephcluster -o jsonpath='{.items[0].spec.cephVersion.image}'` |

### [2026-08-06] Rook v1.20.2 → v1.20.3  (PR #1269)

| Field | Value |
|-------|-------|
| **Change** | operator + cluster OCI tags `v1.20.3` |
| **Why** | Renovate chart bump |
| **Rollback** | pin both tags back to v1.20.2 |
| **Verify** | OCIRepository tags `v1.20.3` |

### [2026-07-14] Rook v1.20.1 → v1.20.2  (PR #1159)

| Field | Value |
|-------|-------|
| **Change** | operator + cluster OCI tags `v1.20.2` |
| **Why** | Renovate chart bump |
| **Rollback** | pin both tags back to v1.20.1 |
| **Verify** | OCIRepository tags `v1.20.2` |

### [2026-07-03] Mon + logcollector Guaranteed QoS  (commit `e27686d0`)

| Field | Value |
|-------|-------|
| **Change** | Mon and logcollector pods set to Guaranteed QoS so Talos OOMController does not pick quorum members as victims. |
| **Why** | Companion to the 2026-07-03 OOM-outage recovery (same day). |
| **Rollback** | revert the QoS block in `cluster/helmrelease.yaml` |
| **Verify** | mon / logcollector pods show request==limit, QoS Class `Guaranteed` |

### [2026-07-03] OOM-outage recovery: Talos OOMConfig retuned (live + codified), transient `min_size=1` windows, noout window  (operational + `talos/machineconfig.yaml.j2`)

| Field | Value |
|-------|-------|
| **Change** | (1) **Talos `OOMConfig` trigger retuned on ALL 3 nodes**: `triggerExpression: memory_full_avg10 > 50.0 && d_memory_full_avg10 > 0.0 && time_since_trigger > duration("30s")` (defaults: `> 12.0` / `500ms`). Applied live via `talosctl patch mc` at 15:04 UTC 2026-07-03, then **codified in `talos/machineconfig.yaml.j2`** (a live patch survives reboot but NOT a config re-render — codifying was mandatory). (2) **Two transient `min_size=1` windows on the ceph-blockpool ONLY** to break the RBD/activate circular deadlock (ceph-volume D-state on frozen `/dev/rbdX` blocking the very OSDs that would unfreeze them); **restored to `min_size=2` both times**. (3) `noout` set ~13:30 UTC → unset ~20:15 UTC (after all 6 OSDs up + HEALTH_OK). |
| **Why** | 2026-06-30 → 07-03 outage: the Talos v1.12 OOMController default PSI trigger SIGKILLed the heaviest Burstable cgroups (cilium-agent, OSD pods, kube-apiserver) on normal Ceph memory-PSI spikes → 4/6 OSDs down, **100% PGs inactive ~3 days**, kill storms on all 3 nodes during recovery. Full incident: [`hardware-incidents.md` [2026-06-30]](./hardware-incidents.md). |
| **Risk** | OOMConfig: at 50%/30s a node tolerates sustained heavy memory pressure before killing — a genuine runaway leak is reaped later than before (kernel OOM killer remains the last-resort backstop). `min_size=1`: single-replica write exposure on the blockpool during each window (an OSD death then = data loss) — kept to minutes and reverted. `noout`: masks genuinely dead OSDs while set — bounded to the recovery window. |
| **Rollback** | OOMConfig: revert the block in `talos/machineconfig.yaml.j2` + `just talos apply-node` per node (returns to Talos defaults). `min_size`/`noout`: already restored/unset — nothing to roll back, verify only. |
| **Verify** | `talosctl -n <node> get oomconfig -o yaml` → trigger `50.0`/`30s` on all 3 nodes; `ceph osd pool ls detail \| grep min_size` → 2 everywhere; `ceph osd dump \| grep flags` → no `noout`; `ceph status` → HEALTH_OK, 393 active+clean. Post-patch: **zero OOMController events** through full recovery + backfill. |

Companion durable fixes shipped with the recovery (GitOps): cilium-agent → **Guaranteed QoS** (OOM ranking weight 0.0), `NotIn [talos-1]` affinities on 7 heavy burstables (TODO 2026-08: revert after the talos-1 RAM RMA), node-exporter re-enabled + PrometheusRule alerts (`Talos1MemoryPressure`, `NodeMemoryPSIHigh`, `CephOsdPodTerminalError`, `CephPodCrashLooping`). Ruled out during root-cause: MGLRU (verified `0x0000` on all nodes), rook#17224 path drift (all OSDs re-activated cleanly after full reboots), store corruption/bad hardware (zero signatures through ~6 kill cycles).

### [2026-06-22] Ceph v20.2.1 → v20.2.2  (PR #1021)

| Field | Value |
|-------|-------|
| **Change** | `cephImage.tag: v20.2.2` |
| **Why** | Tentacle point release |
| **Note** | Did not retire `csi-rwx`. Workaround still in GitOps after later v20.2.3. |
| **Rollback** | pin `cephImage.tag` back to v20.2.1 |
| **Verify** | CephCluster image tag v20.2.2 |

### [2026-06-21] Rook v1.20.0 → v1.20.1  (PR #1022)

| Field | Value |
|-------|-------|
| **Change** | operator + cluster OCI tags `v1.20.1` |
| **Why** | Renovate chart bump |
| **Note** | CSI driver RBAC workaround `operator/csi-driver-rbac.yaml` stayed; v1.20.1 did not bake those ServiceAccounts into the operator chart. |
| **Rollback** | pin both tags back to v1.20.0 |
| **Verify** | OCIRepository tags `v1.20.1` |

### [2026-06-17] Multi-failure cascade + recovery; deep root-cause (B70 DMA theory EXONERATED)  (operational, no GitOps merge yet)

| Field | Value |
|-------|-------|
| **What happened** | Re-adding osd.0 (stopgap, PR #994) → backfill thrash on the fragile cluster → **talos-3 OOM storm** killed both its OSDs (osd.2 Samsung went into a permanent NVMe **D-state IO hang**; osd.4 Lexar stuck) → **mon.l (talos-1) RocksDB corrupt crashloop** → fragile **2/3 quorum** with the 2 survivors including mon.h on the hung talos-3 → **28 PGs inactive**, 36% degraded. No data lost. |
| **Recovery (executed)** | (1) Rebuilt corrupt **mon.l → mon.m** (operator→0, del mon-l deploy, `ceph mon remove l`, del mon-l PVC, patch `rook-ceph-mon-endpoints` cm, operator→1) → 3/3 `h,i,m`. (2) **Disabled MGLRU on talos-3** (`echo 0 > /sys/kernel/mm/lru_gen/enabled`, 0x7→0x0). (3) **Rebooted talos-3** (graceful; quorum held i,m) → cleared osd.2 hang + osd.4 → 6/6 OSDs up, mon.h rejoined, inactive PGs cleared. (4) Resumed **throttled** recovery → **393 active+clean / HEALTH_OK**. Fresh osd.0 survived the full backfill this round. |
| **Root cause (deep research, 5-agent)** | **NOT one B70 cause; two separate roots.** **(a) talos-3 OOM** = confirmed **kernel-6.18.3 MGLRU regression** (Talos default-on) + B70/vLLM **host-RAM** footprint + **no swap** ("OOM-kill, empty victim, memory free"). **(b) talos-1 corruption** (osd.0 980 PRO "out-of-order keys"/`_txc_apply_kv` + mon.l RocksDB) = **bad RAM OR a single lying/bad 980 PRO** — **the B70 is on talos-3, talos-1 has NO GPU**, so the GPU can't be the cause. **Lexar "wedges"** = DRAM-less HMB drives stalling → PG lease/peering stall (`waiting for readable`), **network RULED OUT** (bond/LACP/MTU-9000 all clean incl. talos-3's renamed NICs). **OOM-kill is crash-SAFE** (RocksDB WAL) — it did not cause the corruption. **`nvme_core.io_timeout=4294967295` (∞)** turns transient NVMe stalls into permanent error-free D-state hangs (reboot-only). |
| **⚠️ Risk / EXONERATED** | The prime suspect — `pci=realloc`/`pci=assign-busses` + 32 GB ReBAR causing silent DMA/NVMe corruption — is **largely exonerated** (those fail loud; 32G BAR placed above RAM by design; corruption is on the no-GPU node). Keep IOMMU/AER on the rule-out list only. |
| **State** | **HEALTH_OK** (393 active+clean, 6/6, 3/3) on **temporary live mitigations (DRIFT, not in GitOps)** — see below. Cluster is patched, not permanently fixed. |
| **Verify** | `ceph status` → 393 active+clean, 6/6 up, quorum h,i,m. `ceph osd stat`/`ceph mon stat`. |

**⚠️ 2026-06-17 live `ceph config set` drift vs GitOps (do not treat this list as current without a live dump):**
GitOps `cephConfig` now has `osd_mclock_profile: high_client_ops` and `osd_recovery_max_active: "3"`.
`osd_mclock_override_recovery_settings`, `osd_max_backfills=1`, and `osd_recovery_sleep_ssd=0.05` are
**not** in the HelmRelease. Rook does not auto-`rm` unmanaged mon-DB keys, so those three may still
overlay GitOps. Confirm with `ceph config dump` before assuming recovery is capped at 1 **or** at 3.
Do not GitOps-ify or `config rm` them from this changelog pass. Plus **MGLRU disabled on talos-3 was
RUNTIME-ONLY (resets on reboot)**.

**Durable follow-ups (NOT done — the real fixes):** (1) **memtest86+ talos-1** — settle bad-RAM vs lying-980-PRO
for the osd.0/mon corruption. (2) **Persistent MGLRU disable** (kernel past the 6.18.3 fix, or a boot DaemonSet
writing `lru_gen/enabled=0`). (3) **Finite `nvme_core.io_timeout`** (e.g. 180s) so stalls self-recover instead of
permanent hangs. (4) **Replace DRAM-less no-PLP Lexar NM790s** with PLP/DRAM drives (P0). (5) **Cap vLLM/AI memory
on talos-3** so it can't OOM-starve Ceph. (6) Formalize/revert the drift above. Full analysis: agent memory
`reference_ceph_cascade_rootcause_2026-06-17`. Until memtest clears talos-1, **treat osd.0 as still suspect**.

### [2026-06-16] osd.0 — active silent write-corruption (rebuild recurred → HARDWARE-suspect, osd.0 held OUT)

| Field | Value |
|-------|-------|
| **Change** | (1) Destroyed + recreated **osd.0** (Samsung 980 PRO 2TB, talos-1): purge → wipe disk → operator re-provision → backfill. (2) **After the fresh store re-corrupted under backfill writes, osd.0 was marked `out` + scaled to 0** — cluster running stable on 5 OSDs pending a hardware decision. New runbook [`docs/ceph/osd-store-corruption-recovery.md`](./ceph/osd-store-corruption-recovery.md). |
| **Why** | osd.0 crash-looped: aborted **every startup** in `load_pgs → PG::read_state → BlueStore::omap_iterate` (uncaught C++ `terminate`) = a corrupt pg_log/pg_info OMAP record it couldn't decode. Rebuilt from peers (canonical fix when redundancy intact + disk SMART-healthy). **BUT ~90 min into backfill the FRESH store aborted again** with a *different, decisive* signature: `rocksdb: Background IO error Corruption: Compaction sees out-of-order keys` → `BlueStore.cc:14648 FAILED ceph_assert(r == 0)` in `_txc_apply_kv`. That is **active silent data corruption on write** (the read-side omap crash was its downstream aftermath). SMART **clean** (media_errors 0, err-log 0, spare 100%, 6% used, 52 °C) and **zero NVMe/IO errors in talos-1 dmesg**, and no MCE/EDAC → corruption is invisible to the device layer. |
| **Hypotheses → triaged** | **RAM ~CLEARED (no downtime):** talos-1 runs Intel `igen6` **in-band ECC** — `/sys/devices/system/edac/mc/{mc0,mc1}` report **ce_count=0, ue_count=0**, `HardwareCorrupted: 0`, 57 GiB free (not OOM). In-band ECC would have logged correctable/uncorrectable bit-errors; it shows none. **Firmware NOT the cause:** osd.0's 980 PRO is on **`5B2QGXA7`** (the *fixed* Samsung fw), identical to the stable osd.2 980 PRO. → **Prime suspect = osd.0's individual 980 PRO unit, or its talos-1 M.2 slot / PCIe lane** (B70 OCuLink/`pci=realloc` node); silent corruption SMART can't see. Definitive test = swap the drive (move the suspect unit to another slot/node to tell drive-vs-path apart). RAM memtest now low-priority. osd.3's separate slow-op crashes are the no-PLP-Lexar fragility, not this corruption. |
| **Risk** | Data safe throughout — osd.0 only ever held *partial* backfill copies; full replicas always existed on the other 5 OSDs. `ceph osd safe-to-destroy osd.0` was confirmed before purge. Each osd.0 abort briefly degraded ~54 PGs (recovered). |
| **State / next** | **REVERSED same day:** running osd.0-OUT left talos-1 with only the fragile Lexar osd.3 → repeated CephFS-metadata **slow-op wedges** (768 blocked ops, MDS stall, client IO→0; cleared each time with `ceph osd down osd.3`). Decision (user): **re-add osd.0 as a STOPGAP** despite the corruption fault — corruption is contained (BlueStore csum + replica-3 → osd.0 *crashes*, doesn't lose/serve bad data), and it restores talos-1's fast OSD that the metadata pool needs. Re-added by uncommenting the disk in the HR (disk already zapped → Rook provisions a fresh osd.0 + backfill). **VALIDATED 2026-06-17:** fresh osd.0 backfilled + served **3h50m**, then crashed (`_txc_apply_kv r==0`, deep-scrub-triggered) and **self-recovered** — **no data damage** (zero inconsistent/damaged PGs; the fault crashes osd.0's local KV, it does not commit bad object replicas), cluster self-healed the brief degrade. Kept `osd_mclock_profile=high_client_ops` (live `ceph config set`, NOT in GitOps) while osd.0 is the flaky stopgap, to keep recovery gentle on the Lexars during osd.0's repeated crash→recover cycles (revert to default after hardware swap). **Still NOT a fix — swap the drive/slot; expect periodic osd.0 crash+recover until then. Watch for: a crash-LOOP (osd.0 fails to restart → needs zap+rebuild or re-removal) or a Lexar slow-op wedge (`ceph osd down osd.3`).** |
| **Verify** | `ceph status` → all `active+clean` on **5** OSDs once reconvergence finishes; `ceph osd tree` → osd.0 `down`/`out`. Re-corruption proof: `kubectl -n rook-ceph logs rook-ceph-osd-0-... -c osd --previous \| grep "out-of-order keys"`. |

**Procedure (executed):** operator→0; `scale/delete deploy rook-ceph-osd-0`; `ceph osd purge 0`;
wipe **only** the by-id Samsung 980 PRO via a rendered `WipeDiskJob` (the `task rook:reset-disk`
runner's `envsubst < <(...)` is unreliable on Windows); operator→1 → fresh osd.0 rejoined →
backfilled to ~7.7% misplaced (≈halfway) in ~90 min → **aborted with out-of-order-keys corruption**
→ marked osd.0 `out`, operator→0, osd-0 deploy→0. **Chronic fragile osd.0** (112 crashes since
2026-01-01; prior `_txc_apply_kv r==0` on 2026-06-02). No public Ceph tracker matches either
signature. **Lesson: a rebuild clears corrupt *data* but not a corrupt *substrate* — if a freshly
rebuilt OSD re-corrupts under load, stop blaming the data and investigate hardware (drive + node
RAM/PCIe).** Logged as a hardware incident → see [`hardware-incidents.md`](./hardware-incidents.md).

### [2026-06-14] OSD device-path drift hardening (Rook #17224) + storage.osdMaxUpdatesInParallel  (PR #984/#985/#986)

| Field | Value |
|-------|-------|
| **Change** | (1) Docs/tooling: runbook [`docs/ceph/osd-device-path-recovery.md`](./ceph/osd-device-path-recovery.md), `task rook:check-osd-device-paths` (+ script), CLAUDE.md reboot guardrail. (2) **`cephClusterSpec.storage.osdMaxUpdatesInParallel: 1`** — roll operator-driven OSD updates one at a time (CRD default **20** ≈ all 6 OSDs at once). |
| **Why** | During the 2026-06-14 CephFS-metadata-storm recovery, restarting `osd.4` exposed Rook [#17224](https://github.com/rook/rook/issues/17224) (`wontfix`): raw-mode OSDs store the resolved **kernel name** in `ROOK_BLOCK_PATH`, not the by-id path we set in the CR; kernel names reshuffle across reboots so the path goes stale. The relocate fallback self-heals **under normal load** but returns empty when the cluster is already wedged — so a restart during a degraded cluster leaves an OSD stuck `Init`. `osdMaxUpdatesInParallel=1` limits the blast radius during operator upgrades (each OSD self-heals against a healthy cluster). |
| **Risk** | Minimal. The setting is operator-orchestration only — applied **live, no OSD restart**; only makes future Ceph/Rook upgrades sequential. Docs/task read-only. Live-verified: relocate fallback works under HEALTH_OK on all 3 nodes; 4/6 OSD deployments (osd.3/4/5/6) are stale-but-running (harmless until restarted). |
| **Rollback** | `git revert` → setting back to default 20; docs/task drop out. |
| **Verify** | `kubectl -n rook-ceph get cephcluster -o jsonpath='{.items[0].spec.storage.osdMaxUpdatesInParallel}'` → `1`. `task rook:check-osd-device-paths` → drift audit + HEALTH_OK gate. |

**Path correction (process note):** `osdMaxUpdatesInParallel` lives at **`spec.storage.osdMaxUpdatesInParallel`** (nested under `storage`), NOT `spec.osdMaxUpdatesInParallel`. #984 first set it at the wrong (spec) level → the API silently pruned it → inert; #985 then *mis*diagnosed the prune as "CRD drift" and reverted it. Investigated to ground truth: the **CRDs are current (v1.20.0)** — every "missing" field exists at its real nested path (`spec.storage.osdMaxUpdatesInParallel`, `spec.csi.readAffinity`). **No CRD drift, no CRD surgery needed.** #986 sets it at the correct path. Lesson: when `kubectl explain spec.X` says "field does not exist", search the full CRD schema for the real (possibly nested) path before concluding the CRD is stale.

**Best-practice note:** raw mode + by-id device refs (what we run) **is** the Rook-recommended layout — raw is the modern default; LVM is legacy, reserved for encryption + `metadataDevice`, and risks LVM-tag corruption ([Rook ceph-volume design](https://github.com/rook/rook/blob/master/design/ceph/ceph-volume-provisioning.md)). So there is **no best-practice "permanent fix"** for #17224 short of the upstream code change; raw→LVM is a last-resort workaround only. **Keep OFF** (all at safe defaults): `upgradeOSDRequiresHealthyPGs` (deadlocks with #17224), `removeOSDsIfOutAndSafeToRemove` (could auto-purge a recoverable stale OSD), `skipUpgradeChecks`/`continueUpgradeAfterChecksEvenIfNotHealthy`. Storm **trigger** removed separately (SABnzbd tiny-file flood → ceph-block, PR #983). Guardrail: confirm HEALTH_OK + run the audit before `just talos upgrade-node`/`reboot-node`/`reset-node`.

### [2026-06-14] P2: pinned realistic per-OSD mClock IOPS (override inflated auto-bench)  (PR #981)

| Field | Value |
|-------|-------|
| **Change** | Set `osd_mclock_max_capacity_iops_ssd` per-OSD via `cephClusterSpec.cephConfig` per-daemon sections (`"osd.N":`): Lexar NM790 **osd.3/4/5 → `7000`**, Samsung 980/990 PRO **osd.0/2/6 → `15000`**. |
| **Why** | mClock startup auto-bench reported **49k–61k IOPS for every OSD** (config source `basic`) — ~3–4× too high for the no-PLP Samsungs, **~8–12× too high for the DRAM-less NM790s** — on the 4K sync-write path Ceph uses. Inflated capacity mis-allocates client vs background IO and is a plausible contributor to the slow-ops/laggy-PG cascade. |
| **Risk** | Low — scheduler tuning only; no data-path/peering/availability impact. Values sit well above the `1000` low-guard so client IO is protected, not starved. Picked up **live, no OSD restart** (restarting the fragile NM790s is itself a slow-ops risk, so avoided). |
| **Rollback** | `git revert`; then (Rook doesn't auto-`rm` unmanaged keys) in toolbox: `for n in 0 2 3 4 5 6; do ceph config rm osd.$n osd_mclock_max_capacity_iops_ssd; done` + restart OSDs to re-enable auto-bench. |
| **Verify** | `ceph config dump \| grep mclock_max_capacity` → 6 keys, source `advanced`; `ceph config get osd.N osd_mclock_max_capacity_iops_ssd` → `7000`/`15000`. |

Values are conservative literature/community estimates (Proxmox false-`osd_mclock_max_capacity_iops_ssd` thread; consumer-NVMe 4K sync-write reviews), **NOT fio-measured** — fio on a live BlueStore device is destructive, so deferred (measure only on a drained + stopped OSD, one at a time). The three *identical* NM790s auto-benched 52.7k/55.4k/60.9k (16% spread) — the classic "bench caught SLC-cache burst, not steady state" symptom. OSD→model mapping verified by drive **serial** via `ceph osd metadata` (the `/dev/nvmeXn1` names differ between Rook's namespace view and Talos enumeration). Once a value is set the OSD logs `Skip OSD benchmark test` and never re-benches until the key is removed.

**Cleanup note (ad-hoc toolbox op, NOT GitOps):** a stale `osd.1` key `osd_mclock_max_capacity_iops_ssd=41589.54` lingers in the mon config DB for a non-existent OSD (`ceph osd find 1` → `ENOENT`; not in the CRUSH tree). Harmless (no daemon reads it) but untidy — remove it with:
```bash
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph config rm osd.1 osd_mclock_max_capacity_iops_ssd
```
It lives only in the mon DB (not the helmrelease), so it can't be expressed as a GitOps revert — hence this runbook note.

### [2026-06-14] P1 verified: CephFS already on kernel client — removed dead `forceCephFSKernelClient` config  (PR #981)

| Field | Value |
|-------|-------|
| **Change** | Removed two **no-op** CSI config lines: `csi.cephfs.forceCephFSKernelClient: false` from `cluster/helmrelease.yaml` and `csi.cephFSKernelMountOptions` from `operator/helmrelease.yaml`. Kept the effective `cephClusterSpec.csi.cephfs.kernelMountOptions: ms_mode=prefer-crc`. Docs truth-up (this entry + baseline table + backlog P1). |
| **Why** | Backlog P1 assumed CephFS RWX was on slow `ceph-fuse` and a flip to the kernel client was the biggest perf lever. **Live verification disproved this:** CephFS is already on the kernel client. Under Rook v1.20 (ceph-csi-operator mode) the mounter is the ceph-csi `Driver` CR `cephFsClientType` (=`autodetect` → kernel on Talos 6.x; OperatorConfig default `kernel`). The chart `forceCephFSKernelClient` value path doesn't exist in the cluster chart, and `cephFSKernelMountOptions` was dropped from the v1.20 operator chart — both silently ignored. |
| **Risk** | None functional — removing inert values doesn't change rendered manifests, so Helm sees no Driver/CephCluster diff. A Helm upgrade may bounce the operator pod, but existing kernel CephFS mounts are host mounts and persist independently. |
| **Rollback** | `git revert`; the deleted lines were inert, so they return as inert. |
| **Verify** | `/proc/mounts` on each node: CephFS mounts are `type ceph` + `ms_mode=prefer-crc` + mon port 3300, **no `ceph-fuse`** (talos-1: 11, talos-3: 4); `kubectl -n rook-ceph get drivers.csi.ceph.io rook-ceph.cephfs.csi.ceph.com -o jsonpath='{.spec.cephFsClientType}'` → `autodetect`. |

Evidence captured 2026-06-14 (read-only): all CephFS mounts kernel-client; ceph-csi `Driver` `cephFsClientType: autodetect`; `OperatorConfig` default `kernel`; operator ConfigMap has no `CSI_FORCE_CEPHFS_KERNEL_CLIENT` / `CSI_CEPHFS_KERNEL_MOUNT_OPTIONS`; CephCluster CRD `spec.csi.cephfs` exposes only `fuseMountOptions` + `kernelMountOptions` (no mounter-type knob). The related [2026-06-12 "CephFS CSI forced to autodetect → ceph-fuse"](#2026-06-12-cephfs-csi-forced-to-autodetect--ceph-fuse--pr--commit-17c7a9df-ae44b3ac) change never actually forced fuse — at most it produced `autodetect`, which still mounts via the kernel on this kernel version.

### [2026-06-13] CephFS RWX moved to fresh `csi-rwx` subvolumegroup  (PR #977)

| Field | Value |
|-------|-------|
| **Change** | New subvolumegroup `csi-rwx` (clusterID `eb9cbc3c…`) + StorageClass `ceph-filesystem-rwx`; migrated `downloads/shared-downloads` (2Ti) and `media/tdarr-temp` onto it |
| **Why** | Ceph v20.2.1 corrupted the default `csi` subvolumegroup → all RWX provision/expand/snapshot/delete failed `EINVAL invalid value specified for ceph.dir.subvolume` |
| **Risk** | data migration; full-throttle rsync (310 MB/s) **wedges** the small OSDs (osd.0/osd.6) — use `--bwlimit=80M` |
| **Rollback** | old PVs kept as `Retain` safety-net (delete when confident) |
| **Verify** | apps healthy on `ceph-filesystem-rwx`, Ceph back to baseline |

Cross-ref: [`cephfs-rwx-subvolumegroup.yaml`](../kubernetes/apps/rook-ceph/rook-ceph/cluster/cephfs-rwx-subvolumegroup.yaml). Do **not** retire `csi-rwx` on image tag alone: live probe on v20.2.3 (2026-08-21) still fails `create`/`getpath` against the default `csi` group with `EINVAL: invalid value specified for ceph.dir.subvolume`. Keep `ceph-filesystem-rwx` until a future release passes that probe.

### [2026-06-12] CephFS CSI forced to autodetect → `ceph-fuse`  (PR — commit 17c7a9df, ae44b3ac)

| Field | Value |
|-------|-------|
| **Change** | `csi.cephfs.forceCephFSKernelClient: false` (default is `true`); added `kernelMountOptions: ms_mode=prefer-crc` |
| **Why** | ceph-csi default `cephFsClientType=kernel` reportedly failed with **"Module ceph not found"**; switched to autodetect so CSI falls back to `ceph-fuse` |
| **Risk** | ⚠️ **`ceph-fuse` is materially slower than the kernel client** — this trades performance for "it works" |
| **Rollback** | set `forceCephFSKernelClient: true`, restart CSI cephfs nodeplugin + remount |
| **Verify** | CephFS volumes mount; (today) they mount via fuse |

> **⚠️ This change is now in doubt.** The stated reason — *"Talos does not ship ceph.ko"* — is
> **contradicted** by the Talos kernel config: `CONFIG_CEPH_FS=y` on the `release-1.11/1.12/1.13`
> branches means the CephFS client is **compiled into the kernel** (built-in), so there is no
> loadable `ceph.ko` to be "missing", and `mount -t ceph` should work. The "Module ceph not found"
> error is the classic **built-in-vs-loadable gotcha** (a `=y` feature has no `.ko`, so a module
> presence check fails even though the FS works). ceph-csi's own mounter
> (`internal/cephfs/mounter/volumemounter.go`) detects kernel support by probing the `mount.ceph`
> **binary + kernel version**, not by modprobe — so the real failure on 2026-06-12 is **unexplained
> by kernel capability** and must be re-tested live. See [backlog P1](#p1--re-test-the-cephfs-kernel-client-biggest-perf-lever).

### [2026-06-12] Rook v1.19.6 → v1.20.0  (PR #934)

| Field | Value |
|-------|-------|
| **Change** | operator + cluster chart bumped to v1.20.0 |
| **Why** | stay current; v1.20.0 is the latest release |
| **Risk** | v1.20.0 ships the CSI driver RBAC outside the operator chart → needed a manual workaround (`operator/csi-driver-rbac.yaml`, [rook#17644](https://github.com/rook/rook/issues/17644)). Keep that file until a `ceph-csi-drivers` chart (or equivalent) is added to GitOps. Rook's fix was "install the CSI driver charts", not "v1.20.1 bakes RBAC into the operator chart". Charts are now v1.20.4 and the workaround is still required; deleting it reproduces `FailedCreate: serviceaccount not found` and breaks all RBD/CephFS attach. |
| **Rollback** | pin `ocirepository.yaml` tag back to v1.19.6 |
| **Verify** | `kubectl -n rook-ceph get deploy rook-ceph-operator -o jsonpath='{.spec.template.spec.containers[0].image}'` |

### [2026-06-03] Raised OSD/MDS memory targets  (PR #908)

| Field | Value |
|-------|-------|
| **Change** | `osd_memory_target` → 10 GiB (OSD pod limit → 14Gi); `mds_cache_memory_limit` → 8 GiB (MDS limit → 10Gi) |
| **Why** | read-cache headroom; more onode/metadata hits → fewer reads hit the slow drives. **Read optimisation, NOT a fix for the write-latency slow-ops storm.** |
| **Rollback** | revert the two values + pod limits |
| **Verify** | `ceph config get osd osd_memory_target`; OSD RSS under limit |

### [2026-06-03] Gentler off-peak scrubs + `high_client_ops` mClock  (PR #905)

| Field | Value |
|-------|-------|
| **Change** | `osd_max_scrubs: 1`, scrub window 01:00–07:00, `osd_mclock_profile: high_client_ops` |
| **Why** | reduce background-IO contention on consumer NVMe; prioritise client IO |
| **Rollback** | remove the three keys (reverts to mClock `balanced` default + 24/7 scrubs) |
| **Verify** | `ceph config get osd osd_mclock_profile` → `high_client_ops` |

### [2026-06-01 → 06-02] PG autoscaler + compression + roots fixes  (PRs #900, #902, #903, #904)

| Field | Value |
|-------|-------|
| **Change** | compression `none` on all pools + freed OSD cache (#900); `bulk: true` on block + cephfs-data0 (#902); restored immutable ceph-block StorageClass params to unstall the HR (#903); dropped `deviceClass: nvme` from cephfs-data0 to fix "overlapping roots" that had stalled the autoscaler on **all** pools (#904) |
| **Why** | aftermath of the 2026-06-01 slow-ops incident — see [`ceph-performance-review.md`](./ceph-performance-review.md) |
| **Rollback** | per-PR `git revert` |
| **Verify** | `ceph osd pool ls detail` (compression none, pg_num growing); `ceph osd pool autoscale-status` (no overlapping-roots, all pools listed) |

### [2026-04-11] Ceph v20.2.0 → v20.2.1  (PR #681)

| Field | Value |
|-------|-------|
| **Change** | `cephImage.tag: v20.2.1` |
| **Why** | Tentacle point release |
| **Note** | v20.2.1 corrupted the default `csi` subvolumegroup (later worked around — see 2026-06-13) |

---

## Backlog — proposed changes (researched, NOT yet applied)

Ranked by impact. Each is verify-then-apply; treat as plan-mode work, not a blind merge.
Sources are from the 2026-06-14 deep-research pass (Ceph Squid/Tentacle-era docs).

### P1 — ✅ RESOLVED (2026-06-14): CephFS kernel client was already active

> **Resolution:** No flip was needed. Live verification showed CephFS RWX is **already on the
> kernel client** (`/proc/mounts` → `type ceph`, `ms_mode=prefer-crc`, mon port 3300 msgr2; **no
> `ceph-fuse`** on any node). Under Rook v1.20 (ceph-csi-operator mode) the mounter is the ceph-csi
> `Driver` CR `cephFsClientType` (=`autodetect` → kernel on Talos 6.x; `OperatorConfig` default
> `kernel`), **not** the chart `forceCephFSKernelClient` value (which is inert here). The dead
> `forceCephFSKernelClient` / `cephFSKernelMountOptions` lines were removed; the effective option
> `cephClusterSpec.csi.cephfs.kernelMountOptions` was kept. See the [2026-06-14 change-log entry](#2026-06-14-p1-verified-cephfs-already-on-kernel-client--removed-dead-forcecephfskernelclient-config--pr-981).
> The original analysis below is retained as the read-only evidence procedure.

- **What (original):** flip `csi.cephfs.forceCephFSKernelClient` back to `true` so CephFS RWX uses the
  Linux kernel client (`mount -t ceph`) instead of `ceph-fuse`.
- **Why:** the kernel CephFS client is **materially faster** than `ceph-fuse` (userspace,
  per-op context switches), and Talos v1.13 has `CONFIG_CEPH_FS=y` **built-in** — the
  "Talos lacks ceph.ko" premise behind the current fuse fallback is wrong. RBD (`ceph-block`)
  already uses the kernel `krbd` mounter by default, so only CephFS is on the slow path.
- **Verify FIRST (live, read-only):**
  ```bash
  # 1. Confirm the running kernel exposes ceph as a filesystem (built-in => listed, no module needed)
  talosctl -n <node-ip> read /proc/filesystems | grep ceph        # expect: "  ceph"
  talosctl -n <node-ip> read /proc/modules    | grep -E 'ceph|rbd' # built-in => may show NOTHING (that's fine)
  # 2. Which mounter is each PV using right now?
  kubectl get pv -o json | jq -r '.items[]|select(.spec.csi.driver|test("cephfs"))|.spec.csi.volumeHandle' # cephfs PVs
  #   on the node, a kernel cephfs mount shows "type ceph"; fuse shows "ceph-fuse" in `mount`
  ```
- **Then:** in a maintenance window, set `forceCephFSKernelClient: true`, let Flux reconcile,
  restart the `csi-cephfsplugin` nodeplugin DaemonSet, and **remount** (existing fuse mounts
  don't auto-switch — bounce one low-risk consumer pod and confirm it comes up as `type ceph`).
- **Gotcha (msgr2):** the cluster has `requireMsgr2: true`. Keep `kernelMountOptions:
  ms_mode=prefer-crc` so the kernel client negotiates msgr2 on mon port 3300. Modern kernels
  (Talos 1.13 is 6.x) support msgr2, so this should connect.
- **Risk:** if the kernel mount genuinely fails (re-creating the original "Module ceph not
  found" / a real msgr2 issue), revert to `false` — fuse is the known-good fallback. Blast
  radius of krbd/kernel-cephfs is node-wide on a kernel bug, vs userspace for fuse, but the
  kernel client is the battle-tested production default.
- **Note:** `# CONFIG_FSCACHE is not set` on Talos → the kernel CephFS client's optional
  fscache-backed page caching is unavailable, but that's not required for normal operation.

### P2 — ✅ RESOLVED (2026-06-14): pinned per-OSD mClock IOPS

> **Resolution:** Confirmed live the auto-bench inflated every OSD to 49k–61k IOPS (NM790s ~8–12×
> over realistic 4K sync-write). Pinned conservative per-OSD `osd_mclock_max_capacity_iops_ssd`
> (NM790 osd.3/4/5 → 7000; Samsung osd.0/2/6 → 15000) via GitOps `cephConfig` per-daemon sections —
> see the [2026-06-14 change-log entry](#2026-06-14-p2-pinned-realistic-per-osd-mclock-iops-override-inflated-auto-bench--pr-981).
> `fio` measurement deferred (destructive on live OSDs); values are conservative estimates, easily
> iterated. The original analysis below is retained as the rationale + (deferred) fio methodology.

- **What (original):** measure real 4 KiB random-write IOPS with `fio` on each OSD device, then set
  `osd_mclock_max_capacity_iops_ssd` per-OSD instead of trusting the startup auto-bench.
- **Why:** mClock auto-benchmarks each OSD's IOPS at startup; **on fast/DRAM-less NVMe this
  result is frequently inflated/unrealistic**, which mis-allocates client vs background IO and
  is a plausible contributor to the laggy/slow-ops cascade. Threshold guards exist
  (`osd_mclock_iops_capacity_threshold_ssd` 80000 high / `…_low_threshold_ssd` 1000 low).
- **Verify:** `ceph config show osd.N osd_mclock_max_capacity_iops_ssd` per OSD; compare to a
  real fio run; set manually where the auto value is implausible (esp. the three NM790).
- **Source:** docs.ceph.com `/rados/configuration/mclock-config-ref/`;
  Proxmox forum thread on false `osd_mclock_max_capacity_iops_ssd` values.

### P3 — Tighten `BLUESTORE_SLOW_OP_ALERT` as an early-warning tripwire

- **What:** lower `bluestore_slow_ops_warn_lifetime` (default 86400s) and keep
  `bluestore_slow_ops_warn_threshold` low so a struggling drive surfaces *fast* (e.g.
  lifetime 300, threshold 5 — tune to taste), giving time to `ceph osd out`/replace before a
  laggy cascade. `osd_op_complaint_time` default is 30s.
- **Why:** the slow-op alert is the earliest signal of a DRAM-less OSD going bad; a tighter
  window catches it before clients wedge.
- **Source:** docs.ceph.com `/rados/operations/health-checks/`; rook#15403.

### P4 — Verify PG counts land near target (~100/OSD), keep autoscaler honest  (still unverified live)

This changelog owns "what healthy looks like" for this cluster. Do not treat
`docs/ceph/pg.md` generic 100-150 PGs/OSD, docs.ceph.com ~200, and this backlog
as three current truths.

- **GitOps target:** `mon_target_pg_per_osd` 100 with `bulk: true` on block +
  cephfs-data0 (6 OSDs, `size=3`).
- **Observed fingerprint after 2026-06-14 recovery:** **393 PGs active+clean**
  (`ceph status` in the 2026-06-17 and 2026-07-03 entries). That is a point-in-time
  number, not a pin.
- **Upstream:** docs.ceph.com recommends ~200 PGs/OSD for all but the smallest
  clusters; >500 risks peering/RAM.
- **Still open:** confirm `ceph osd pool autoscale-status` / `ceph osd df tree`
  after the next PG change. If a pool is stuck low, consider `pg_autoscale_mode:
  warn` + a manual `pg_num` bump.
- **Source:** docs.ceph.com `/rados/operations/placement-groups/`.

### P5 — (Optional, lower ROI) dedicated cluster/replication network

- **What:** split 3× replication/recovery traffic onto a dedicated VLAN/CIDR (nodes already
  have VLAN 3/90).
- **Why / caveat:** helps *during* recovery/backfill, but if the bottleneck is purely
  consumer-NVMe commit latency (likely here), network separation gives little benefit. Lower
  priority than P1–P2.

### P0 (standing) — the real durable fix: PLP datacenter NVMe

Retiring the three DRAM-less Lexar NM790 for enterprise NVMe with power-loss protection
(Samsung PM9A3, Micron 7450, Kioxia CD/CM, Solidigm D7) remains the single biggest reliability
win — it removes the sync-write latency cliff that triggers the lease cascade at the source.
See [`ceph-performance-review.md`](./ceph-performance-review.md) §F1.

---

## Operational runbooks (for "when something breaks")

### Slow-ops / `waiting for readable` laggy-PG cascade

The PG read lease = `osd_pool_default_read_lease_ratio` (0.8) × `osd_heartbeat_grace`. A slow
OSD that can't renew leases in time flips its PGs to `LAGGY` and **blocks reads** (this is a
*correctness* feature, not a bug — the `recheck_readable` defect [#53806] was already fixed in
Reef v18.2.0, so v20.2.1 has it). The cause here is genuine slow-OSD latency.

```bash
ceph -s ; ceph health detail                    # find the slow OSD + blocked-op count
ceph tell osd.<N> dump_blocked_ops              # flag "waiting for readable", which pool
ceph osd down osd.<N>                            # re-peer the stuck OSD (client io ~0 => low risk)
#   or: kubectl -n rook-ceph delete pod -l ceph-osd-id=<N>   (restart the OSD pod)
```
Then remove the sustained write trigger (e.g. pause the sabnzbd queue). Longer-term: P2 (mClock
IOPS) + P0 (PLP drives). Source: docs.ceph.com `/dev/osd_internals/stale_read/`.

### Mon RocksDB store corruption (the SN770M failure mode)

Corruption presents as ceph-mon crash-looping with RocksDB `Corruption: error in middle of
record` / `missing files …/store.db/NNN.ldb` / `block checksum mismatch`. **Single mon:** delete
its deployment + PVC; Rook recreates it and re-syncs from quorum (see
[hardware-incidents.md](./hardware-incidents.md) and the mon-failover-deadlock runbook).

**All mons corrupted** (catastrophic) — the **only** supported recovery is to rebuild from
current OSD state; **never** restore from an old backup:
```bash
# on each OSD host:
ceph-objectstore-tool --data-path /var/lib/ceph/osd/ceph-<id> \
  --op update-mon-db --mon-store-path /tmp/mon-store
# then rebuild:
ceph-monstore-tool /tmp/mon-store rebuild -- \
  --keyring /etc/ceph/ceph.client.admin.keyring --mon-ids a b c
```
Caveat: the rebuild does **not** recover the CephFS FSMap or non-OSD keyrings — CephFS needs
separate recovery (recreate FS with the recovery flag, set joinable, reapply
`standby_count_wanted`). Since this cluster uses CephFS RWX, pre-stage that runbook.
Sources: docs.ceph.com `/rados/troubleshooting/troubleshooting-mon/` +
`/cephfs/recover-fs-after-mon-store-loss/`.

---

## References

- [`ceph-performance-review.md`](./ceph-performance-review.md) — 2026-06-01 deep review & rationale
- [`hardware-incidents.md`](./hardware-incidents.md) — hardware failure log (incl. SN770M firmware fix)
- [`ceph/`](./ceph/) — toolbox, PG, backup-recovery notes
- Deep-research pass (2026-06-14): ceph-csi mounter docs, Ceph mClock/PG/lease/mon docs,
  Talos `siderolabs/pkgs` kernel config. Talos blog (oneuptime 2026-03-03) loads only the
  `rbd` module + a `/var/lib/rook` rshared bind-mount — note `rbd` is **built-in (`=y`) on
  Talos 1.13**, so that `machine.kernel.modules` entry is unnecessary on this version.
