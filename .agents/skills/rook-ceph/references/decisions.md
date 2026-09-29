# Ceph decisions that are still in force

Each one is a property of the current manifests. Git history holds the incident writeups.

## CephX (PR #1398)

`cephClusterSpec.security.cephx.daemon` is `keyType: aes256k`, `keyRotationPolicy: KeyGeneration`. Live status: osd and mgr report `keyType: aes256k` at `keyGeneration: 2`. `csi` is still `keyGeneration: 1` on Ceph 19.2.3 with no `aes256k`.

- Never set `security.cephx.csi.keyType: aes256k`. The `csi-*` keys are used by krbd and the CephFS kernel client. `aes256k` needs Linux >= 7.0. Talos 1.14.1 ships kernel 6.18.51. Rotating those keys breaks every RBD map and CephFS mount.
- `keyGeneration` is CRD-validated `self >= oldSelf`. Removing the block does not un-rotate. Rollback only stops the next rotation.
- `keyType` is pruned by a CRD older than Rook 1.20.5. Applying an `aes256k` rotation on an older Rook silently burns a generation as an `aes` rotation. Read `kubectl -n rook-ceph get cephcluster rook-ceph -o jsonpath='{.status.cephx}'` before bumping the generation.
- Residual health: `AUTH_INSECURE_*` client-key warnings can be muted while the cluster is `HEALTH_OK`. That mute is the csi keys still on `aes`, not a reason to rotate them.
- Next daemon rotation: set `keyGeneration` to current+1. A same-type csi generation bump is a separate change and needs `keepPriorKeyCountMax` for a soft cutover.

## SigV4 and the quay.io/ceph automerge hole (PR #1401)

`cephConfig.client.rgw.rgw_sigv4_insecure: "true"` is load-bearing on v20.2.4. That release's CVE-2026-54330 fix rejects any header absent from `SignedHeaders` and wrongly requires `content-type` to be signed. minio-go's streaming signer never signs `content-type`, so every minio-go and restic `PutObject` returns 403 while GET and HEAD keep working. Ceph stays `HEALTH_OK`. The access-log line is `op=put_obj` `http_status=403` with the user unresolved (`-`).

The flag also disables the `x-amz-*` check that is the real CVE mitigation. Remove it only together with a `cephImage.tag` bump to a Tentacle build that contains ceph/ceph#71192, then prove an unsigned `content-type` PUT returns 200 and an unsigned `x-amz-*` header returns 403.

`quay.io/ceph/ceph` is not in the Rook chart group and has no `automerge: false` rule. `.renovate/overrides.json5` only limits it to stable `x.2.z` tags (`/^v?\d+\.2\.\d+$/`). Do not put that regex on `ghcr.io/rook/ceph`. Branch automerge is closed (`automergeType` is Renovate's default `pr`). The hole that remains: the generic "auto merge patch updates" rule still matches this image, so v20.2.4 to the next stable patch can merge a green PR and leave `rgw_sigv4_insecure: "true"` in place. A Ceph image bump is not done until the flag decision is in the same commit. Adding a never-automerge rule is a `.renovate/**` edit (skill `renovate`); do not fold it into an unrelated Rook PR, and do not merge a Renovate config change without suspending the GitHub Actions Renovate workflow first.

## Reclaim

`rbd du` counts full 4 MiB objects still marked EXISTS. krbd discards at 64 KiB, so holes free BlueStore extents without updating the object map. The fast-diff versus exact gap also exists on images that were never trimmed. Only `rbd du --exact`, and only a same-instrument before/after, proves the weekly `fstrim` reclaimed space. `fstrim` sees mounted claims only. `mountOptions: [discard]` on `ceph-block` was evaluated and left out.

## Resources and QoS

- OSD container: request 12Gi, limit 14Gi (PR #1736). `osd_memory_target` 10 GiB is the BlueStore cache target, not total RSS. Highest observed OSD working set is 9317 Mi, measured mid-degradation. Do not cut the request to 10Gi. OSDs stay Burstable (no cpu limit). Do not promote them to Guaranteed.
- Mon and logcollector stay Guaranteed (`requests.memory` equals `limits.memory`). That is eviction protection, not an oversight.
- Rook operator: the chart default `resources.limits.memory: 512Mi` still applies when values set `limits: {}`. The HelmRelease sets an explicit `limits.memory: 1Gi` because the operator was OOMKilled at ~455 Mi during a six-OSD reconcile. Do not "clear" a chart limit with an empty map.
- MDS anti-affinity is preferred. Required anti-affinity would pin a rank unschedulable during a drain.

## Modules, pools, classes

- Mgr modules `rook` and `nfs` stay disabled. `rook` crash-looped and blocked tuppr. `nfs` has no consumer.
- The mgr dashboard stays removed (PR #1394). Do not re-enable it to satisfy a Gatus check that was pointed at a dead Service.
- No `deviceClass` on pool `data0`. Adding one rewrites CRUSH.
- StorageClass parameters are immutable. Changing one means a new class and a migrate, not a helm values edit.
- `osdMaxUpdatesInParallel` is `spec.storage.osdMaxUpdatesInParallel`. A key at `spec.osdMaxUpdatesInParallel` is pruned. It is pinned to 1 (CRD default 20).
- Ceph PrometheusRules that count events use `increase()`. A raw counter compare (`> 5`) latches after the fifth event ever.
- The cluster overlay Kustomization keeps `prune: false` where it is set so Flux cannot delete a Ceph CR that still owns disks.

## PG notes worth keeping

PG count is autoscaled (`bulk: true` on the data pools). A decrease is supported by the autoscaler. Do not hand-set `pg_num` to chase a target. Confirm with `ceph osd pool autoscale-status` before declaring the autoscaler stuck. There is no dedicated replication network; that idea was researched and not applied.
