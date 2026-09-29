# DRA stays unshipped

Evaluated against `intel/intel-resource-drivers-for-kubernetes` and
rejected. The cluster can serve `resource.k8s.io/v1`
(`DynamicResourceAllocation` and `DRAConsumableCapacity` are on). The
driver finds both `0xe223` and `0xa7a0` by scanning sysfs. Neither fact
is the blocker.

## Blockers

1. The driver README on `main` says, verbatim: "CAUTION: This is a beta
   / non-production software, do not use on production clusters."
2. It cannot share one GPU. Upstream issue #79: strict 1-to-1 or
   SR-IOV. `ai/vllm` and `media/tdarr-node` both need the single B70.
   1-to-1 leaves one of them `Pending`. More iGPUs do not fix that.
   Today's plugins hand out share-count tokens (`count: 99`).
3. No DRA-equivalent series exists for the gpu-loss alerts. That gates
   the last cutover stage, not the decision to wait.

`adminAccess: true` plus `allocationMode: All` is the combination that
lets more than one pod onto a device with this driver. Intel documents
it for monitor deployments. Those allocations are not counted as
consumed, and every GPU namespace would need
`resource.k8s.io/admin-access: "true"`. Do not use it.

Collapsing both B70 consumers into one namespace to share a
`ResourceClaim` means moving `tdarr-node` out of `media`. That is a
larger change than the migration, for a worse result.

## Reopen when both are true

1. The CAUTION line is gone, or Intel states production support. A
   v1.0.0 GPU release is the obvious signal.
2. Issue #79 is implemented: devices publish
   `allowMultipleAllocation: true`. The cluster side is already ready
   (`DRAConsumableCapacity`, and the driver already publishes
   `millicores: 1k` per device).

Check those upstream and stop. Do not start a cutover on a partial
match. Alert migration can follow stages 1-4. It does not have to
precede them.

## What stays until then

`devic.es/b70` for Level Zero, `devic.es/b70-vaapi` for VA-API,
`gpu.intel.com/xe` with `allowIDs: "0xa7a0"` for the iGPU. The
`allowIDs` pin has shipped. It is not a pending follow-up. The gpu-loss
alerts stay valid while the plugins stay deployed.
