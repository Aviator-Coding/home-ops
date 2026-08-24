# Installation (Flux, this cluster)

AgentGateway is installed by Flux from this repo. Do not `helm upgrade -i kgateway`. There is no `agentgateway.enabled` flag - the standalone chart is the dataplane.

## GitOps layout

```
kubernetes/apps/main/ai/kustomization.yaml     # includes ./agentgateway.yaml
kubernetes/apps/main/ai/agentgateway.yaml      # Flux Kustomizations: agentgateway + agentgateway-crds
kubernetes/apps/base/ai/agentgateway/
  crds/
    ocirepository.yaml                         # oci://cr.agentgateway.dev/charts/agentgateway-crds v1.4.1
    helmrelease.yaml                           # CRDs, helm.sh/resource-policy: keep
  app/
    ocirepository.yaml                         # oci://cr.agentgateway.dev/charts/agentgateway v1.4.1
    helmrelease.yaml                           # controller replicaCount 2, monitoring, parameters ref
    agentgatewayparameters.yaml
    backends/  gateways/  policies/  ...
```

Namespace `ai` is set on the app kustomization. Flux `dependsOn`: `agentgateway-crds` (same ns), `onepassword-store` (security), `certificates-import` (network).

## Helm values that matter

Live values are in [`app/helmrelease.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/helmrelease.yaml). Notes already in that file:

- `resources` is a **top-level** chart value. `controller.resources` is ignored.
- Chart ServiceMonitors are on; chart Grafana dashboard is **off** (vendored separately).
- `gatewayClassParametersRefs.agentgateway` points at `AgentgatewayParameters/agentgateway-params` in `ai`.
- Controller `replicaCount: 2` with PDB `minAvailable: 1` (chart-managed) and hostname anti-affinity.

OCIRepository API version is `source.toolkit.fluxcd.io/v1` (not `v1beta2`). HelmRelease uses `chartRef` to that OCIRepository, not inline `spec.chart.spec.version`.

## CRDs

[`crds/helmrelease.yaml`](../../../kubernetes/apps/base/ai/agentgateway/crds/helmrelease.yaml) installs `agentgateway-crds` and keeps CRDs on Helm uninstall so deleting the chart cannot cascade-delete every backend/policy.

## What the old "Home-Ops install" pages got wrong

`02` and `11` used to paste kgateway v2.1.2 manifests under `kubernetes/apps/ai-system/`, list kagent/kmcp/kgateway as apps, and disagree with each other on `crds: CreateReplace` vs `Skip`. The live app HelmRelease uses `CreateReplace`. The live namespace kustomization is [`kubernetes/apps/main/ai/kustomization.yaml`](../../../kubernetes/apps/main/ai/kustomization.yaml) - no kagent, kmcp, or kgateway.

## Verify

```bash
kubectl -n ai get ocirepository agentgateway agentgateway-crds
kubectl -n ai get helmrelease agentgateway agentgateway-crds
kubectl -n ai get gatewayclass
flux get ks -n ai | grep agentgateway
```
