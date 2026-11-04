# Ceph decisions that are still in force

Each one is a property of the current manifests. Git history holds the incident writeups.

## CephX (PR #1398)

`cephClusterSpec.security.cephx.daemon` is `keyType: aes256k`, `keyRotationPolicy: KeyGeneration`. Live status: osd and mgr report `keyType: aes256k` at `keyGeneration: 2`. `csi` is still `keyGeneration: 1` on Ceph 19.2.3 with no `aes256k`.

- Never set `security.cephx.csi.keyType: aes256k`. The `csi-*` keys are used by krbd and the CephFS kernel client. `aes256k` needs Linux >= 7.0. Talos 1.14.5 ships kernel 6.18.58. Rotating those keys breaks every RBD map and CephFS mount.
- `keyGeneration` is CRD-validated `self >= oldSelf`. Removing the block does not un-rotate. Rollback only stops the next rotation.
- `keyType` is pruned by a CRD older than Rook 1.20.5. Applying an `aes256k` rotation on an older Rook silently burns a generation as an `aes` rotation. Read `kubectl -n rook-ceph get cephcluster rook-ceph -o jsonpath='{.status.cephx}'` before bumping the generation.
- Residual health: four sticky mutes are set live (2026-11-04): `AUTH_EMERGENCY_CIPHERS_SET`, `AUTH_INSECURE_CLIENT_KEY_TYPE`, `AUTH_INSECURE_KEYS_ALLOWED`, `AUTH_INSECURE_KEYS_CREATABLE` (`ceph health mute <code> --sticky`). They cover the csi keys and `client.rbd-mirror-peer` still on `aes`, and the Rook-set `mon_auth_allow_insecure_key` / `mon_auth_emergency_allowed_ciphers` that keep those keys usable. Never turn those two options off and never treat the mutes as a reason to rotate. Unmute after the kernel >= 7.0 csi rotation. The `CephCluster` `.status.ceph` still lists muted checks as `HEALTH_WARN`; read `ceph health detail` for the real state.
- Next daemon rotation: set `keyGeneration` to current+1. A same-type csi generation bump is a separate change and needs `keepPriorKeyCountMax` for a soft cutover.

## SigV4 and the quay.io/ceph automerge hole (PR #1401, flag cleared on v20.2.5)

`cephConfig.client.rgw.rgw_sigv4_insecure` is explicitly `"false"`. v20.2.4's CVE-2026-54330 fix rejected any header absent from `SignedHeaders` and wrongly required `content-type` to be signed. minio-go's streaming signer never signs `content-type`, so every minio-go and restic `PutObject` returned 403 while GET and HEAD kept working, with Ceph `HEALTH_OK`. The access-log line is `op=put_obj` `http_status=403` with the user unresolved (`-`). PR #1401 set the flag to `"true"`, which also disabled the `x-amz-*` check that is the real CVE mitigation.

v20.2.5 contains ceph/ceph#71192 (Tentacle backport #71364), so the flag went back to `"false"`. Keep the key rather than deleting it: Rook only sets `cephConfig` keys and does not remove a dropped key from the mon DB. Proof on a change: an unsigned `content-type` PUT returns 200 and an unsigned `x-amz-*` header returns 403. If a future Ceph image brings the PUT 403s back, setting `"true"` again is the stop-gap, not a fix.

`quay.io/ceph/ceph` is not in the Rook chart group and has no `automerge: false` rule. `.renovate/overrides.json5` only limits it to stable `x.2.z` tags (`/^v?\d+\.2\.\d+$/`). Do not put that regex on `ghcr.io/rook/ceph`. Branch automerge is closed (`automergeType` is Renovate's default `pr`). The generic "auto merge patch updates" rule still matches this image, so a stable patch can merge on green CI, and CI does not exercise RGW PUTs. After a Ceph bump, check the RGW log for `PUT` status codes. `task rook:reset-disk` reads its image from the HelmRelease `cephImage`, so the wipe Job needs no separate bump. Adding a never-automerge rule is a `.renovate/**` edit (skill `renovate`); do not fold it into an unrelated Rook PR, and do not merge a Renovate config change without suspending the GitHub Actions Renovate workflow first.

## Reclaim

`rbd du` counts full 4 MiB objects still marked EXISTS. krbd discards at 64 KiB, so holes free BlueStore extents without updating the object map. The fast-diff versus exact gap also exists on images that were never trimmed. Only `rbd du --exact`, and only a same-instrument before/after, proves the weekly `fstrim` reclaimed space. `fstrim` sees mounted claims only. `mountOptions: [discard]` on `ceph-block` was evaluated and left out.

## Resources and QoS

- OSD container: request 12Gi, limit 14Gi (PR #1736). `osd_memory_target` 10 GiB is the BlueStore cache target, not total RSS. Highest observed OSD working set is 9317 Mi, measured mid-degradation. Do not cut the request to 10Gi. OSDs stay Burstable (no cpu limit). Do not promote them to Guaranteed.
- Mon and logcollector stay Guaranteed (`requests.memory` equals `limits.memory`). That is eviction protection, not an oversight.
- Rook operator: the chart default `resources.limits.memory: 512Mi` still applies when values set `limits: {}`. The HelmRelease sets an explicit `limits.memory: 1Gi` because the operator was OOMKilled at ~455 Mi during a six-OSD reconcile. Do not "clear" a chart limit with an empty map.
- MDS anti-affinity is preferred. Required anti-affinity would pin a rank unschedulable during a drain.

## Modules, pools, classes

- Mgr modules `rook` and `nfs` stay disabled. On v20.2.3 `rook` crash-looped through the prometheus mgr module (`rook/rook#18124`, reopened, unfixed in Rook). v20.2.5 ships the Ceph-side fixes (`ceph/ceph#71041`, `#71180` via backport `#72275`), so enabling is possible, but nothing here consumes the orchestrator. Enable it only for a consumer, and watch `ceph crash ls-new` after. `nfs` has no consumer and needs `rook`; enable both together or neither.
- The mgr dashboard stays removed (PR #1394). Do not re-enable it to satisfy a Gatus check that was pointed at a dead Service.
- No `deviceClass` on pool `data0`. All OSDs are one class. A deviceClass put data0 on a shadow CRUSH root while every other pool used the default root, and the autoscaler then skipped every pool.
- StorageClass parameters are immutable. Changing one means a new class and a migrate, not a helm values edit. `ceph-block` still says `compression_mode: aggressive`. That tags new RBD images only. The pool's `compression_mode: none` is what governs. Do not edit the class to match the pool.
- `osdMaxUpdatesInParallel` is `spec.storage.osdMaxUpdatesInParallel`. A key at `spec.osdMaxUpdatesInParallel` is pruned. It is pinned to 1 (CRD default 20).
- Ceph PrometheusRules that count events use `increase()`. A raw counter compare (`> 5`) latches after the fifth event ever.
- The cluster overlay Kustomization keeps `prune: false` where it is set so Flux cannot delete a Ceph CR that still owns disks.
- CSI controller-plugin affinity is not settable from this chart. ceph-csi-operator owns the Driver CRs through an internal `ceph-csi-drivers` Helm release. Do not hunt for a values key, and do not apply a second Driver that owns more than `spec.nodePlugin.tolerations`.
- `csi.serviceMonitor.enabled` stays false. After the operator move no pod carries `app=csi-metrics`, and the Driver CRD has no metrics field. Turning it on selects zero targets.

## PG notes worth keeping

PG count is autoscaled (`bulk: true` on the data pools). A decrease is supported by the autoscaler. Do not hand-set `pg_num` to chase a target. Confirm with `ceph osd pool autoscale-status` before declaring the autoscaler stuck. There is no dedicated replication network; that idea was researched and not applied.
