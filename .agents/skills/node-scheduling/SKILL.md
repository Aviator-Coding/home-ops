---
name: node-scheduling
description: "Read before changing any container requests or limits, tolerations, affinity or topology spread, placing a workload on talos-3, or triaging CPU throttling, OOMKills or evictions. Covers limit-without-request reserving the whole limit, CFS quota and peak sampling, Guaranteed QoS demotion, limits: {} not clearing a chart default, and the talos-3 PreferNoSchedule taint."
---

# Node scheduling: requests, limits, QoS and talos-3 placement

## Tripwires

1. **A missing `requests.memory` reserves the whole `limits.memory`.** Kubernetes defaults the request to the limit before anything can read the Pod, so the live object looks identical to a deliberate Guaranteed one. Audit the Git manifest. Gate: `scripts/ci/memory-request-declared-test.py`. [sizing-method.md](references/sizing-method.md)
2. **A CPU limit is a CFS quota per 100ms period, not a share of the node.** Node idle does not mean the container is unthrottled. Throttling does not crash, restart, fail a probe, or OOM. Mean usage under a limit is the quota talking. Do not size a request from a coarse `max_over_time(rate(...[5m]))`. Gate: `scripts/ci/cpu-throttle-contract-test.py`.
3. **Cutting a request or raising only the limit on a `requests == limits` container demotes it from Guaranteed to Burstable.** Talos `OOMController` skips Guaranteed cgroups. k8tz, the Rook mons and the Rook log collector hold equality on purpose (skill `rook-ceph`, skill `system-namespaces`).
4. **`limits: {}` does not unset a chart default.** Helm merges the empty map as a no-op. Read the chart's own `values.yaml` before trusting it. The Rook operator is the worked case (skill `rook-ceph`).
5. **talos-3's taint is `home-operations.com/dedicated: PreferNoSchedule`.** It is a scheduler score penalty (`TaintToleration` weight 3), not a filter. A hard `NoSchedule` strands a drain of talos-1 or talos-2 once the general fleet has rolled off. A toleration matches only when its `effect` equals the taint's. [talos-3-ledger.md](references/talos-3-ledger.md)
6. **`nodeTaintsPolicy: Honor` does not see `PreferNoSchedule`.** It filters `NoSchedule` and `NoExecute` only. Leave the Honor entries; do not read them as keeping pods off talos-3.
7. **Descheduler will not evict `PreferNoSchedule` violators.** `RemovePodsViolatingNodeTaints` is on and `includePreferNoSchedule` is unset. Pods leave talos-3 on their own next rollout. `LowNodeUtilization` thresholds are 20, so it does not rebalance this node.
8. **Do not place a new workload on talos-3, raise a request there, or drop a toleration without remeasuring.** Old committed-percent figures contradict each other. Remeasure. vLLM and embedding memory belong to skill `b70-llm-serving`. OSD request and `osd_memory_target` belong to skill `rook-ceph`.

## Where things live

| What | Path |
|---|---|
| Taint | `talos/nodes/talos-3.yaml.j2` (`nodeTaints`) |
| Ledger and who tolerates | [talos-3-ledger.md](references/talos-3-ledger.md) |
| Request, limit, QoS method | [sizing-method.md](references/sizing-method.md) |
| Descheduler policy | `kubernetes/apps/base/kube-system/descheduler/app/helmrelease.yaml` |
| CSI nodeplugin tolerations | `kubernetes/apps/base/rook-ceph/rook-ceph/operator/csi-driver-tolerations.yaml` |
| Multus toleration (chart hardcodes its own) | `kubernetes/apps/base/kube-system/multus/app/helmrelease.yaml` |
| Memory and CPU gates | `scripts/ci/memory-request-declared-test.py`, `scripts/ci/cpu-throttle-contract-test.py` |

## Procedures

- Change a request or limit: [sizing-method.md](references/sizing-method.md).
- Add, remove, or retune talos-3 placement: [talos-3-ledger.md](references/talos-3-ledger.md). The taint reaches the node only through `just talos apply-node talos-3` (skill `talos-nodes`).

## Verify

- `python3 scripts/ci/memory-request-declared-test.py` and `python3 scripts/ci/cpu-throttle-contract-test.py`.
- Live taint: `kubectl get node talos-3 -o jsonpath='{.spec.taints}'` prints `PreferNoSchedule` / `home-operations.com/dedicated`.
- A spread or toleration you edited is on the live Pod, not only in Helm values. A key the chart never renders is not protection.
