# kgateway is not deployed here

This cluster does **not** run kgateway.

AgentGateway and kgateway started as one product. They split in 2026:

- **kgateway** remains an Envoy-based Kubernetes gateway (charts at `cr.kgateway.dev`).
- **AgentGateway** is a standalone Rust AI gateway (charts at `cr.agentgateway.dev`, 1.x line).

This cluster migrated on 2026-06-06 in `e4321cb2` (#938) from the deprecated `ghcr.io/kgateway-dev/charts/agentgateway` snapshot (`v2.3.0-main`) to `oci://cr.agentgateway.dev/charts/agentgateway`. The `ai-system` namespace merged into `ai` the next day (`0e0a5fb9`, #943).

Non-AI HTTP ingress here is **Envoy Gateway** in `network/` (`envoy-internal` / `envoy-external`), not kgateway. There is no `kgateway/` app under `kubernetes/apps/`, and no `kgateway-system` namespace.

For the live AI dataplane, see [AgentGateway](../agentgateway/README.md). Upstream kgateway docs live at <https://kgateway.dev/>.
