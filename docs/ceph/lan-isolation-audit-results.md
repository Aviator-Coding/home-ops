# Ceph LAN isolation evidence

Why the host policy looks the way it does: skill `cilium-host-policy`, [`.agents/skills/cilium-host-policy/references/evidence.md`](../../.agents/skills/cilium-host-policy/references/evidence.md).

The policy enforces (`policyAuditMode: false`, `hostFirewall.enabled: true`). `enableDefaultDeny` is false in both directions. The selector is `node-role.kubernetes.io/control-plane` Exists. `kubernetes.io/os: linux` matched nothing on the host endpoint.
