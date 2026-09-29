---
name: flux-gitops
description: "Read before adding an app or overlay (kubernetes/apps/base/<ns>/<app>, kubernetes/apps/main/<ns>/<app>.yaml), editing a Flux Kustomization's healthChecks, wait, dependsOn or postBuild, live-testing a HelmRelease with kubectl, or debugging a Kustomization or HelmRelease that is not Ready. Covers health-check targets, suspend-before-edit, and why flate -n gives a false green."
---

# Flux GitOps: apps, overlays, health checks and live testing

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`docs/app-structure.md`](../../../docs/app-structure.md) - app and overlay layout
- [`scripts/add-app/README.md`](../../../scripts/add-app/README.md) - app generator
- [`docs/flux-migration-validation-report.md`](../../../docs/flux-migration-validation-report.md) - flate migration evidence

`AGENTS.md` entries (search for the opening words):

- `kubernetes/clusters/main/` is the only Flux entry point
- A namespace-scoped manifest test still cannot prove a namespace is healthy
- A controller that has been failing for a while does not retry
- `healthChecks:` must target the workload, not the HelmRelease
- The `flux-system` namespace object is exempt from `cluster-apps` reconciliation
- A Flux `Kustomization`'s own reconcile interval will silently clobber live edits

## Related skills

- `flux-substitution`
- `app-workloads`
