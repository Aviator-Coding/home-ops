---
name: cilium-host-policy
description: "Read before adding or editing any CiliumClusterwideNetworkPolicy, host firewall policy, or nodeSelector-scoped Cilium policy, and before changing hostFirewall settings in the cilium HelmRelease. The default-deny and label-filter traps here can lock all three Talos nodes out at once, needing physical-console recovery."
---

# Cilium host firewall and CiliumClusterwideNetworkPolicy

Relocated verbatim from `AGENTS.md` on 2026-09-01 so it loads only when this subsystem is in play.
The text below is unchanged; only line breaks were inserted. `AGENTS.md` keeps a one-sentence pointer.
Add new findings here or to the owning document, not back into `AGENTS.md` - see its
"Maintaining this file" section for the rule.

- **NEVER add a `CiliumClusterwideNetworkPolicy` that carries a `nodeSelector` and an `ingress:` (or `ingressDeny:`) section without also setting `enableDefaultDeny: {ingress: false}`.** The 1.18 CCNP CRD defaults default-deny to *true* for any direction that has rules - `ingressDeny` included - so such a policy silently puts the **host endpoint** into ingress default-deny and drops everything not explicitly allowed: Talos API 50000/50001, Kubernetes API 6443, kubelet 10250, etcd, Cilium health, BGP 179 to the router. On 3 Talos nodes with no SSH that is a physical-console recovery. Default-deny is also OR-ed across policies ("enabled if any policy requests it"), so one careless new CCNP re-arms it for every host policy at once. Host firewall is enabled (`hostFirewall.enabled` in `kubernetes/apps/base/kube-system/cilium/app/helmrelease.yaml`); the only host policy is `cilium/app/hostpolicy-ceph.yaml` (enforcing since stage 2; safety is `enableDefaultDeny: false`, not audit mode), which documents the full safety argument. Ceph daemons are host-network (`spec.network.provider: host`), so a plain `NetworkPolicy` in `rook-ceph` cannot reach them at all. Audit evidence: `docs/ceph/lan-isolation-audit-results.md`. Gates and rollback: `docs/ceph/lan-isolation-audit-plan.md`.

- **A CCNP `nodeSelector` must use a label that survives into the *host endpoint's* label set, which is not the node's label set.** Cilium's default label filter strips well-known node labels including `kubernetes.io/os` and `kubernetes.io/hostname` (the upstream host-policy docs example), so an unmatched selector **fails silently and open**: CCNP `VALID: True`, host endpoint `policy-enabled: none`, zero Deny rows - the first Ceph host policy shipped this way. Never trust a host policy until `cilium-dbg bpf policy get <host-endpoint-id>` shows actual `Deny` rows. Measured label sets, the live `node-role.kubernetes.io/control-plane` Exists selector, and the all-control-plane coverage assumption live next to the policy in `kubernetes/apps/base/kube-system/cilium/app/hostpolicy-ceph.yaml`; the two-way post-merge gate is `docs/ceph/lan-isolation-audit-plan.md` §2c.
