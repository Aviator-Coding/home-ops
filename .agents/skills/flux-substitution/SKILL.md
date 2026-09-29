---
name: flux-substitution
description: "Read before adding or editing any Flux-reconciled file that can carry a literal ${...} token that is NOT a Flux substitution variable - Grafana dashboard JSON, Helm values, ConfigMaps, container env - and when a Flux Kustomization is stuck with an envsubst error. Covers the three fixes and why neither flate nor task flux:test:all catches this."
---

# Flux postBuild.substitute and literal ${...} tokens

Flux strict-mode envsubst runs over the entire built Kustomization, not
one resource. A literal `${...}` that is not a substitution variable fails
that whole Kustomization. Flux reports the first failure only, so fixing
one collision can reveal the next.

`postBuild.substituteFrom` reads the `cluster-secrets` Secret. Inline
`postBuild.substitute` on the overlay is the per-app map. One map per
Kustomization.

## Tripwires

1. **Neither flate nor `task flux:test:all` catches this.** The cluster
   runs `StrictPostBuildSubstitutions=true` (opt-out since
   kustomize-controller v1.9). flate calls the same `fluxcd/pkg/envsubst`
   in lenient mode: an undefined `${VAR}` expands to empty and the run
   still passes. A green PR can freeze the Kustomization on the next
   reconcile. Detect live with
   `flux get ks -A --status-selector ready=false` (the message names the
   variable).
2. **Three fixes, pick by what the token is.** Do not escape a real
   missing variable, and do not disable substitution on a resource that
   needs `${SECRET_DOMAIN}`.
   [fixes.md](references/fixes.md)
3. **A declared substitute key that no manifest reads is not
   documentation.** Delete it. Only `KOPIUR_*` and `VOLSYNC_*` identity
   variables drive movers. Skill `flux-gitops`.

## Where things live

| What | Path |
|---|---|
| Per-app substitute map | `kubernetes/apps/main/<ns>/<app>.yaml` `postBuild.substitute` |
| Cluster secrets | `cluster-secrets` Secret, referenced by `substituteFrom` |
| Disable on one resource | annotation `kustomize.toolkit.fluxcd.io/substitute: disabled` |
| Escape a literal | `$${...}` in the manifest |

## Verify

- After a dashboard, ConfigMap, or env edit, search the built output for
  `${` that is not a known Flux variable.
- Live: `flux get ks -A --status-selector ready=false`.
