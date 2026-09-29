---
name: cilium-host-policy
description: "Read before adding or editing any CiliumClusterwideNetworkPolicy, host firewall policy, or nodeSelector-scoped Cilium policy, and before changing hostFirewall settings in the cilium HelmRelease. The default-deny and label-filter traps here can lock all three Talos nodes out at once, needing physical-console recovery."
---

# Cilium host firewall and CiliumClusterwideNetworkPolicy

## Tripwires

1. **A `CiliumClusterwideNetworkPolicy` with a `nodeSelector` and an `ingress:` or `ingressDeny:` must set `enableDefaultDeny: {ingress: false}`.** The CRD defaults that direction to deny. The host endpoint then drops Talos API 50000/50001, Kubernetes API 6443, kubelet 10250, etcd, Cilium health, and BGP 179. Three Talos nodes, no SSH: physical console. Default-deny is OR-ed across policies, so one new CCNP re-arms it for every host policy. Set egress the same way when the policy has egress rules. [gates.md](references/gates.md)
2. **The `nodeSelector` must match a label that exists on the host endpoint, not merely on the Node.** Cilium's default filter strips `kubernetes.io/os` and `kubernetes.io/hostname`. An unmatched selector is `VALID: True`, `policy-enabled: none`, and zero Deny rows. The live selector is `node-role.kubernetes.io/control-plane` Exists. `kubernetes.io/os: linux` matched nothing. [evidence.md](references/evidence.md)
3. **Do not trust the policy until both halves of the bpf gate hold on every node.** Deny rows for the Ceph ports, and `Allow Ingress ANY` still present. Missing Deny means the policy is inert and Ceph is still LAN-exposed. Missing Allow means the host is in default-deny: roll back before debugging. [gates.md](references/gates.md)
4. **Ceph daemons are host-network.** `spec.network.provider: host`. A `NetworkPolicy` or `CiliumNetworkPolicy` in `rook-ceph` does not apply to them. Host policy is the layer that can.
5. **The only live host policy is `cilium/app/hostpolicy-ceph.yaml`.** `enableDefaultDeny` is false in both directions. `policyAuditMode` is false and `hostFirewall.enabled` is true, so it enforces. Do not add a second host policy that omits tripwire 1.

## Where things live

| What | Path |
|---|---|
| Host policy | `kubernetes/apps/base/kube-system/cilium/app/hostpolicy-ceph.yaml` |
| hostFirewall and policyAuditMode | `kubernetes/apps/base/kube-system/cilium/app/helmrelease.yaml` |
| Chart tag | `kubernetes/apps/base/kube-system/cilium/app/ocirepository.yaml` |
| Two-way gate and rollback | [gates.md](references/gates.md) |
| Why the selector and the policy look the way they do | [evidence.md](references/evidence.md) |
| Selector gate | `scripts/ci/hostpolicy-ceph-selector-test.py` |

## Procedures

- Edit the policy or the host-firewall switches, then run the gate in [gates.md](references/gates.md) before considering it live.
- If management traffic drops, roll back in the order in that file. Do not investigate first.

## Verify

- `python3 scripts/ci/hostpolicy-ceph-selector-test.py`
- Live: the §2 commands in [gates.md](references/gates.md). `kubectl get ccnp ceph-lan-isolation` showing Valid is true for an inert policy too.
