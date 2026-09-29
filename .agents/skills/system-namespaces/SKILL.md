---
name: system-namespaces
description: "Read before changing system-controller/k8tz, system-upgrade/tuppr, kube-system add-ons (coredns, multus, descheduler, reloader, spegel, mglru-disable) or system/fstrim, or moving anything between the system* namespaces. Covers k8tz excluding its own namespace, the non-atomic webhook move, tuppr pinned by the Talos namespace allowlist, and CREATE-only admission webhook drift."
---

# System namespaces: k8tz, tuppr and kube-system add-ons

## Tripwires

1. **Do not merge `system-controller` or `system-upgrade` into `system`.** k8tz's chart prepends its release namespace to `ignoredNamespaces` with no opt-out, so the move strips TZ injection from `system`. The webhook object is cluster-scoped and same-named, so the uninstall/install has no safe order. tuppr's namespace is on the Talos API allowlist. [k8tz.md](references/k8tz.md)
2. **k8tz stays `requests == limits` on every container it runs, including the injected init container and the cert-watcher sidecar.** `failurePolicy: Fail` is on the critical path of pod CREATE. The two replicas stay on distinct nodes (PDB plus `DoNotSchedule` spread). Raising only the CPU limit demotes the pod to Burstable. [k8tz.md](references/k8tz.md)
3. **imageVolume stays off.** Chart 0.20.0 already contains the strategy. Upstream still recommends the init container because imageVolume does not mount `/etc/localtime`. Reopen only when upstream supports that mount and changes the recommendation. Dropping the init container also drops its requests, which is a separate accept.
4. **Admission webhooks mutate CREATE only.** A CronJob born before the webhook keeps a wrong `spec.timeZone` while looking healthy. Fix is delete and recreate, never a hand-patched `spec.timeZone`. HelmRelease-owned objects need `flux reconcile hr <name> --force`. Read the object again after a second reconcile. [webhook-drift.md](references/webhook-drift.md)
5. **CoreDNS answers AAAA with NODATA, and `autopath` stays out while `cache serve_stale` is set.** `serve_stale` turns autopath answers into NXDOMAIN after about 30s, and a DHCP search domain on the node resolver then breaks external names. `pods verified` existed only to feed autopath. The other half is `domains: []` in the machine config (skill `talos-nodes`). Forward upstream is Talos hostDNS `169.254.116.108` via `dnsPolicy: None`.
6. **fstrim must not filter out kubelet mounts.** Every Ceph PV is mounted under `/var/lib/kubelet`. A `grep -v kubelet` recipe trims nothing that matters. The job's awk ranks kubelet-namespace mounts and skips read-only ones.
7. **MGLRU is disabled twice, and both stay.** `machine.sysfs` sets `kernel/mm/lru_gen/enabled` to `0`, and `kube-system/mglru-disable` rewrites the same sysfs every 300s. Do not delete either until a reboot has proved the other still holds. Skill `talos-nodes`.
8. **Multus tolerations cannot be set through chart values.** The chart hardcodes them. The dedicated-taint toleration is a JSON6902 postRenderer. Skill `node-scheduling`.
9. **Descheduler does not clear `PreferNoSchedule` drift.** Policy and thresholds: skill `node-scheduling`.

## Where things live

| What | Path |
|---|---|
| k8tz | `kubernetes/apps/base/system-controller/k8tz/app/helmrelease.yaml` |
| Why the namespaces stay split | [k8tz.md](references/k8tz.md) |
| CREATE-only webhook repair | [webhook-drift.md](references/webhook-drift.md) |
| tuppr upgrades | `kubernetes/apps/base/system-upgrade/tuppr/upgrades/` |
| Talos namespace allowlist | `talos/machineconfig.yaml.j2` `allowedKubernetesNamespaces` |
| CoreDNS | `kubernetes/apps/base/kube-system/coredns/app/helmrelease.yaml` |
| fstrim | `kubernetes/apps/base/system/fstrim/app/helmrelease.yaml` |
| MGLRU loop | `kubernetes/apps/base/kube-system/mglru-disable/app/daemonset.yaml` |
| Multus | `kubernetes/apps/base/kube-system/multus/app/helmrelease.yaml` |
| Descheduler | `kubernetes/apps/base/kube-system/descheduler/app/helmrelease.yaml` |
| Reloader, spegel | `kubernetes/apps/base/kube-system/reloader/`, `.../spegel/` |

## Procedures

- Touch k8tz resources, timezone, or namespace: [k8tz.md](references/k8tz.md).
- A CronJob or pod is missing `TZ` or `spec.timeZone`: [webhook-drift.md](references/webhook-drift.md).
- Reboot or upgrade a node: skill `talos-nodes`, and the Ceph gate in skill `rook-ceph`.

## Verify

- k8tz pods are 2/2, on different nodes, and a new pod's `spec.initContainers` includes the k8tz init with requests equal to limits.
- CoreDNS Corefile has `rcode NOERROR` for AAAA, `serve_stale`, and no `autopath`.
- After a talos-3 reboot, `kernel/mm/lru_gen/enabled` is still `0`.
