# Docs index

Start with the operator files, then the topic notes under `docs/`.

## Operator

| File | What it covers |
|---|---|
| [`AGENTS.md`](../AGENTS.md) (`CLAUDE.md` is a symlink to it) | Day-to-day commands, app layout, secrets, Volsync, networking, agent-facing conventions and anti-patterns |
| [`talos/AGENTS.md`](../talos/AGENTS.md) | Talos templates, render/apply/upgrade |
| [`bootstrap/AGENTS.md`](../bootstrap/AGENTS.md) | First-time / disaster-recovery bootstrap |
| [`README.md`](../README.md) | Cluster overview |

## Topic notes

| Path | Topic |
|---|---|
| [`talosctl.md`](talosctl.md) | Node IPs and a short `talosctl` pointer |
| [`hardware-incidents.md`](hardware-incidents.md) | Hardware / Talos incident log |
| [`ceph/`](ceph/) | Ceph OSD recovery, toolbox, backups |
| [`networking/bgp.md`](networking/bgp.md) | Cilium BGP |
| [`organisation-services-1password-setup.md`](organisation-services-1password-setup.md) | 1Password setup |
| [`kubernetes/components/volsync/Readme.md`](../kubernetes/components/volsync/Readme.md) | Volsync schedules and restore |
| [`ai-system/retired-2026-08-22.md`](ai-system/retired-2026-08-22.md) | What the `ai` namespace retirements kept, and how to revive each app |
| [`branch-protection.md`](branch-protection.md) | `main`'s GitHub ruleset: what's enforced, why only `Labeler` is a required check today, and what closes the gap |

[`flux-migration-validation-report.md`](flux-migration-validation-report.md) is a historical snapshot from 2026-03-29. Do not treat its FAILs as open work.

[`backups/volsync-coverage-2026-08-22.md`](backups/volsync-coverage-2026-08-22.md) is a historical snapshot of a full PVC-vs-VolSync coverage audit from 2026-08-22. Re-measure before trusting any figure in it.
