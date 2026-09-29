---
name: intel-gpu
description: "Read before any change touching the cluster's Intel GPUs: generic-device-plugin groups or mountPaths, the devic.es/b70 and devic.es/b70-vaapi resources, gpu.intel.com/xe, any workload that transcodes or runs inference on a GPU, or a Grafana panel/alert over GPU metrics. Covers the DRM device-node rename that silently kills VA-API and what GPU telemetry Prometheus does and does not have."
---

# Intel GPUs: devices, VA-API, telemetry

One discrete Arc Pro B70 on talos-3 (PCI `0000:03:00.0`, `8086:e223`) plus
a Raptor Lake iGPU (`0xa7a0`) on each node. llama.cpp flags, the NaN
probe, and the embedding rate curve are skill `b70-llm-serving`. This
skill is the device and the metrics.

## Tripwires

1. **Never rename a DRM node with `generic-device-plugin` `mountPath`.**
   The `b70` group exposes the card as `card0` / `renderD128` for Level
   Zero. libdrm ignores that path, `fstat`s the fd, and reopens the
   kernel `DEVNAME` (`dri/renderD129`). VA-API then fails before a
   driver loads. Level Zero stays green, so inference looks healthy
   while transcoding is dead. VA-API consumers use `devic.es/b70-vaapi`
   (`card1` / `renderD129`). Allocatable capacity is not proof.
   [devices.md](references/devices.md)
2. **Device IDs are `sha1(count + every host path in the group)`.**
   Adding a path to `b70` changes all 99 IDs and drops kubelet's live
   allocations. Add a group instead of editing that one.
3. **The device-plugin config is a `subPath` mount.** Kubelet never
   refreshes it. The `configMapGenerator` name-suffix hash in
   `kubernetes/apps/base/system/generic-device-plugin/app/kustomization.yaml`
   must stay enabled. A comment-only edit of `config/config.yaml` still
   rolls the DaemonSet. Do not touch that file to tidy prose.
4. **No per-engine utilization exporter.** Prometheus has the B70 `xe`
   hwmon chip (temps, fan, energy counters) and kube-state-metrics
   allocation for the iGPU. The iGPU has no hwmon chip. There is no
   VRAM-utilization gauge and no `xpu-smi` equivalent.
   [telemetry.md](references/telemetry.md)
5. **Do not migrate GPU scheduling to DRA, and never via
   `adminAccess: true`.** The Intel driver README says it is beta and
   not for production, and it cannot share one GPU (upstream issue #79,
   strict 1-to-1 or SR-IOV). `vllm` and `tdarr-node` both need the one
   B70. Reopen only when the CAUTION line is gone **and** #79 ships
   `allowMultipleAllocation: true`. [dra.md](references/dra.md)
6. **`allowIDs: "0xa7a0"` is already set** on the Intel GPU plugin
   (`kubernetes/apps/base/system/intel-device-plugin-operator/gpu/helmrelease.yaml`).
   It scopes `gpu.intel.com/xe` to the iGPU. Do not re-add it, and do
   not drop it while the B70 is also an `xe` device.
7. **Do not buy a second B70** on the current recommendation. The buy-if
   is a >32 GB MoE in production **and** talos-3 seating that card at
   PCIe x8 or wider, or llm-scaler#382 closing so vLLM-XPU prefill is a
   hard requirement. The chassis question (second OCuLink or x8/x8
   bifurcation) is still open. [second-card.md](references/second-card.md)

## Where things live

| What | Path |
|---|---|
| `devic.es/b70` and `devic.es/b70-vaapi` | `kubernetes/apps/base/system/generic-device-plugin/app/config/config.yaml` |
| iGPU plugin (`allowIDs`) | `kubernetes/apps/base/system/intel-device-plugin-operator/gpu/helmrelease.yaml` |
| B70 hwmon dashboard | `kubernetes/apps/base/ai/gpu-node-dashboard/app/gpu-node.json` |
| iGPU loss alert | `kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts/gpu-loss.yaml` |
| VA-API check after a GPU change | [vaapi-check.md](references/vaapi-check.md) (skill `tdarr-transcoding` consumes it) |
| B70 dock power order, `pcie_port_pm=off` | skill `talos-nodes` |

Those system and monitoring files are not this domain's to tidy. The
invariants above are why a comment edit there is not free.

## Procedures

- Device groups: [devices.md](references/devices.md).
- VA-API check after a GPU change: [vaapi-check.md](references/vaapi-check.md).
- What a panel can query: [telemetry.md](references/telemetry.md).
- Why DRA stays unshipped: [dra.md](references/dra.md).
- Second card: [second-card.md](references/second-card.md).
- Serving baseline the device was sized around: [baseline.md](references/baseline.md).

## Verify

- After any device-plugin or kernel change, run the VA-API check in
  [vaapi-check.md](references/vaapi-check.md) on `tdarr-node`. `kubectl describe node` showing
  `devic.es/b70` allocatable is the Level Zero path only.
- `scripts/ci/igpu-xe-allowids-test.py` pins `allowIDs` and the consumer
  maps. A retired GPU app must leave that map in the same change.
- Read-only: B70 energy is
  `rate(node_hwmon_energy_joule_total{instance="talos-3", chip="0000:02:01_0_0000:03:00_0", sensor="energy1"}[5m])`.
  Absence of a VRAM gauge is expected.
