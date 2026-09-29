# talos-3 placement ledger

talos-3 is the only node with `devic.es/b70` (capacity 99; 0 on talos-1 and talos-2). It also hosts two of the six Ceph OSDs, a mon, and the `postgres-17` instance pinned one-per-node by anti-affinity. The general fleet may land here only as overflow.

## The taint

Git and the live node (re-read before trusting this sentence):

```yaml
nodeTaints:
  home-operations.com/dedicated: PreferNoSchedule
```

`PreferNoSchedule` is a score penalty, `TaintToleration` weight 3 on stock kube-scheduler. Untolerating pods can still schedule here when other scores win, and they are expected to during a drain of talos-1 or talos-2. They return on their next rollout. A hard `NoSchedule` was rejected (PR #1798): once the general fleet has rolled off, draining either other node leaves cert-manager, CoreDNS, Authentik and the Rook operator with nowhere to go while talos-3 has free cores it cannot receive them into. tuppr's upgrade drains have the same window.

The taint is Talos-owned. Merging the template does nothing until `just talos apply-node talos-3`. A `kubectl taint` edit lasts until the next machine-config apply or kubelet restart. `NoSchedule` does not evict pods that are already running; neither does `PreferNoSchedule`.

A toleration matches only when `effect` equals the taint effect. Every toleration for this key uses `effect: PreferNoSchedule` and `operator: Exists`. Changing the effect is one change across the template and every toleration.

## Who tolerates

These belong on talos-3. Do not copy the toleration onto a workload that merely prefers the node.

| Class | Where the toleration is set |
|---|---|
| GPU: `ai/vllm`, `ai/embedding-gpu`, `media/tdarr` node | each HelmRelease |
| Ceph mgr, mon, OSDs | `CephCluster.spec.placement.all` |
| Rook operator `discover` DaemonSet | operator HelmRelease |
| `postgres-17` | `database/cloudnative-pg/cluster-17/cluster-17.yaml` |
| `generic-device-plugin`, intel GPU plugin, promtail | each HelmRelease |
| rbd and cephfs CSI nodeplugins | `operator/csi-driver-tolerations.yaml` |
| multus | JSON6902 postRenderer on the multus HelmRelease |

The CSI file is a partial `Driver` manifest. Server-side apply field-merges `spec.nodePlugin.tolerations` into the operator-owned object. Its Kustomization `prune: disabled` is load-bearing: pruning would delete the operator's Driver. Do not delete that file.

The multus chart hardcodes DaemonSet tolerations and `mergeOverwrite`s them, so `values` tolerations are discarded. The postRenderer is the only in-repo path. `helm template` is how you confirm a values override is not silently dropped.

MDS and RGW do not tolerate the taint. They are not belongs-there workloads.

## Honor is hygiene, not current protection

`topologySpreadConstraints[].nodeTaintsPolicy: Honor` uses the scheduler's DoNotSchedule taint filter, which matches `NoSchedule` and `NoExecute` only. Against `PreferNoSchedule` it does not remove talos-3 from the spread domain.

Those Honor entries were the fix while the taint was hard `NoSchedule`: the default `Ignore` kept talos-3 in the domain, and `whenUnsatisfiable: DoNotSchedule` then refused to place MDS and Dragonfly anywhere. Leave Honor in place so a future hard taint does not recreate that outage. Do not add Honor expecting it to keep a pod off talos-3 today, and do not delete it as dead.

## What will not move pods off

`kube-system/descheduler` enables `RemovePodsViolatingNodeTaints` and does not set `includePreferNoSchedule`, so this taint is not an eviction reason. `LowNodeUtilization` thresholds are cpu 20, memory 20, pods 20 (targets 60). A node under 20% is the only source. Do not expect the descheduler to rebalance talos-3.

## Remeasure, do not quote old percents

Committed-percent writeups of this node disagree with each other because the request set changed underneath them. Before adding a pod or raising a request here, read `kubectl describe node talos-3` allocated versus capacity, and remember a CPU request is weight under contention on this node, not proof of idle elsewhere (skill `node-scheduling` sizing method).

vLLM's memory request and the retracted `--no-mmap` lever are skill `b70-llm-serving`. OSD request 12Gi, limit 14Gi, and why `osd_memory_target` is not that request, are skill `rook-ceph`.
