# What Prometheus has

No xpu-smi, Level Zero, or DCGM-style exporter. Per-engine busy
percent and clocks are not queryable for either GPU. VRAM is: see
`system/xe-gpu-exporter` below.

## VRAM, `system/xe-gpu-exporter`

DaemonSet that sums `drm-total-vram0` from `/proc/<pid>/fdinfo` per card
and per pod. `hostPID` plus `CAP_SYS_PTRACE`; no host mounts. It counts
everything the client holds in VRAM: weights, KV cache, compute buffers.

| Metric | Labels |
|---|---|
| `xe_gpu_vram_used_bytes`, `xe_gpu_gtt_used_bytes`, `xe_gpu_clients` | `pdev` |
| `xe_gpu_vram_total_bytes` | `pdev`; from `XE_VRAM_TOTALS_MIB` in the HelmRelease |
| `xe_gpu_process_vram_bytes`, `xe_gpu_process_gtt_bytes` | `pdev`, `pod_uid`, `comm` |
| `xe_gpu_exporter_up` | none |

The total is configured, not read: xe exposes none, and the BAR is 256 MiB
without Resizable BAR. B70 `0000:03:00.0` is 32656 MiB (Level Zero). A pod
name needs a join: `... * on(pod_uid) group_left(namespace, pod)
label_replace(kube_pod_info, "pod_uid", "$1", "uid", "(.*)")`. iGPU clients
report 0 VRAM (shared memory). `xe_gpu_exporter_up 1` with
`xe_gpu_clients 0` while chat is serving means the scan lost `hostPID` or
`SYS_PTRACE`. Gate: `scripts/ci/xe-gpu-exporter-test.py`.

## B70, talos-3 only

`xe` hwmon chip `0000:02:01_0_0000:03:00_0`.

| Signal | Metric | Notes |
|---|---|---|
| Package temp | `node_hwmon_temp_celsius{sensor="temp2"}` | |
| VRAM temp | `node_hwmon_temp_celsius{sensor="temp3"}` | Temperature, not utilization |
| Fan | `node_hwmon_fan_rpm{sensor="fan1"}` | |
| Power cap / crit | `node_hwmon_power_cap_watt`, `node_hwmon_power_crit_watt`, sensor `power1` | Static config, not live draw |
| Live watts | `rate(node_hwmon_energy_joule_total{sensor=~"energy1|energy2"}[5m])` | `energy1` card, `energy2` package. No direct power gauge |

Filter `instance="talos-3"` and that chip. Dashboard:
`kubernetes/apps/base/ai/gpu-node-dashboard/app/gpu-node.json`.
The `xe` driver publishes no VRAM used/total counters in sysfs; the
exporter above is the source for those. The dashboard's VRAM row uses it.

## iGPU, all three nodes

No hwmon chip. The only series are kube-state-metrics:

- `kube_node_status_allocatable{resource="gpu_intel_com_xe"}`
- `kube_pod_container_resource_requests{resource="gpu_intel_com_xe"}`

`gpu-loss.yaml` alerts on the allocatable series and documents
`allowIDs: "0xa7a0"`. Allocation is not temperature and not utilization.
