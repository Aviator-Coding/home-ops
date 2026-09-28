---
name: pvc-integrity-checks
description: "Read before editing kubernetes/apps/base/system/pvc-writable-check/** or pvc-mover-readable-check/**, adding a namespace that holds PVCs, changing the securityContext of a pod that mounts a PVC, or triaging PVC writable or mover-readable alerts. Covers the split pods/exec RBAC, RoleBindings for new namespaces, UNMEASURED never being a pass, and alert-on-kopiur vs report-only-for-VolSync."
---

# PVC integrity checks: writable and mover-readable CronJobs

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`kubernetes/apps/base/system/pvc-writable-check/app/README.md`](../../../kubernetes/apps/base/system/pvc-writable-check/app/README.md) - writable check design and RBAC
- [`kubernetes/apps/base/system/pvc-mover-readable-check/app/README.md`](../../../kubernetes/apps/base/system/pvc-mover-readable-check/app/README.md) - mover-readable check and walk traps

`AGENTS.md` entries (search for the opening words):

- An app can be denied write access to its own PVC

## Related skills

- `kopiur-backups`
- `app-workloads`
