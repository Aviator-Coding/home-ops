# Requests, limits and QoS

Audit this in Git. The API server has already defaulted a missing request to the limit by the time `kubectl` can see the Pod.

## Memory request

A container with `limits.memory` and no `requests.memory` reserves the whole limit. The gate walks resources blocks in manifests and in Kustomize strategic-merge `patch: |` bodies. A JSON6902 op list stays opaque to it, so judge those by reading the target object.

`request == limit` is allowed when it is written down. The objection is an implicit request, not the size. Two exemptions are pinned in `scripts/ci/memory-request-declared-test.py` and must stay real:

- `database/cloudnative-pg/cluster-17` declares the equality on purpose.
- The flux-instance memory-limit patch only sets a limit because flux-operator's base manifest already sets `requests.memory`.

An exemption that matches nothing fails the test. Delete it rather than leaving a stale pass.

## CPU

The limit is a CFS quota for each 100ms period. A container at a fraction of its node can still be throttled for most periods. Throttling produces slow work. It does not produce a restart, a probe failure, or an OOMKill, and this repo has no alert on it.

Mean CPU under a limit is not a sizing input: the quota is what caps the mean. A `max_over_time(rate(...[5m])[14d:1h])` peak under-reports a burst, and coarser steps under-report further. Size from the finest resolution you can actually query, then re-read the container's throttle ratio (`container_cpu_cfs_throttled_periods_total` over `container_cpu_cfs_periods_total`) after the change.

A CPU request is the container's weight when the node is contended. talos-3 is that node. A request that looks generous on talos-1 can still starve work here. Do not cut one on talos-3 from an idle-node chart.

## Guaranteed versus Burstable

QoS is Guaranteed only when every container (init containers included) has request equal to limit for each resource it sets. k8tz injects an init container; its `initContainerResources` are request equal to limit so a pod that was Guaranteed stays Guaranteed (skill `system-namespaces`).

Raising a limit without the request, or cutting a request without the limit, demotes the pod to Burstable. Talos `runtime.OOMController` skips Guaranteed cgroups and will kill a demoted pod on a PSI spike. Hold the equality where the manifest says that is the point: k8tz, Rook mons, Rook log collector, Dragonfly.

## Empty limit maps

`resources.limits: {}` in a HelmRelease `values` block merges onto the chart default. It does not clear it. Before using an empty map to mean "no limit", open the chart `values.yaml` for that key. The Rook operator's chart default of 512Mi is the case that OOMKilled the operator; the manifest now sets an explicit limit (skill `rook-ceph`).

## Alerts

`VLLMMemoryExceedsRequest` and `VLLMMemoryRetainedAboveBound` watch the vLLM request. Nothing else in the repo pages when a container crosses its own request. A green cluster does not mean the reservation matches the working set.
