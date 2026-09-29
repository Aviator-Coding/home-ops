# What Prometheus has

No xpu-smi, Level Zero, or DCGM-style exporter. Per-engine busy
percent, VRAM utilization, and clocks are not queryable for either GPU.

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
The `xe` driver publishes no VRAM used/total counters in sysfs, so a
panel cannot show the 26909 / 32656 MiB split. That figure is from a
one-shot read during serving work (skill `b70-llm-serving`).

## iGPU, all three nodes

No hwmon chip. The only series are kube-state-metrics:

- `kube_node_status_allocatable{resource="gpu_intel_com_xe"}`
- `kube_pod_container_resource_requests{resource="gpu_intel_com_xe"}`

`gpu-loss.yaml` alerts on the allocatable series and documents
`allowIDs: "0xa7a0"`. Allocation is not temperature and not utilization.
