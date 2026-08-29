# grafana-operator removal - 2026-08-29

`grafana-operator`'s CRDs were bootstrapped (`bootstrap/helmfile/crds.yaml`) and 10 `kind: GrafanaDashboard`
CRs existed across the tree, but no grafana-operator workload, and no `kind: Grafana` instance CR, was
ever deployed. Investigated and removed on the captain's decision; this is the record.

## What was found

- Grafana in this cluster is a standalone HelmRelease (`kubernetes/apps/base/monitoring/grafana`) running
  the official `grafana/grafana` chart directly - not kube-prometheus-stack's bundled Grafana
  (`kube-prometheus-stack` sets `grafana.enabled: false`), and not grafana-operator.
- `git log --all` has no commit that ever added a grafana-operator `HelmRelease`/workload, and no commit
  that ever added a `kind: Grafana` instance CR. The feature's consumer side (CRs) existed without its
  provider side ever being built.
- The explicit `crds.yaml` entry (chart `oci://ghcr.io/grafana/helm-charts/grafana-operator`, pinned
  version) was not a deliberate "deploy grafana-operator" decision. It was flagged in
  `bootstrap/helmfile/crds.yaml` as the one release with "no OCIRepository in kubernetes/apps", carried
  forward mechanically during an unrelated 2026-05-31 bootstrap restructure (`#881`) that derived every
  other release's chart/version from its app's own `OCIRepository`. Renovate then kept the pinned version
  current (it looked live and maintained) for three months with nothing ever consuming it.
- All 10 `GrafanaDashboard` CRs had permanently empty `.status` on the live cluster - proof zero
  controller reconciliation ever ran against them, not just "not yet". 3 (`cilium`, `cilium-operator`,
  `external-secrets`) were exact duplicates: their `configMapRef` pointed at ConfigMaps the owning app's
  own chart already renders with the `grafana_dashboard` sidecar label, so the content was already live
  in Grafana through the working path. A 4th (`spegel`) referenced a ConfigMap that existed but carried
  neither the sidecar label nor a folder annotation - invisible through *either* mechanism. A 10th CR
  (`system-upgrade/tuppr-dashboard`) existed live with **no corresponding file in git at all** - it was
  rendered by the `tuppr` chart's own `monitoring.dashboards.grafanaOperator.enabled` flag, which a
  2026-05-30 commit (`564f21ea`) turned on one day before the CRD bootstrap entry was made explicit,
  despite the same chart defaulting to a working sidecar ConfigMap when that flag is left off.

## What changed

- Removed the `grafana-operator` release from `bootstrap/helmfile/crds.yaml`.
- Deleted all `kind: GrafanaDashboard` manifests and their kustomization references: `cilium` (2),
  `spegel`, `envoy-gateway` (2), `cloudflare-tunnel`, `cert-manager`, `toolhive`, `external-secrets`.
- `tuppr`: flipped `monitoring.dashboards.grafanaOperator.enabled` to `false` (the chart default), which
  makes it render a plain sidecar ConfigMap instead of the dead CR - the tuppr dashboard is now live
  through the same mechanism as everything else.
- `spegel`: added `grafanaDashboard.labels.grafana_dashboard: "1"` and
  `grafanaDashboard.annotations.grafana_folder: "Spegel"` to its HelmRelease values - the chart already
  ships these as commented-out examples in its own `values.yaml`, they just were never set.
- Re-added the 5 genuinely unique dashboards that had no other delivery path (`cert-manager`,
  `cloudflare-tunnels`, `envoy-gateway`, `envoy-proxy`, `toolhive-mcp-gateway`) as `url:` entries in
  `monitoring/grafana`'s own `dashboards:` values block, using the exact same source URLs the deleted CRs
  used - no dashboard content was lost or substituted.

## What is still outstanding (deliberately not done here)

This PR is manifest/git-only. It does **not** touch the live cluster. Leftovers from the old setup
still need a separate, explicit cleanup pass:

- The `grafana.integreatly.org` CRDs installed by the old bootstrap step (`grafanadashboards`,
  `grafanadatasources`, `grafanafolders`, `grafanaalertrulegroups`, `grafanacontactpoints`,
  `grafanalibrarypanels`, `grafanamutetimings`, `grafananotificationpolicies`,
  `grafananotificationpolicyroutes`, `grafananotificationtemplates`) are cluster-scoped and were applied
  out-of-band by `helmfile`, not by Flux, so removing the bootstrap entry does not retroactively uninstall
  them. Bootstrap stages are not re-run against a healthy cluster (see `bootstrap/AGENTS.md`), so nothing
  in this PR removes them automatically.
- The 9 git-tracked `GrafanaDashboard` CRs still exist live until their owning Flux Kustomizations
  prune them after merge. The already-orphaned `tuppr-dashboard` was chart-rendered (never a git
  file); flipping `grafanaOperator.enabled` off makes the next tuppr Helm upgrade drop it — if it
  somehow remains, delete by hand (command below).
- `kubernetes/apps/base/ai/toolhive/mcp-servers/kubectl-mcp/rbac.yaml` still lists
  `grafana.integreatly.org` in its ClusterRole apiGroups. Harmless while the CRDs remain; drop that
  entry in the same cleanup pass that deletes the CRDs (not done here — functional RBAC change is
  out of scope for the git-only removal).

Cleanup commands for a future, explicitly-approved pass (do not run as part of this change):

```bash
# Confirm nothing still references the CRDs before removing them
kubectl get grafanadashboards -A

# Only if tuppr-dashboard survived the Helm upgrade that disabled grafanaOperator
kubectl delete grafanadashboard tuppr-dashboard -n system-upgrade --ignore-not-found

# Remove the CRDs themselves once no CRs of any kind remain
kubectl get crd -o name | grep grafana.integreatly.org | xargs kubectl delete
```

## Verification

- `task flux:test:all` passes on the resulting tree.
- `kustomize build` on each touched namespace overlay renders no `GrafanaDashboard` kind and no reference
  to a deleted file.
- `grep -rn "kind: GrafanaDashboard" kubernetes/` is empty, and `bootstrap/helmfile/crds.yaml` has no grafana-operator release (prose mentions in this doc, `bootstrap/AGENTS.md`, and `docs/reference.md` are intentional).
