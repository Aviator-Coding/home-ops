# Ceph LAN isolation gate

The post-merge gate and rollback live in skill `cilium-host-policy`: [`.agents/skills/cilium-host-policy/references/gates.md`](../../.agents/skills/cilium-host-policy/references/gates.md).

What older notes called §2c is the two-way bpf check: Deny rows for the Ceph ports, and `Allow Ingress ANY` still present, on every host endpoint. CCNP `VALID: True` holds either way.

What older notes called §6 is the rollback order in that same file: `policyAuditMode: true`, then delete the CCNP, then the Talos API or the physical console.
