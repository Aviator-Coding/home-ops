# talos-3 scheduling truth

All figures measured 2026-09-14 against the live cluster and Prometheus
(`max_over_time(container_memory_working_set_bytes[30d])` for peaks). Node
allocatable is **93604 Mi (91.4 GiB)** on every node.

## 1. The problem: a request that looked harmless was the actual danger

`ai/vllm` requested **16384 Mi** and its 30-day peak working set is **39342 Mi**
- over its request by **21230 Mi**.

That matters because of how the kubelet ranks pods for node-pressure eviction.
It sorts by, in order: whether the pod's usage **exceeds its requests**, then
pod **priority**, then **how far usage exceeds requests**. Every workload on
talos-3 sits at priority 0 with no priorityClassName, so the third key decides,
and the ranking on talos-3 looked like this:

| pod | usage | request | over |
|---|---:|---:|---:|
| `ai/vllm` | 37614 Mi | 16384 Mi | **+21230 Mi** |
| `kube-system/kube-apiserver-talos-3` | 2597 Mi | 512 Mi | +2085 Mi |
| `flux-system/konflate` | 437 Mi | 256 Mi | +181 Mi |
| `monitoring/loki-0` | 433 Mi | 256 Mi | +177 Mi |
| `selfhosted/n8n` | 369 Mi | 200 Mi | +169 Mi |

kube-apiserver is a static pod at `system-node-critical`, so among pods the
kubelet would actually evict, **vllm's overage was 117x the next one**. If
anything on talos-3 spiked, the LLM went first - and it would be killed long
before reaching its own 48Gi limit. The container limit was never the binding
risk; the request was.

**The fix for this is the request itself.** Because the kubelet's *first* sort
key is "does usage exceed requests", raising the request above actual usage does
not merely move vllm down the list - it removes it from the exceeds-set entirely
and puts it in the last-evicted group. Nothing else needs to change to get that
property.

## 2. Why the truth could not simply be declared

An honest request did not fit. talos-3 committed **75654 Mi (80.8%)**, and
raising vllm 16384 -> 40960 Mi adds 24576 Mi, which is 107.1% of the node. The
pod would have gone `Pending`, and `ai/vllm` uses `strategy: Recreate`, so the
old pod is terminated *before* the new one is created - the LLM would have been
down, not merely un-upgraded. Load had to be shed first.

### What cannot move, and why

`devic.es/b70` (the Arc Pro B70) has capacity **99 on talos-3 and 0 on talos-1
and talos-2**, so vllm cannot be placed anywhere else. That eliminates the
entire "move the LLM" family of solutions. `media/tdarr-tdarr-node` holds
`devic.es/b70-vaapi` and is pinned for the same reason.

Verified immovable, totalling **57794 Mi** before the vllm change:

| what | request | why it cannot move |
|---|---:|---|
| 2x Ceph OSD | 29184 Mi | `kubernetes.io/hostname` nodeSelector; own local NVMe |
| `rook-ceph-mon-h` | 2304 Mi | hostname nodeSelector + `openebs-hostpath` local PV |
| `database/postgres-17-1` | 4096 Mi | CNPG `instances: 3` + `podAntiAffinityType: required` on hostname - one instance per node, in a 3-node cluster |
| `database/nats-2` | 1024 Mi | `cluster.replicas: 3` + `maxSkew: 1` hostname spread - one per node |
| DaemonSets | 4048 Mi | one pod per node by definition |
| static control plane | 832 Mi | `kube-apiserver`/`-controller-manager`/`-scheduler` |
| GPU-pinned (`vllm`, `tdarr-node`) | 16896 Mi | `devic.es/b70*`, talos-3 only |

The **OSD reservation is not slack**, though its current usage suggests it. The
cluster sets `osd_memory_target: 10 GiB` with the pod bound at 14Gi
(`rook-ceph/cluster/helmrelease.yaml`), so each OSD grows its BlueStore cache
toward 10 GiB over time; the 30-day peaks (4783 Mi and 7380 Mi) are a cache
still filling, not headroom to reclaim. Likewise `rook-ceph-mon` and the
`dragonfly` component deliberately set requests == limits for Guaranteed QoS,
and dragonfly's `--maxmemory=512Mi` means its 640Mi request is a ceiling
reservation for a cache that will fill. None of these are over-declarations.

The brief's leading candidate, `database/postgres-17-1` (4Gi), turned out to be
immovable for the reason above **and** already honest: its 4Gi comes from
`limits.memory` with no request declared, and its 30-day peak is 3454 Mi (84%).

## 3. What was actually wrong: three different lies, not one

The scheduler's view was wrong in both directions.

**Class A - limit-only declarations.** A container that declares
`limits.memory` and no `requests.memory` gets `requests.memory` defaulted to the
limit. 21 places in this repo do that, and the author's intent was plainly a
*ceiling*, not a *reservation*. Fixed here (limits untouched):

| container | reserved | 30-day peak | now requests |
|---|---:|---:|---:|
| `home-assistant/app` | 2048 Mi | 393 Mi | 768 Mi |
| `home-assistant/code-server` | 512 Mi | 148 Mi | 192 Mi |
| `envoy` (4 gateway pods) | 1024 Mi | 200 Mi | 256 Mi |
| `multus` (DaemonSet, 3 nodes) | 512 Mi | 7 Mi | 64 Mi |
| `cloudflare-tunnel/app` | 256 Mi | 62 Mi | 128 Mi |

**Class B - no request at all (29 containers cluster-wide, 3454 Mi).** See
section 5; this is real but small, and mostly not a talos-3 problem.

**Class C - under-declared workloads.** Several pods on the tightest node
consumed far more than they declared, which is invisible to the scheduler:
`downloads/radarr` 384 Mi requested against a 4036 Mi peak,
`flux-system/konflate` 256 Mi against 1542 Mi, `selfhosted/n8n` 200 Mi against
913 Mi, `monitoring/loki-0` 256 Mi against 625 Mi. Moving these off talos-3
frees their declared request *and* removes their real appetite from the node
with the least room.

## 4. What changed, and the arithmetic

Shed **5960 Mi** from talos-3:

| change | mechanism | freed |
|---|---|---:|
| 5 limit-only containers (Class A) | explicit `requests.memory`, limits unchanged | 2944 Mi |
| `radarr`, `linkwarden`, `flaresolverr`, `opencode`, `n8n` | `hostname NotIn talos-3` node affinity | 2120 Mi |
| `konflate` | same, chart-verified via `helm template` | 256 Mi |
| `rsshub-dragonfly-1` | per-instance `podAntiAffinity` (see below) | 640 Mi |

The relocation idiom is the one `downloads/sabnzbd` has used since 2026-06-27,
verified live to reach the pod spec verbatim. Confining these apps to
talos-1/talos-2 costs no real availability: the 3 Ceph mons are one per node, so
losing both other nodes already means no mon quorum and no `ceph-block` storage,
and none of them could run on talos-3 alone anyway.

The dragonfly change fixes a genuine HA defect found along the way:
`selfhosted/rsshub-dragonfly-0` and `-1` were **both on talos-3**, so the
replica bought nothing. `topologySpreadConstraints` cannot prevent this -
its `labelSelector` only matches pods in the same namespace, so it balances a
namespace's dragonfly pods collectively rather than an instance's replica pair,
and every per-namespace skew was a legal 1. A per-instance `podAntiAffinity`
is what actually separates them.

### Where that leaves the node

```
allocatable                              93604 Mi
committed, excluding ai/vllm entirely    53310 Mi
room available for ai/vllm               40294 Mi
ai/vllm 30-day peak working set          39342 Mi
```

| vllm request | node total | commit | margin | overage at 30-day peak |
|---|---:|---:|---:|---:|
| 34Gi | 88126 Mi | 94.1% | 5478 Mi | 4526 Mi |
| 36Gi | 90174 Mi | 96.3% | 3430 Mi | 2478 Mi |
| 38Gi | 92222 Mi | 98.5% | 1382 Mi | 430 Mi |
| 39Gi | 93246 Mi | 99.6% | 358 Mi | **0** |
| 40Gi | 94270 Mi | 100.7% | - | does not fit |

**This is the finding that matters.** Full eviction protection needs a request
at or above 39342 Mi, and the first value that clears it (39Gi) leaves the node
at 99.6%. A fully honest declaration and a usable node margin are mutually
exclusive on talos-3 as currently loaded - not because the shed was too timid,
but because the node's non-negotiable floor (2 OSDs at 29184 Mi, a mon, a CNPG
instance, a NATS instance, DaemonSets, the control plane) is **57794 Mi, 62% of
the node, before the LLM is placed at all**. Adding a 38.4 GiB LLM to that
leaves roughly 2 GiB of slack in a 91.4 GiB node.

## 5. The 29 unrequested containers

29 containers cluster-wide declare no memory request, totalling **3454 Mi** -
real hygiene, but a seventh of the single vllm misdeclaration, and **only
312 Mi of it is on talos-3** (`tuppr` 145, `loki-sc-rules` 78, `rook-discover`
21, `grafana-mcp` 19, `nats-2` 17, `nack` 16, `flux` 16). The bulk sits on
talos-1 and talos-2, which have 23765 Mi and 20866 Mi of uncommitted capacity.

**Deferred deliberately**, with one finding worth recording, because fixing it
correctly is a separate question from talos-3's capacity:

> **`MCPServer.spec.resources` does not reach the MCP server container.** Nine
> of the eleven `toolhive.stacklok.dev/v1alpha1` `MCPServer` manifests declare
> `spec.resources` (typically 128Mi request / 512Mi limit), and the ToolHive
> operator applies it to the **`toolhive` proxy Deployment** instead. The actual
> server runs in a separate StatefulSet whose `mcp` container has
> `resources: {}` - verified live on `ai/kubectl-0` and `ai/grafana-mcp`. So
> `ai/kubectl-0` is unbounded and unreserved, and was measured at **1196 Mi**,
> 2.3x the 512Mi limit its own manifest appears to set. It is the single
> largest of the 29.
>
> Closing this needs resources set under
> `spec.podTemplateSpec.spec.containers[name: mcp].resources` (that path does
> propagate - the live container carries its `args` and `env` from there), and
> it needs a decision about whether kubectl-mcp's 1.2 GiB is legitimate or a
> leak. Adding *requests* is safe; adding a *limit* below current usage would
> OOM-kill the container on the next reconcile.

The rest are chart-owned sidecars (`nats` prom-exporter/reloader, grafana's
`sc-dashboard`/`sc-datasources`, `loki-sc-rules`, `rook-discover`,
`kube-state-metrics`) plus `security/authentik-server` (710 Mi).

## 6. Two things a future reader should not re-derive

- **`ai/vllm`'s memory is anonymous, not reclaimable cache.** Measured
  `container_memory_rss` 37536 Mi against `container_memory_cache` **4 Mi**,
  because the server runs with `--no-mmap`. A request has to cover it; the
  kernel cannot reclaim it under pressure. Dropping `--no-mmap` would let
  llama.cpp mmap the GGUF so model pages become evictable page cache - that is
  an argument change and a separate captain decision, deliberately not made
  here, but it is the lever that would shrink the footprint rather than
  re-shuffle around it.
- **The descheduler's `LowNodeUtilization` plugin has never rebalanced
  anything.** Its `thresholds` are `cpu/memory/pods: 20`, and no node in this
  cluster has been under 20% memory, so it logs `No node is underutilized,
  nothing to do here, you might tune your thresholds further` and evicts 0 on
  every 5-minute pass while correctly classifying talos-3 as `overutilized`.
  Do not expect it to relieve talos-3. Its
  `RemovePodsViolatingNodeAffinity` plugin *is* effective and will enforce the
  `NotIn talos-3` rules added here.
