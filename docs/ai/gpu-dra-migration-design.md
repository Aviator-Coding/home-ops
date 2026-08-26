# GPU scheduling: DRA migration design and stop-at-design finding

**Status: designed, NOT shipped.** Captain decision D6 (2026-08-26) asked to migrate GPU
scheduling from device-plugin identities to native Kubernetes DRA now. The design below is
complete and ready to execute. It is deliberately **not** applied, because the driver it
depends on is declared non-production by its own vendor and cannot express the GPU sharing
this cluster does today.

This document is the deliverable for that stop: the verdict and its evidence, the full
target design so the work is ready when upstream catches up, and the exact conditions that
should reopen it.

- Applied GPU/AI changes → [`../ai-gpu-changelog.md`](../ai-gpu-changelog.md)
- Hardware failures → [`../hardware-incidents.md`](../hardware-incidents.md)
- B70 serving/tuning → [`b70-llm-serving-tuning.md`](b70-llm-serving-tuning.md)
- Why the B70 has its own resource identity → [`b70-second-card-decision.md`](b70-second-card-decision.md)

---

## 1. Verdict

Migrating today would trade a working scheduler contract for a beta one, and would take
GPU pods down on the way. Three independent blockers, any one of which is disqualifying:

| # | Blocker | Evidence | Requirement it breaks |
|---|---------|----------|----------------------|
| 1 | The only Intel GPU DRA driver is vendor-declared non-production | Intel's own README, verbatim (§2.1) | 4 (production-grade driver) |
| 2 | It cannot share one GPU across pods; we share one GPU across **5 pods in 3 namespaces right now** | Upstream issue + code search + live cluster (§2.2) | 5 (share semantics), 2 (zero unschedulable window) |
| 3 | No DRA-equivalent alert series exists in our monitoring stack | kube-state-metrics v2.20.0 resource list (§2.3) | 3 (migrate gpu-loss alerts, prove new series) |

Blocker 2 is the hard one. It is not a maturity judgement call: the driver allocates a GPU
to exactly one pod, and we currently run five on one card. Cutting over would leave four
GPU pods `Pending` — the precise outcome D6's staging requirement forbids.

**The design itself is sound and worth keeping.** DRA would fix a real weakness in today's
setup (§3.2). The blocker is the driver's maturity and feature set, not the approach.

---

## 2. Evidence

### 2.1 The driver is vendor-declared non-production

[`intel/intel-resource-drivers-for-kubernetes`](https://github.com/intel/intel-resource-drivers-for-kubernetes)
is the only vendor DRA driver for Intel GPUs. Line 3 of its README on `main`, and line 3 of
its GPU driver README, read verbatim (fetched 2026-08-26):

> `CAUTION: This is a beta / non-production software, do not use on production clusters.`

Latest GPU release is `gpu-v0.11.0` (2026-06-10) — pre-1.0. It *is* technically compatible
with us: `v0.11.0` targets Kubernetes v1.34+ with structured parameters, and we run v1.36.3
serving `resource.k8s.io/v1`. Compatibility is not the problem; the vendor's own support
statement is.

Two things this is **not**:

- Not [`kubernetes-sigs/dra-example-driver`](https://github.com/kubernetes-sigs/dra-example-driver).
  That is a teaching scaffold whose devices are **mock GPUs** — "an example resource driver
  … intended to demonstrate best-practices". It cannot drive real hardware at all. The two
  are not interchangeable, and neither is a substitute for the other.
- Not a hardware-support gap. The driver enumerates devices by scanning sysfs PCI
  generically (`pkg/gpu/discovery/discovery-pci.go`), not from a device-ID allowlist, so
  our Battlemage B70 (`0xe223`) and Raptor Lake iGPU (`0xa7a0`) would both be discovered and
  published. **Our hardware would work.** The blockers are elsewhere.

### 2.2 It cannot express our sharing — and we depend on that sharing

Measured live 2026-08-26, mid-roll: talos-3 has **exactly one** physical GPU, and five pods
in three namespaces are concurrently Running against it.

```
$ talosctl -n 10.10.10.13 ls /dev/dri
card0        # 0000:03:00.0, 8086:E223 — the Arc Pro B70
renderD128
```

| Pod | Namespace | Requests |
|-----|-----------|----------|
| `vllm` | `ai` | `devic.es/b70: 1` |
| `tdarr-node` | `media` | `devic.es/b70: 1` |
| `jellyfin` | `media` | `gpu.intel.com/xe: 1` |
| `plex` | `media` | `gpu.intel.com/xe: 1` |
| `playwright` | `selfhosted` | `gpu.intel.com/xe: 1` |

That five-on-one figure is a snapshot taken while the #1444 iGPU roll was still in flight
(talos-1 had just come back advertising `xe=99`; talos-2 was `NotReady,SchedulingDisabled`).
**The blocker does not depend on it.** Once the roll settles the fleet is 3 iGPUs + 1 B70,
and under strict 1-to-1 it still does not fit:

- **B70 group — strictly impossible.** `vllm` and `tdarr-node` both run against the single
  B70, and both must: the chat server needs the 32 GiB discrete card, and tdarr's QSV
  transcode needs the discrete codec block. One card, two concurrent pods, no second B70.
  Under 1-to-1 one of them is permanently `Pending`. This alone is disqualifying.
- **iGPU group — fits only with zero headroom.** `jellyfin`, `plex` and `playwright` would
  land one per node across three iGPUs. That leaves no slack: this cluster reboots nodes one
  at a time as routine maintenance (Ceph OSD safety, `just talos apply-node`), and every such
  drain would strand one of them `Pending` with nowhere to go — where today it simply
  co-schedules onto a surviving node.

Sharing works today because both plugins hand out share-count tokens (`count: 99`,
`sharedDevNum: 99`) rather than exclusive devices. The Intel DRA driver does not do this.
From upstream issue
[#79](https://github.com/intel/intel-resource-drivers-for-kubernetes/issues/79) (open,
`enhancement`), stating the current behaviour plainly:

> "Right now, the Intel DRA driver only allows a strict 1-to-1 mapping OR SR-IOV."

Confirmed independently: a code search for `allowMultipleAllocation` across the repository
returns **zero hits**. The feature is requested, not implemented.

So a cutover strands GPU pods `Pending` in either fleet state — four of five today, and at
minimum `vllm` or `tdarr-node` permanently once the roll settles. The three DRA sharing
mechanisms all fail here:

| Mechanism | Needs driver support? | Why it fails for us |
|-----------|----------------------|---------------------|
| Shared `ResourceClaim` (many pods → one claim) | No | `ResourceClaim` is **namespaced**. Our B70 consumers span `ai` + `media`; our iGPU consumers span `media` + `selfhosted`. No single claim can cover either group. |
| `allowMultipleAllocation` / consumable capacity | **Yes** | Not implemented by the driver (issue #79, zero code hits). Our cluster side is ready — `DRAConsumableCapacity` is enabled (BETA). |
| Partitionable devices / SR-IOV | **Yes** | Splits VRAM. The chat server alone needs ~21.4 GiB weights + ~5.2 GiB KV of the card's 32 GiB; partitioning starves it. |

That leaves exactly one workaround, and it is the one the reference repo
(`joryirving/home-ops`) uses: `adminAccess: true` with `allocationMode: All`. Rejected here,
because Intel documents that combination under **"GPU monitor deployment"**, and notes:

> "`adminAccess` ResourceClaim allocations are not counted by scheduler as consumed resource"

Using it for ordinary serving workloads means (a) the scheduler stops accounting GPU
consumption at all, (b) every GPU namespace — `ai`, `media`, `selfhosted` — must be labelled
`resource.k8s.io/admin-access: "true"`, granting privileged device access to routine
workloads, and (c) claims grab *every* matching device rather than one. Swapping an
enforced share-count for an unaccounted privileged escape hatch is not a migration; it is a
regression wearing newer API types.

### 2.3 No DRA-equivalent alert series exists yet

The gpu-loss alerts landing in [#1445](https://github.com/Aviator-Coding/home-ops/pull/1445)
key off node extended-resource capacity:

```promql
kube_node_status_allocatable{resource="devic_es_b70", node="talos-3"} == 0
kube_node_status_allocatable{resource="gpu_intel_com_xe"} == 0
```

Under DRA those series **cease to exist** — DRA devices are published in `ResourceSlice`
objects, not as node allocatable extended resources. And nothing replaces them today:

- kube-state-metrics **v2.20.0** (our running version) has no collector for
  `resourceslices`, `resourceclaims`, or `deviceclasses`. Verified against its full
  supported-resource list in `pkg/options/resource.go` — all three are absent, so this is
  not merely a `--resources` flag we forgot to set.
- The only DRA-aware series in the cluster today is
  `apiserver_storage_objects{resource="resourceslices.resource.k8s.io"}`, a **cluster-wide
  object count**. It cannot express "talos-3 lost its B70" — no node label, no device
  identity.

So migrating the alerts as requirement 3 asks — and *proving the new series exist* — is not
possible with the current monitoring stack. That work needs its own upstream dependency
(ksm ResourceSlice support, or scraping the driver's own metrics) resolved first.

---

## 3. The target design (ready to execute when §5 clears)

### 3.1 Shape

Additive, alongside the running plugins:

- `intel-gpu-resource-driver` HelmRelease + OCIRepository under
  `kubernetes/apps/base/system/intel-gpu-resource-driver/`, overlay in
  `kubernetes/apps/main/system/`, matching the existing device-plugin layout.
- Two **narrow** `DeviceClass` objects (below) — never the driver's shipped catch-all
  `gpu.intel.com` class, which pools every Intel GPU and would reintroduce the exact trap
  §3.2 describes.
- One `ResourceClaimTemplate` per consumer, in the consumer's own namespace.

### 3.2 The invariant, enforced structurally

This is the part worth having. Today `gpu.intel.com/xe` is a **pooled token**: it means "an
xe-bound Intel GPU", and the B70 and the iGPUs both answer to it. That is why jellyfin,
plex and playwright are sitting on the B70 right now, and why the changelog has a standing
`allowIDs: "0xa7a0"` follow-up to eventually fence them off. The pool cannot tell a 32 GiB
discrete card from an integrated one.

DRA replaces the token with **per-device identity**. The driver publishes each physical GPU
into a `ResourceSlice` carrying `pciId`, `pciBusID`, `driver`, and `memory`, so a
`DeviceClass` can select the actual card by CEL:

```yaml
---
# Discrete Arc Pro B70 (Battlemage G31, 8086:e223) — talos-3 only.
# Consumers: vllm, vllm-embed, comfyui, tdarr-node.
apiVersion: resource.k8s.io/v1
kind: DeviceClass
metadata:
  name: gpu-b70.intel.com
spec:
  selectors:
    - cel:
        expression: >-
          device.driver == "gpu.intel.com" &&
          device.attributes["gpu.intel.com"].pciId == "0xe223"
---
# Integrated Raptor Lake-P iGPU (8086:a7a0) — all three nodes.
# Consumers: jellyfin, plex, playwright.
apiVersion: resource.k8s.io/v1
kind: DeviceClass
metadata:
  name: gpu-igpu.intel.com
spec:
  selectors:
    - cel:
        expression: >-
          device.driver == "gpu.intel.com" &&
          device.attributes["gpu.intel.com"].pciId == "0xa7a0"
```

Why this is stronger than today's arrangement, in both directions:

- **B70 consumers cannot land on an iGPU.** The scheduler filters nodes to those whose
  `ResourceSlice` publishes a `0xe223` device. Only talos-3 does. No hostname affinity, no
  `nodeSelector` to drift.
- **iGPU consumers cannot land on the B70.** This is new. It closes the `allowIDs` gap
  structurally rather than by a plugin flag, and it closes it for *both* device groups at
  once, from one place.

A `memory` selector could reinforce this (`device.capacity["gpu.intel.com"].memory` — the
B70 reports ~32656 MiB), but `pciId` is the crisper identity and is what the driver
guarantees. Prefer it; keep memory as a belt-and-braces addition only if a second discrete
card ever shares the `0xe223` ID.

### 3.3 Per-consumer claims

One `ResourceClaimTemplate` per Deployment, in the consumer's namespace, referenced from
the pod spec via `resourceClaims` + `resources.claims`. Sketch for a B70 consumer:

```yaml
apiVersion: resource.k8s.io/v1
kind: ResourceClaimTemplate
metadata:
  name: vllm-gpu
  namespace: ai
spec:
  spec:
    devices:
      requests:
        - name: gpu
          exactly:
            deviceClassName: gpu-b70.intel.com   # narrow class, not gpu.intel.com
            count: 1
```

Note what is **absent**: no `adminAccess`, no `allocationMode: All`. Those are the reference
repo's sharing workaround (§2.2) and must not be copied. This template is correct only once
the driver can multi-allocate; until then it schedules exactly one pod.

### 3.4 Staged cutover plan (requirement 2)

Five PRs, each independently green and revertible. No stage removes a working path before
its replacement is proven.

| Stage | Change | Live gate before proceeding |
|-------|--------|------------------------------|
| 1 | Deploy the driver only. No `DeviceClass`, no consumer change. | `ResourceSlice` published per node; `0xe223` on talos-3, `0xa7a0` on all three. Plugins still serving; zero pod churn. |
| 2 | Add the two narrow `DeviceClass` objects. Still no consumer change. | `kubectl describe deviceclass` resolves; a throwaway test pod binds the intended card and *fails* to bind the wrong one. |
| 3 | Migrate **one** low-risk consumer (`playwright` — restartable, non-serving) to a claim; leave its plugin request removed only after it is Running. | `playwright` Running on an iGPU, never the B70. Verify via the pod's allocated device in `ResourceClaim.status`. |
| 4 | Migrate the rest one at a time, serving workloads last (`tdarr-node` → `jellyfin` → `plex` → `comfyui` → `vllm-embed` → `vllm`). | After each: pod Running, correct device, no Pending anywhere. Roll back that single app on failure. |
| 5 | Retire both plugins; migrate the gpu-loss alerts (§4) and prove the new series. | All GPU pods Running on DRA for a full reconcile interval; alerts firing correctly against a simulated loss. |

Stage 3 is the earliest point anything can break, and it breaks exactly one non-serving pod.
Stages 1–2 are pure additions with no consumer impact — they are safe to run today, and are
the natural first move if the captain wants forward progress before §5 clears.

### 3.5 Talos specifics

The driver's kubelet plugin needs CDI paths writable on the host. The reference deployment
sets `cdi.staticPath` and `cdi.dynamicPath` to `/run/cdi` and runs the kubelet plugin
privileged (privileged mode is how it reads device details without xpumd). Both need
checking against Talos's read-only rootfs before stage 1 — `/run` is writable, so this is
expected to work, but it is unverified here and must be proven in stage 1, not assumed.
Non-privileged discovery is the driver's recommended mode but requires deploying Intel's
XPU Manager daemon (`xpumd`) as a second component; privileged-without-xpumd is the simpler
path and matches the reference.

---

## 4. Alert migration mapping (requirement 3)

For stage 5, once §2.3's gap is closed. Today's expressions and their DRA intent:

| Alert | Today | DRA intent | Blocked on |
|-------|-------|-----------|------------|
| `B70GpuLost` | `kube_node_status_allocatable{resource="devic_es_b70", node="talos-3"} == 0` | "no `ResourceSlice` on talos-3 publishes a `0xe223` device" | ksm ResourceSlice collector |
| `XeGpuLost` | `kube_node_status_allocatable{resource="gpu_intel_com_xe"} == 0` | "a node that previously published an Intel GPU device now publishes none" | ksm ResourceSlice collector |

Do **not** delete the existing rules before a replacement is proven emitting. The failure
mode being guarded against is a silent GPU loss — both prior B70 losses were found late by a
human noticing something else — so an alert gap here is a direct regression of #1445's whole
purpose. Options when reopening, in preference order:

1. **kube-state-metrics ResourceSlice support**, if it lands upstream — keeps one metrics
   pipeline and one alerting idiom.
2. **The driver's own metrics endpoint**, scraped via `ServiceMonitor`, if it exposes
   per-device health. The driver already tracks a `health` attribute per device.
3. A small exporter, only if neither of the above materialises. Least preferred: it is new
   bespoke machinery for one alert.

Until then the existing extended-resource alerts remain correct **because the plugins remain
deployed** — the stop-at-design outcome keeps them valid, at no cost.

---

## 5. When to reopen this

Reopen when **both** of these are true. Neither is in our control; both are cheap to check.

1. **Driver maturity.** The `CAUTION: This is a beta / non-production software, do not use
   on production clusters.` line is gone from
   [the README](https://github.com/intel/intel-resource-drivers-for-kubernetes/blob/main/README.md),
   or Intel otherwise states production support. A `v1.0.0` GPU release is the obvious
   signal — the driver's own docs already treat v1.0 as the horizon for removing deprecated
   attributes.
2. **Sharing.** Issue
   [#79](https://github.com/intel/intel-resource-drivers-for-kubernetes/issues/79) is
   implemented — the driver publishes devices with `allowMultipleAllocation: true`. Our
   cluster side is **already ready**: `DRAConsumableCapacity` is enabled (BETA), and the
   driver already publishes a `millicores: 1k` capacity per device. Only the
   multi-allocation flag is missing.

Requirement 3's alert work (§2.3) gates stage 5 specifically, not the whole migration —
stages 1–4 can proceed once 1 and 2 clear.

If the captain wants to proceed **before** those clear, the honest options are: accept
`adminAccess: true` on every GPU namespace with the privilege and accounting costs in §2.2;
or collapse each device group into a single namespace to use a shared `ResourceClaim`, which
means moving `tdarr-node` out of `media` and `playwright` out of `selfhosted` — a larger
architectural change than the migration itself, for a worse result.

---

## 6. What holds in the meantime

Nothing is left broken by stopping here. The current arrangement — `devic.es/b70` for
discrete consumers, `gpu.intel.com/xe` for light QSV/browser work — is the design recorded
in [`b70-second-card-decision.md`](b70-second-card-decision.md) and remains correct. Two
things stay on the books, unaffected by this stop:

- The `allowIDs: "0xa7a0"` follow-up on the Intel GPU plugin, to drop the B70 from the `xe`
  pool once talos-1/2 advertise their iGPUs. §3.2 is the eventual structural replacement for
  it; until then it remains the right fix, and it is a materially smaller change than this
  migration.
- The gpu-loss alerts from #1445, which stay valid exactly as written.
