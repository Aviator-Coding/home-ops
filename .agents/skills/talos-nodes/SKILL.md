---
name: talos-nodes
description: "Read before editing talos/*.j2, running any just talos recipe, changing kernel args, extensions or Talos/Kubernetes versions, touching tuppr TalosUpgrade or KubernetesUpgrade CRs, rebooting a node, or planning power work. Covers apply-node vs upgrade-node, template pin drift that downgrades nodes, the pre-reboot Ceph gate, never shutdown-node --force, and the talos-3 B70 dock order."
---

# Talos nodes: machine config, upgrades and planned power work

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`talos/AGENTS.md`](../../../talos/AGENTS.md) - render, validate, apply vs upgrade
- [`docs/talosctl.md`](../../../docs/talosctl.md) - talosctl notes
- [`docs/hardware-incidents.md`](../../../docs/hardware-incidents.md) - power-down/up and B70 reboot procedures
- [`kubernetes/apps/base/system-upgrade/tuppr/upgrades/talosupgrade.yaml`](../../../kubernetes/apps/base/system-upgrade/tuppr/upgrades/talosupgrade.yaml) - live upgrade trigger

`AGENTS.md` entries (search for the opening words):

- `talos/machineconfig.yaml.j2`'s 6 version pins
- Before any node reboot
- `just talos shutdown-node` hard-codes `talosctl shutdown --force`
- A talos-3 reboot additionally risks losing the Arc Pro B70

## Related skills

- `rook-ceph`
- `renovate`
- `node-scheduling`
