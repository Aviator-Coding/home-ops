# k8tz `imageVolume` injection strategy - evaluation (2026-09-01)

Evaluation of switching `system-controller/k8tz` from the `initContainer` injection
strategy to `imageVolume`, to remove the per-pod injected init container.

**Outcome: do not adopt.** The QoS gate that motivated the evaluation passes cleanly,
but three findings that the change request did not anticipate argue against shipping.
Upstream itself does not recommend the strategy. Recorded here so the question is not
re-opened from scratch.

Live evidence in this document was measured against the running cluster on 2026-09-01
(Kubernetes v1.36.3, Talos v1.13.9, containerd 2.2.7, 273 pods, k8tz chart 0.20.0).

## 1. The QoS gate - PASSES

`kubernetes/apps/base/system-controller/k8tz/app/helmrelease.yaml` carries this reasoning:

> Injected bootstrap initContainers get requests==limits so they never disqualify a pod
> from Guaranteed QoS (Talos runtime.OOMController skips Guaranteed cgroups - the
> rook-ceph mons depend on this; a resource-less k8tz init kept them Burstable despite
> requests==limits everywhere else).

**The reasoning becomes obsolete by construction, not contradicted.** Under `imageVolume`
there is no injected init container at all, so nothing exists to disqualify a pod. This
was verified rather than assumed.

Method: every pod was pulled as JSON and its QoS class recomputed twice - once as-is, once
with the `k8tz` init container removed - replicating Kubernetes' `ComputePodQOS`, which
considers `spec.containers` **and** `spec.initContainers` (this is precisely why a
resource-less k8tz init demoted the mons, as the comment records).

The model reproduced the live `.status.qosClass` for **273/273 pods with zero mismatches**,
so it is validated against ground truth before being used predictively.

| Transition | Pods |
|---|---|
| `Guaranteed` -> `Guaranteed` | 13 |
| `Burstable` -> `Burstable` | 195 |
| `Burstable` -> `BestEffort` | 34 |
| **`Guaranteed` -> anything lower** | **0** |

All three `rook-ceph` mons (`rook-ceph-mon-{h,i,m}`) are `Guaranteed` today and remain
`Guaranteed`. This is not a coincidence of measurement but a property: `Guaranteed`
requires *every* container to have `requests == limits`, so a pod that is `Guaranteed`
today already satisfies that on all its own containers, and removing one container from
that set cannot break the invariant. **Removing an init container can never demote a
Guaranteed pod.**

## 2. Finding: upstream does not recommend `imageVolume`

The k8tz v0.20.0 README says verbatim:

> On Kubernetes 1.33 and later, k8tz can use the `imageVolume` strategy to mount
> `/usr/share/zoneinfo` directly from the k8tz image, without requiring the shared
> `emptyDir` volume used by the `initContainer` strategy. **However, `initContainer`
> remains the recommended strategy for now, because `imageVolume` currently does not
> support mounting `/etc/localtime` from the image.**

Adopting it means running a configuration the maintainers advise against, for a feature
they ship as incomplete. The controller's own `--injection-strategy` flag help string
still advertises only `hostPath/initContainer`, and there is **no startup validation** of
the value - an unrecognised strategy fails at *patch* time, i.e. every pod `CREATE` in all
21 covered namespaces fails against a `failurePolicy: Fail` webhook.

## 3. Finding: `/etc/localtime` is silently dropped

`pkg/inject/inject.go` v0.20.0, `createImageVolumePatches`, mounts only
`/usr/share/zoneinfo`. The `/etc/localtime` mount is present but **commented out** with:

> `// currently only directory subpath is supported,`
> `// hopefully in future we can enable that:`

By contrast `createInitContainerPatches` (today's strategy) mounts **both**
`/etc/localtime` (subPath = the timezone) and `/usr/share/zoneinfo`. Verified live on a mon:

```
mountPath: /etc/localtime      subPath: America/New_York   readOnly: true
mountPath: /usr/share/zoneinfo                             readOnly: true
```

So the change would remove `/etc/localtime` from ~242 containers across 21 namespaces.
Worse, `removeContainerVolumeMounts` still *strips* any pre-existing `/etc/localtime`
mount without adding one back.

Measured consequence: the container's own `/etc/localtime` survives and typically points at
`Etc/UTC`. Software that honours the `TZ` env var is unaffected (proven below); software
that reads `/etc/localtime` and ignores `TZ` **silently shifts to UTC**. That is the same
silent-UTC-reversion failure class this repo has already been burned by - the measured
incident where 78 backup sources reverted to UTC.

`TZ` itself is still injected under `imageVolume` (`createEnvironmentVariablePatches` runs
for every strategy), so VolSync's cron scheduler - which reads the process `TZ` - is safe.
But the TZ-vs-`/etc/localtime` behaviour of every other workload is not enumerable.

## 4. Finding: 34 pods lose their QoS floor (`Burstable` -> `BestEffort`)

34 pods declare **no resources at all** on their own containers and are `Burstable` *only*
because the k8tz init container reserves `100m`/`128Mi`. Removing it returns them to their
natural `BestEffort`: evicted first under node pressure, `oom_score_adj` 1000.

Verified as having `resources={}` on every own container:

| Namespace | Workload |
|---|---|
| `security` | `authentik-server` (live SSO for every gated surface) |
| `monitoring` | `kube-state-metrics`, `grafana-image-renderer` |
| `system-upgrade` | `tuppr` (4 replicas) |
| `rook-ceph` | `rook-discover` (3), `rook-ceph-backup` (3) |
| `system` | `openebs-hostpath-localpv-provisioner`, snapshot/queue jobs |
| `home-automation` | `matter-server` |
| `database` | `nack`, `nats-box` |
| `ai` | 9 MCP-server pods |
| `downloads` | recyclarr / sabnzbd mover jobs |

This is not a QoS-reasoning failure - the recorded reasoning only ever concerned
`Guaranteed` - but it is an unrecorded dependency on the init container's reservations, and
it reaches live SSO.

## 5. Premise corrections

- **There is no chart bump to make.** `0.20.0` is both the latest published chart and the
  release that *added* `imageVolume` (`Chart.yaml` changelog: "add support for imageVolume
  injection strategy"; `CHANGELOG.md` -> PR #123). The HelmRelease is already pinned there.
  The requested "bump past 0.20.0" is not possible.
- **The benefit is smaller than stated.** 242 injected init containers today, not 204. And
  the freed *scheduler* reservation is **8.53 cores / 6.25 GiB**, not 242 x (100m, 128Mi) =
  24.2 cores / 30 GiB - a pod's effective request is
  `max(max(init requests), sum(container requests))`, so for most pods the init's 100m/128Mi
  is not the maximum and reserves nothing extra.

## 6. What was proven to work

Image volumes themselves are fully supported here - this is not why the strategy is being
declined. A throwaway pod mounting `quay.io/k8tz/k8tz:0.20.0` as an `image:` volume, using
the exact `subPath: usr/share/zoneinfo/` k8tz emits, succeeded: the zoneinfo tree mounted,
was correctly read-only, and `America/New_York` was present and valid.

A second probe replicating the post-change container shape (`TZ` set, `/usr/share/zoneinfo`
mounted, `/etc/localtime` left as the image's `-> Etc/UTC` symlink) resolved local time
correctly via `TZ`: glibc `date` returned `EDT-0400`, Python `time.tzname` returned
`('EST','EDT')`. This is the evidence that TZ-honouring software is unaffected - and equally
the evidence that `/etc/localtime` no longer carries the timezone.

## 7. Coverage baseline

21 covered namespaces; `kube-system` and `system-controller` excluded by the webhook's
`namespaceSelector`. The chart unconditionally prepends its own release namespace to
`ignoredNamespaces`, which is why `system-controller` is uninjected and why coverage is not
symmetric. Every covered namespace holding pods is at **100% injection** (242/242 pods
annotated `k8tz.io/injected: true`, 0 uninjected) - the baseline any future change must
preserve.

## 8. If this is revisited

Reopen when upstream lands `/etc/localtime` support for `imageVolume` and changes its
recommendation. At that point the QoS analysis in section 1 still holds and only sections 3
and 4 need re-testing. The 34-pod `BestEffort` transition is independent of upstream and
would need to be accepted deliberately, or pre-empted by giving those workloads real
resource requests - which is worth doing on its own merits regardless of k8tz.
