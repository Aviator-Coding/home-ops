# AgentGateway migrations (this cluster)

This page is the **actual** history of this GitOps install. It is not a kgateway 2.1 -> 2.2 -> 3.0 upgrade matrix. That product line is not what runs here.

## What we run now

- Chart: `oci://cr.agentgateway.dev/charts/agentgateway` tag **`v1.4.1`**
- CRDs: `oci://cr.agentgateway.dev/charts/agentgateway-crds` tag **`v1.4.1`**
- Namespace: `ai`
- API: `agentgateway.dev/v1alpha1` (`AgentgatewayBackend`, `AgentgatewayPolicy`, `AgentgatewayParameters`)

Bump the two OCIRepository tags together. Re-vendor the Grafana dashboard JSON in `kubernetes/apps/base/ai/agentgateway-dashboards/` when the chart dashboard changes (comment on that kustomization).

## 2026-06-06: split from kgateway (`e4321cb2`, #938)

**From:** `oci://ghcr.io/kgateway-dev/charts/agentgateway` tag `v2.3.0-main` (moving main-branch snapshot of the deprecated kgateway-dev distribution).

**To:** `oci://cr.agentgateway.dev/charts/agentgateway` tag `1.2.1` (standalone 1.x).

Dropped kgateway-isms:

- Helm value `agentgateway.enabled` (standalone chart is always-on)
- `KGW_*` environment variables
- `gateway.kgateway.dev` kinds (`Backend`, `TrafficPolicy`, `GatewayParameters`, ...)

Upstream: kgateway v2.2 separated the controllers; AgentGateway v1.0 ships its Kubernetes controller in-tree and versions with the binary. Charts live at `cr.agentgateway.dev`.

## 2026-06-07: namespace merge (`0e0a5fb9`, #943)

**From:** `ai-system`

**To:** `ai`

Gateway YAML still comments the IP pins as surviving that move. In-cluster Service DNS is `*.ai.svc.cluster.local` (for example `internal-noauth.ai.svc.cluster.local`).

## 2026-06-22 .. 2026-07-31: 1.x patch line

`v1.3.0` -> `v1.3.1` -> **`v1.4.1`** (`88994e19`, `c97aff63`, `999ea171`). Renovate tracks the standalone chart, not kgateway v2.x.

Admin UI path: v1.3.0 serves the dashboard at `/ui`. Bare `/` 308-redirects to an `http://` URL that dies behind TLS, so Envoy HTTPRoutes issue a same-scheme 302 to `/ui/`. See comments on `httproute.yaml` and `httproute-internal.yaml`.

## Routing model change (after the March 2026 test)

The [historical testing report](../agentgateway-testing-report.md) used per-provider prefixes (`/openai`, `/anthropic`, `/groq`, ...). Live traffic is unified `/v1` with model-name routing. Backend comments still say "No per-provider path route exists anymore."

Aggregator base paths that those old prefix routes rewrote are now injected **per unified-route rule** (Groq `/openai`, OpenRouter `/api`, OpenCode Go `/zen/go`). See the header on `httproute-unified.yaml`.

## What not to migrate with

Do not:

- Point Flux at `cr.kgateway.dev` or `ghcr.io/kgateway-dev`
- Set `agentgateway.enabled: true`
- Apply `kind: Backend` / `TrafficPolicy` from `gateway.kgateway.dev`
- Patch HelmRelease `spec.chart.spec.version` - the live HelmRelease uses `chartRef` + OCIRepository
- Target namespace `ai-system` or `kgateway-system`
- Restore LiteLLM as the `/v1` proxy

## CRD uninstall guard

The CRD HelmRelease post-renderer sets `helm.sh/resource-policy: keep` so uninstalling the CRD chart does not cascade-delete every `AgentgatewayBackend` / `AgentgatewayPolicy` / `AgentgatewayParameters`. See `crds/helmrelease.yaml`.
