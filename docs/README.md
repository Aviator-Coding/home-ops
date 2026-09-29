# Docs

Runbooks live in the skills under `.agents/skills/<skill>/references/`, indexed by the
SKILL INDEX in [`AGENTS.md`](../AGENTS.md). Git history is the archive for dated reports.

## Kept here

The Tdarr node harness reads these files, so they stay in this directory:

- [`tdarr/README.md`](tdarr/README.md): Tdarr rebuild (PVC-only state)
- [`tdarr-errored-remuxes.md`](tdarr-errored-remuxes.md): open captain decisions the harness pins

## Runbooks outside the skills

| When | Runbook |
|---|---|
| Cluster bootstrap / disaster recovery | [`bootstrap/AGENTS.md`](../bootstrap/AGENTS.md) |
| Talos render, apply, upgrade | [`talos/AGENTS.md`](../talos/AGENTS.md) |
| Ceph metadata emergency recovery | [`RECOVERY-PROCEDURES.md`](../kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md) |
| Coder alerts | [`coder/app/runbooks/`](../kubernetes/apps/base/coder/app/runbooks/) |
