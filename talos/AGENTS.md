# Talos

Render, apply, upgrade, pin drift and the pre-reboot Ceph gate: skill `talos-nodes` (`.agents/skills/talos-nodes/SKILL.md`).

Human runbooks:

- Planned power-down and power-on: `docs/runbooks/power-down-up.md`
- talos-3 B70 reboot: `docs/runbooks/talos-3-b70-reboot.md`

`talosctl validate` is a schema check. It does not catch a bad enum, a bad CIDR, or a missing install disk. Green validate is not permission to `apply-node`.

`just bootstrap cluster` is disaster recovery only. See `bootstrap/README.md`.
