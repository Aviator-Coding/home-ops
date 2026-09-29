# Docs

Human runbooks live here. Agent knowledge lives in skills under `.agents/skills/`
(index: the SKILL INDEX section of [`AGENTS.md`](../AGENTS.md)); files here that no
runbook below names are being folded into those skills. Git history is the archive for
dated reports.

## Runbooks

| When | Runbook |
|---|---|
| Planned power work, talos-3 B70 reboot | [`hardware-incidents.md`](hardware-incidents.md) (power-down/up and B70 sections) |
| OSD stuck `Init` after a reboot | [`ceph/osd-device-path-recovery.md`](ceph/osd-device-path-recovery.md) |
| OSD crash-looping in `load_pgs` | [`ceph/osd-store-corruption-recovery.md`](ceph/osd-store-corruption-recovery.md) |
| Ceph metadata emergency recovery | [`RECOVERY-PROCEDURES.md`](../kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md) |
| Cluster bootstrap / disaster recovery | [`bootstrap/AGENTS.md`](../bootstrap/AGENTS.md) |
| Full-cluster rebuild: restore every volume from r2 | [`backups/full-cluster-restore-from-r2.md`](backups/full-cluster-restore-from-r2.md) |
| Talos render, apply, upgrade | [`talos/AGENTS.md`](../talos/AGENTS.md) |
| kopiur scratch restore | [`backups/kopiur-restore-drill-2026-08-30.md`](backups/kopiur-restore-drill-2026-08-30.md) |
| VolSync scratch restore | [`backups/restore-drill-2026-08-23.md`](backups/restore-drill-2026-08-23.md) |
| Rebuild a claim whose snapshots will not clone | [`backups/corrupt-claim-recreation-runbook.md`](backups/corrupt-claim-recreation-runbook.md) |
| Expire retired VolSync repositories (owner go-ahead only) | [`backups/volsync-retired-expiry-apply-plan.md`](backups/volsync-retired-expiry-apply-plan.md) |
| Authentik OpenTofu plan / approved apply | [`authentik/terraform.md`](authentik/terraform.md) |
| Claude Code client setup via LiteLLM | [`ai-system/litellm/claude-code-subscription.md`](ai-system/litellm/claude-code-subscription.md) |
| Reading LiteLLM spend logs | [`ai-system/litellm/request-logs.md`](ai-system/litellm/request-logs.md) |
| SABnzbd out of disk space | [`downloads/sabnzbd-disk-space-runbook.md`](downloads/sabnzbd-disk-space-runbook.md) |
| Tdarr rebuild (PVC-only state) | [`tdarr/README.md`](tdarr/README.md) |
| New 1Password item for an ExternalSecret | [`organisation-services-1password-setup.md`](organisation-services-1password-setup.md) |
| UniFi BGP peer config | [`networking/bgp.md`](networking/bgp.md) |
| `main` branch ruleset | [`branch-protection.md`](branch-protection.md) |
| Coder alerts | [`coder/app/runbooks/`](../kubernetes/apps/base/coder/app/runbooks/) |
