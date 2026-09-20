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

Verified immovable, totalling **53698 Mi** before the vllm change (was 57794 Mi;
the 2026-09-20 OSD request cut below removed 4096 Mi of it):

| what | request | why it cannot move |
|---|---:|---|
| 2x Ceph OSD | 25088 Mi | `kubernetes.io/hostname` nodeSelector; own local NVMe (was 29184 Mi until the 2026-09-20 request cut below) |
| `rook-ceph-mon-h` | 2304 Mi | hostname nodeSelector + `openebs-hostpath` local PV |
| `database/postgres-17-1` | 4096 Mi | CNPG `instances: 3` + `podAntiAffinityType: required` on hostname - one instance per node, in a 3-node cluster |
| `database/nats-2` | 1024 Mi | `cluster.replicas: 3` + `maxSkew: 1` hostname spread - one per node |
| DaemonSets | 4048 Mi | one pod per node by definition |
| static control plane | 832 Mi | `kube-apiserver`/`-controller-manager`/`-scheduler` |
| GPU-pinned (`vllm`, `tdarr-node`) | 16896 Mi | `devic.es/b70*`, talos-3 only |

The OSD reservation is **mostly** not slack - but this paragraph originally said it
was *entirely* not slack, and that was wrong on its stated reason. Corrected
2026-09-20; 4096 Mi of it was in fact reclaimable on this node.

The cluster sets `osd_memory_target: 10 GiB` (`rook-ceph/cluster/helmrelease.yaml`),
and the original argument here was that each OSD grows its BlueStore cache toward
10 GiB, so the observed peaks were "a cache still filling, not headroom to reclaim".
Two problems with that. First, `osd_memory_target` sizes the BlueStore cache, not
total RSS - real usage legitimately runs *above* the target, so the target is a floor
for sizing the request, never the request itself. Second, those figures were quoted
as "30-day peaks" but Prometheus retains **14d**, so no 30-day peak has ever been
observable here; treat every "30-day peak" in this doc as a 14-day one.

The correct argument lands in the same place but not at the same number. The highest
OSD working set on record is **9,317 Mi** (2026-09-05, during a real 128-of-393-PG
degradation - precisely when an OSD must not be constrained), so the request must
clear that, which rules out matching it to the 10 GiB target. It does **not** have to
clear 14Gi. As of 2026-09-20 the request is **12Gi** with the limit left at 14Gi, so
a spike can still burst while the unusable 2Gi/OSD of reservation is returned to the
node. Detection if that proves too tight:
`max_over_time(container_memory_working_set_bytes{namespace="rook-ceph",container="osd"}[7d])`
crossing 12,288 Mi, and it is worth re-checking after the next real recovery event.

Likewise `rook-ceph-mon` and the
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

That first wave was not enough. It left 40294 Mi of room against a 39342 Mi
peak, so the only request that cleared the peak (39Gi) put the node at 99.6%
with 358 Mi of margin. A truthful request and a usable margin were mutually
exclusive, because the node's non-negotiable floor - 2 OSDs at 29184 Mi, a mon,
a CNPG instance, a NATS instance, DaemonSets, the control plane - is 57794 Mi,
**62% of the node before the LLM is placed at all**.

> Those two figures are the numbers **as they stood at the time of that decision**,
> kept as written so the reasoning still reads true. The floor has since dropped:
> the 2026-09-20 OSD request cut took the OSD pair to 25088 Mi and the floor to
> 53698 Mi (~57% of the node). It does not change the conclusion that was reached
> here, only how tight it was.

So a second wave moved two Ceph daemons that are neither OSDs nor mons off
talos-3. **These two are not equivalent to each other, and the order matters.**

| step | what | frees | class of action |
|---|---|---:|---|
| 1 | `rgw` (objectstore gateway) | 1280 Mi | One of **two** replicas (talos-2 + talos-3), both serving behind one service. The other serves throughout. No failover, no client impact. |
| 2 | `mds` (CephFS metadata) | 1280 Mi | `ceph-filesystem-b` held **ACTIVE rank 1**, not a standby. Draining it forces a **rank failover** to standby-replay `-d`. Routine and designed-for - every node roll does it - but client-visible in a way step 1 is not. |
| 3 | `ai/vllm` request 16Gi -> 39Gi | - | Only possible once 1 and 2 have created the room. Raising it first makes the LLM unschedulable. Shipped at 40Gi first and corrected to 39Gi the same day - see the Final arithmetic correction below. |

Verify step 2 by `ceph health` returning to **HEALTH_OK** (it was HEALTH_OK,
muted `AUTH_*` only, before the change) before treating step 3 as done. That is
the same gate this repo uses for node rolls.

Step 2 also confines all 4 mds pods to talos-1/talos-2, because Rook applies one
placement block to every mds pod. It stays schedulable during a single-node
drain - one eligible domain means skew 0, so the `maxSkew: 1` `DoNotSchedule`
spread is satisfied and all four land on the survivor - but for the length of a
talos-1 or talos-2 maintenance window the filesystem has no node-level MDS
redundancy, where before it still had two nodes. A talos-3 window is now
strictly better: no MDS churn at all.

### What could not be moved

`rook-ceph.rbd.csi.ceph.com-ctrlplugin` (1024 Mi, the third daemon originally
considered) **cannot be relocated from this repo**. Rook v1.20 migrated CSI to
ceph-csi-operator: the Deployment is owned by a `Driver` CR
(`csi.ceph.io/v1`), which carries a usable `spec.controllerPlugin.affinity`
field but is created by an internal Helm release (`ceph-csi-drivers`) that is
not Flux-managed. Neither the `rook-ceph` operator chart v1.20.7 (whose only
`nodeAffinity` value targets the `discover` DaemonSet) nor
`CephCluster.spec.csi` (`cephfs`, `readAffinity`, `skipUserCreation` only)
exposes controllerPlugin placement. Reaching it would mean fighting the internal
release or adding a conflicting `Driver` CR; neither is worth 1024 Mi.

### Final arithmetic

```
allocatable                                  93604 Mi
shed, wave 1 (right-sizing + 7 relocations)   5960 Mi
shed, wave 2 (rgw 1280 + mds 1280)            2560 Mi
                                             --------
total shed                                    8520 Mi
```

The **8520 Mi shed** figure is fixed - it is what the two waves actually
removed from talos-3. What is *not* fixed is what talos-3's non-vllm workloads
commit once the dust settles: it oscillates with ephemeral GitHub Actions
runner pods landing on this node, so "committed, excluding ai/vllm" is a
range, not a point. Both readings below are measured live, same day
(2026-09-14):

```
committed, excluding ai/vllm (CI busy, settled)   52990 Mi
committed, excluding ai/vllm (CI quiet)           51806 Mi
swing between the two                              1184 Mi  (2 ARC runner pods, 640 Mi)
room available for ai/vllm (CI busy)              40614 Mi
room available for ai/vllm (CI quiet)             41798 Mi
ai/vllm 30-day peak working set                   39342 Mi
ai/vllm request, as shipped                       39936 Mi  (39Gi)
                                                  --------
talos-3 committed after the change (CI busy)      92926 Mi  = 99.3%, margin  678 Mi  <- binding case
talos-3 committed after the change (CI quiet)     91742 Mi  = 98.0%, margin 1862 Mi
```

The **busy case is the binding one**: it is the state that must schedule the
pod, and it is what actually happened - see the correction below. The quiet
case is shown so the range is visible; do not treat 98.0%/1862 Mi as the
steady state, it is the more favorable end of a swing driven entirely by
unrelated CI activity.

`ai/vllm` uses `strategy: Recreate`, so on rollout the old pod is terminated
before the new one is created; at 39Gi the new pod needs 39936 Mi free and has
at least 40614 Mi even in the busy case, so it schedules. Its overage at peak
goes from **+21230 Mi to zero** - it leaves the kubelet's exceeds-set entirely
and joins the last-evicted group.

**CORRECTION 2026-09-14: this shipped as 40Gi first, and it failed.** The
request above was projected before wave 2's relocations had actually settled;
the 50750 Mi originally projected for "committed, excluding ai/vllm" turned
out to be 52990 Mi once measured live, about 2.2 GiB higher. 40Gi (40960 Mi)
sits inside the busy/quiet churn band above: at the busy reading it is 346 Mi
short of schedulable and the pod went `Pending` - `0/3 nodes are available: 2
Insufficient devic.es/b70, 3 Insufficient memory`, an LLM outage. At the quiet
reading it fits with 838 Mi to spare, and the pod is in fact `Running` at
40Gi right now, because Flux retried once CI activity dropped. So 40Gi does
not fail *always* - it fails only when CI is busy, which is why it was
rejected: a workload whose schedulability depends on unrelated CI load fails
unpredictably. The fix is 39Gi (39936 Mi), which fits even in the busy case,
with 678 Mi to spare.

A **38Gi** request was also considered and rejected: it leaves roughly
1702 Mi of margin in the busy case, but 38912 Mi falls 430 Mi short of the
39342 Mi peak, so the pod would stay inside the kubelet's exceeds-set -
better than the original +21230 Mi, but it does not achieve the goal of
leaving that set entirely.

678 Mi is a thin margin, accepted deliberately rather than defended as
comfortable. The residual risk is that talos-3's other requests grow by
roughly 700 Mi and the next reconcile cannot schedule vllm - and that has now
been watched happening and self-healing, not merely reasoned about: Helm
declared `UpgradeFailed` at 04:17:37Z (`timeout waiting for:
[Deployment/ai/vllm status: InProgress]`), Flux's `Remediated=True
RollbackSucceeded` at 04:19:13Z automatically restored the previous 16Gi
release, and Flux retried the upgrade at 04:19:24Z - service was restored
with no human action in under two minutes. That is a **loud**, self-healing
failure with a clear `HelmRelease` condition naming the cause. The exposure
this change removes is the opposite: **silent**. At the 16Gi request the pod
sat 21230 Mi over, first in the kubelet's eviction ranking, and would have
been killed with no failed release, no condition, and nothing to read
afterward. Accepting a thin margin guarded by a loud failure, in order to
close a silent one, is the trade being made deliberately.

A high commitment here is the correct end state, not a symptom: talos-3 exists
to host a GPU-pinned 38.4 GiB LLM, two OSDs and a mon. The reservation is nearly
spoken for by design, while actual RAM use sits near 75% - roughly 21 GiB free.
What the margin protects is the ability to admit a future DaemonSet, not the
node's ability to run what is on it.

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

- **`ai/vllm`'s memory is anonymous, not reclaimable cache** - but
  **`--no-mmap` is not why, and dropping it would free approximately nothing.**
  Measured `container_memory_rss` 37536 Mi against `container_memory_cache`
  **4 Mi**, and a request does have to cover it. **CORRECTION 2026-09-19:** the
  original version of this bullet named dropping `--no-mmap` as "the lever that
  would shrink the footprint". That was wrong and is retracted. Sampling the
  pod's first minutes at 2-minute resolution shows RSS stays at **635 MiB**
  through model load while **22,502 MiB of page cache** appears: llama.cpp's
  `--no-mmap` path `read()`s each tensor into a small reusable buffer and
  uploads it to VRAM, so the GGUF already lands in reclaimable, file-backed page
  cache - the exact state dropping the flag was supposed to achieve. Leave it
  alone. The anonymous memory was the **host prompt cache**: a single
  `brk()`-grown `[heap]` of 17,107 Mi (vs 317 Mi on the same image with
  `--cache-ram 0`) growing +6,485 Mi/day, fixed 2026-09-19 by `--cache-ram 4096`
  plus `GLIBC_TUNABLES=glibc.malloc.mmap_threshold=131072`. Expected post-fix
  steady state is ~9-20 GiB, so **the 39Gi request above is expected to become
  re-derivable downward** - but only against a measured steady state, not a
  reading taken shortly after a roll. Full evidence:
  `docs/ai/vllm-host-prompt-cache.md`.
- **The descheduler's `LowNodeUtilization` plugin has never rebalanced
  anything.** Its `thresholds` are `cpu/memory/pods: 20`, and no node in this
  cluster has been under 20% memory, so it logs `No node is underutilized,
  nothing to do here, you might tune your thresholds further` and evicts 0 on
  every 5-minute pass while correctly classifying talos-3 as `overutilized`.
  Do not expect it to relieve talos-3. Its
  `RemovePodsViolatingNodeAffinity` plugin *is* effective and will enforce the
  `NotIn talos-3` rules added here.

## 7. 2026-09-15: `ai/embedding-gpu` added, and why the "committed" figure is a band, not a point

Full context for this change (why the workload exists, VRAM arithmetic, throughput):
[`docs/ai/embedder-gpu-migration-analysis-2026-09-15.md`](ai/embedder-gpu-migration-analysis-2026-09-15.md).
This section only redoes the talos-3 memory-scheduling arithmetic the anti-pattern
rule in `AGENTS.md` requires before adding a workload here.

**The section 4 busy-case figure above (92,926 Mi) was never stale.** Measured
again live on 2026-09-15, talos-3's total committed memory oscillates in a band:

```
permanent (non-CI-runner) requests        87,115 Mi   <- the real floor
live total, quiet (1 CI runner)           88,002 Mi
live total, busy (9-10 CI runners)        92,610 Mi   <- within 316 Mi of the
                                                          92,926 Mi figure above
allocatable                               93,604 Mi
```

Both ends of that band are real: the busy end is the same state section 4 already
measured (permanent load plus roughly ten ephemeral `gha-runner-scale-set` pods at
512 Mi each, up to `maxRunners: 15` per scale set); the quiet end is the same node
caught with only one runner present. Neither reading is wrong and neither
superseded the other - they are the two ends of one oscillation, not evidence of
drift.

**The permanent headroom, not either instantaneous reading, is what a new
talos-3 workload must be sized against:**

```
allocatable                               93,604 Mi
permanent (non-CI-runner) requests        87,115 Mi
                                          ---------
permanent headroom                         6,489 Mi   ~= 12 CI runner slots (512 Mi each)
```

`ai/embedding-gpu` (`kubernetes/apps/base/ai/embedding-gpu/app/helmrelease.yaml`)
requests **1,024 Mi**, permanently - it is not a CI-runner pod, so it comes out of
the permanent side of the ledger:

```
permanent headroom after ai/embedding-gpu  5,465 Mi   ~= 10 CI runner slots
```

**Updated 2026-09-20 - the OSD request cut returns 4,096 Mi to this ledger.** The
measured figures above are left as measured on 2026-09-15; this is the delta from
cutting the two talos-3 OSD requests 14Gi -> 12Gi (limits unchanged), and nothing
else:

```
permanent (non-CI-runner) requests        83,019 Mi   <- was 87,115 Mi
                                          ---------
permanent headroom                        10,585 Mi   <- was  6,489 Mi
permanent headroom after ai/embedding-gpu  9,561 Mi   ~= 18 CI runner slots
```

This accounts only for the OSD change. Any other in-flight talos-3 work (notably
relocating workloads that drifted onto this node) moves the same ledger again, so
re-measure rather than adding deltas from two sources.

That does not put the node over its allocatable ceiling in either the busy or
quiet case - it narrows the number of CI runner pods that can land on talos-3
concurrently before the scheduler starts queuing them `Pending`, which is a
capacity trade against the runner scale set, not a risk to any pinned workload.
`ai/vllm`'s eviction-ranking argument in section 1 is unaffected: its request
still exceeds its usage, so it still sits in the kubelet's last-evicted group
regardless of what else lands on the node. `ai/embedding-gpu` additionally ships
at `priorityClassName: embedding-gpu-low` (`value: -10`,
`preemptionPolicy: Never`) - the lowest priority of anything on the node - so
under genuine memory pressure it is the first pod evicted and can never itself
preempt anything.

**The rule for the next reader:** re-reading `kubectl describe node talos-3` (or
equivalent) at a single moment can return anywhere in this band depending on how
many CI runners happen to be scheduled that instant. Compare a new workload's
request against the **permanent headroom** above (which excludes runner churn),
not against whatever one live reading happens to show.

## 8. 2026-09-20: the node stops being the constraint - and the guard gets weaker

Two changes landed together and moved talos-3 further than everything in §4 did.

**`ai/vllm` 39Gi -> 12Gi.** The 39Gi in §4 was correct for what was measurable
then: it covered a 39342 Mi peak. That peak was the unbounded host prompt-cache
leak, which PR #1731 bounded on 2026-09-19 with `--cache-ram 4096` +
`GLIBC_TUNABLES`. Measured on the post-fix pod (`vllm-5cb8f5f6f4-zmvqq`, started
2026-09-19T19:40:40Z, zero restarts, 17h; all pre-fix history excluded):

| | |
|---|---:|
| peak working set | 5750 Mi |
| current | 5132 Mi |
| trajectory | oscillates 4577-5750 Mi, does **not** climb |

The pre-fix pod rose monotonically at +6485 Mi/day and never plateaued. The
request is sized on the mechanism, not on the 17h peak, because prefill is 88%
of this workload's tokens - its peak arrives with prompt volume, not with time:
baseline 1232 Mi + the 4096 Mi cache bound = a 5328 Mi nominal ceiling, which
the measured peak exceeds by only 1.08x; 12288 Mi is baseline + 2.7x the cache
bound, i.e. 2.14x the measured peak.

**Fifteen Class-A sites fixed.** Containers declaring `limits.memory` with no
`requests.memory` reserve the whole limit. Sixteen were live in Git (see
AGENTS.md for why an audit run against the *cluster* reported zero); fifteen
were given requests sized above their measured 14d peaks.

### Where that leaves the node

| | before | after |
|---|---:|---:|
| talos-3 requested | 89222 Mi (95.3%) | **60262 Mi (64.4%)** |
| talos-1 requested | 67265 Mi (71.9%) | 66455 Mi (71.0%) |
| talos-2 requested | 70064 Mi (74.8%) | 69472 Mi (74.2%) |

28960 Mi returned to talos-3. §4's "final arithmetic" and §7's band are
superseded for the vllm line only; every other row in them still holds.

### The thing to actually worry about now

**The protection got weaker, not stronger.** §7 measured headroom in CI-runner
slots because there were three. There are now roughly thirty. talos-3 is
guarded only by a hand-maintained `hostname NotIn talos-3` deny-list on about a
dozen workloads, and a deny-list **fails open**: between 2026-09-16 and
2026-09-19, 5936 Mi landed on the node purely because the arrivals carried no
affinity at all, while the node was at 99%. At 64% nothing pushes back at all.

A taint on talos-3 plus tolerations on what genuinely belongs there (the GPU
pair, the OSDs, the mon, the CNPG/NATS instances, the DaemonSets) fails closed
and is the structurally correct shape. It is deliberately **not** part of this
change - it is a placement change and its own decision.

### If 12Gi is wrong

`VLLMMemoryExceedsRequest` (`kubernetes/apps/base/ai/vllm/app/prometheusrule.yaml`)
fires at `working_set / request > 1`, `for: 30m`. It was added with this cut for
exactly that purpose: nothing else in the repo alerts on crossing a *request*,
and the two limit rules stay silent for a further 36Gi. The observed post-fix
band is 0.37-0.47 of the new request, so the threshold sits 2.1x above the top
of the measured noise.
