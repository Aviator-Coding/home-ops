# Docs index

Start with the operator files (outside `docs/`), then every markdown file under `docs/`.

`find docs -type f -name '*.md'` is **57**. Each of those files is listed exactly once below, including this index. Rows marked **historical snapshot** are dated captures, not current runbooks.

Paths that live in different directories for the same subsystem (AI, Ceph, network) are grouped together here on purpose. The files themselves were not moved.

## Operator

These are not under `docs/`. They are the day-to-day starting points.

| File | What it covers |
|---|---|
| [`AGENTS.md`](../AGENTS.md) (`CLAUDE.md` is a symlink to it) | Day-to-day commands, app layout, secrets, Volsync, networking, agent-facing conventions and anti-patterns |
| [`talos/AGENTS.md`](../talos/AGENTS.md) | Talos templates, render/apply/upgrade |
| [`bootstrap/AGENTS.md`](../bootstrap/AGENTS.md) | First-time / disaster-recovery bootstrap |
| [`README.md`](../README.md) | Cluster overview |
| [`kubernetes/components/volsync/Readme.md`](../kubernetes/components/volsync/Readme.md) | Volsync schedules, multi-volume pattern, and restore |

## AI

GPU hardware, the live `ai` namespace stack (Hermes, ToolHive, AgentGateway, LiteLLM), and the retired-app tombstones. Flat `docs/ai-gpu-changelog.md`, `docs/ai/`, and `docs/ai-system/` are listed together.

| Path | What it covers |
|---|---|
| [`ai-gpu-changelog.md`](ai-gpu-changelog.md) | Chronological log of applied `ai` / B70 GPU changes, evidence, and rollbacks. Use when something in `ai` or on the B70 broke after a config change. |
| [`ai/gpu-dra-migration-design.md`](ai/gpu-dra-migration-design.md) | Complete DRA migration design and why it is not shipped (vendor driver non-production; cannot share one GPU). Read before retrying DRA; do not apply. |
| [`ai/b70-llm-serving-tuning.md`](ai/b70-llm-serving-tuning.md) | Live B70 llama.cpp SYCL serving args, measured A/B matrix, and current ctx/embed settings. Use when changing vLLM/llama.cpp flags or debugging decode speed. |
| [`ai/b70-second-card-decision.md`](ai/b70-second-card-decision.md) | Decision memo (2026-06-26; DEFER still stands) on why a second B70 is not authorized. Read before proposing another GPU purchase. |
| [`ai-system/litellm/README.md`](ai-system/litellm/README.md) | Live LiteLLM governance layer (captain decisions B4/D4): what it is, what it is not, and where manifests live. Start here for any LiteLLM work. |
| [`ai-system/litellm/auto-router.md`](ai-system/litellm/auto-router.md) | D3 `auto` complexity-tier router: classifier, thinking-off trap, fail-open, and alerts. Use when changing routing tiers or debugging why everything stays local. |
| [`ai-system/litellm/fallbacks.md`](ai-system/litellm/fallbacks.md) | Phase 5 availability and context-window fallbacks, plus the config-fallback allow-list bypass. Read before adding a cloud fallback or proving failover. |
| [`ai-system/litellm/claude-code-subscription.md`](ai-system/litellm/claude-code-subscription.md) | Claude Code Max/Pro pass-through: OAuth token forwarding, $0 pricing, no cluster Anthropic key. Use when wiring the `claude` CLI through the proxy. |
| [`ai-system/litellm/request-logs.md`](ai-system/litellm/request-logs.md) | How to read stored prompts, responses, and cost from Postgres spend logs. Use when investigating what a caller sent or spent. |
| [`ai-system/agentgateway/README.md`](ai-system/agentgateway/README.md) | Cluster-specific AgentGateway install (standalone v1.4.1 in `ai`). Start here; do not follow kgateway docs. |
| [`ai-system/agentgateway/01-quickstart.md`](ai-system/agentgateway/01-quickstart.md) | Live-cluster smoke commands for the three Gateways and the unified `/v1` route. |
| [`ai-system/agentgateway/02-installation.md`](ai-system/agentgateway/02-installation.md) | Flux install tree (CRDs + chart). Use this instead of `helm upgrade -i kgateway`. |
| [`ai-system/agentgateway/03-gateway-setup.md`](ai-system/agentgateway/03-gateway-setup.md) | The three data planes (`internal`, `internal-noauth`, `public`) and their IPs/listeners. |
| [`ai-system/agentgateway/04-llm-providers.md`](ai-system/agentgateway/04-llm-providers.md) | Model-name routing on unified `/v1` and how to add an `AgentgatewayBackend`. |
| [`ai-system/agentgateway/05-mcp-connectivity.md`](ai-system/agentgateway/05-mcp-connectivity.md) | MCP is not federated through AgentGateway here; ToolHive owns it. |
| [`ai-system/agentgateway/06-agent-connectivity.md`](ai-system/agentgateway/06-agent-connectivity.md) | In-cluster clients: kagent is gone; keyless OpenAI endpoint to use instead. |
| [`ai-system/agentgateway/07-security.md`](ai-system/agentgateway/07-security.md) | Authentik extAuth on HTTPS plus API-key Strict on `public` HTTP. |
| [`ai-system/agentgateway/08-observability.md`](ai-system/agentgateway/08-observability.md) | What to scrape (port 15020 PodMonitor) and which kgateway dashboards not to import. |
| [`ai-system/agentgateway/09-advanced-features.md`](ai-system/agentgateway/09-advanced-features.md) | Live failover via `spec.ai.groups` on `AgentgatewayBackend`. |
| [`ai-system/agentgateway/10-api-reference.md`](ai-system/agentgateway/10-api-reference.md) | CRDs this cluster actually applies (`AgentgatewayBackend` / `Policy` / `Parameters`). |
| [`ai-system/agentgateway/11-cluster-deployment.md`](ai-system/agentgateway/11-cluster-deployment.md) | Live GitOps tree. Older fictional `ai-system/` snippets in prior versions of this file must not be applied. |
| [`ai-system/agentgateway/12-troubleshooting.md`](ai-system/agentgateway/12-troubleshooting.md) | Commands that work in namespace `ai` (not `ai-system` / kgateway kinds). |
| [`ai-system/agentgateway/13-function-calling.md`](ai-system/agentgateway/13-function-calling.md) | Gateway-federated MCP tools are not wired; client-side tools still pass through. |
| [`ai-system/agentgateway/14-session-management.md`](ai-system/agentgateway/14-session-management.md) | No gateway-side sessions; `AgentgatewayParameters` only sets `ADMIN_ADDR`. |
| [`ai-system/agentgateway/15-optimization.md`](ai-system/agentgateway/15-optimization.md) | Live replica, resource, and cost-rule capacity. Ignore generic HPA/price tables from old guides. |
| [`ai-system/agentgateway/GLOSSARY.md`](ai-system/agentgateway/GLOSSARY.md) | Cluster-local terms (`AgentgatewayBackend`, unified `/v1`, vs Envoy Gateway). |
| [`ai-system/agentgateway/MIGRATION.md`](ai-system/agentgateway/MIGRATION.md) | Actual GitOps chart history (kgateway-dev snapshot to standalone 1.x), not a kgateway upgrade matrix. |
| [`ai-system/agentgateway-testing-report.md`](ai-system/agentgateway-testing-report.md) | **Historical snapshot** (2026-03-31, kgateway-dev v2.3.0-main, namespace `ai-system`, per-provider URL prefixes). Do not copy its commands; use [`agentgateway/`](ai-system/agentgateway/README.md) instead. |
| [`ai-system/kagent/README.md`](ai-system/kagent/README.md) | Tombstone: kagent was removed 2026-06-07. Do not install kagent charts or `kagent.dev` CRDs. |
| [`ai-system/kgateway/README.md`](ai-system/kgateway/README.md) | Tombstone: kgateway is not deployed. Non-AI ingress is Envoy Gateway in `network`. |
| [`ai-system/kmcp/README.md`](ai-system/kmcp/README.md) | Tombstone: kmcp was removed 2026-06-07. MCP is ToolHive (`toolhive.stacklok.dev/v1alpha1`). |
| [`ai-system/retired-2026-08-22.md`](ai-system/retired-2026-08-22.md) | What the 2026-08-22 `ai` retirements kept (Postgres, 1Password, restic repos) and how to revive each app. |

## Authentik

| Path | What it covers |
|---|---|
| [`authentik/terraform.md`](authentik/terraform.md) | Authentik OpenTofu stack: inventory, import vs create, RGW state bucket, apply approval gate, CI read-only plans. Read before any `tofu plan` / `tofu apply`. |

## Backups

| Path | What it covers |
|---|---|
| [`backups/restore-drill-2026-08-23.md`](backups/restore-drill-2026-08-23.md) | Verified VolSync restore procedure (Ceph + MinIO destinations, scratch-PVC method). Timings are a **historical snapshot** from 2026-08-23; the procedure is durable. |
| [`backups/volsync-coverage-2026-08-22.md`](backups/volsync-coverage-2026-08-22.md) | **Historical snapshot** of a full PVC-vs-VolSync coverage audit from 2026-08-22. Re-measure before trusting any figure. Current pattern: [`kubernetes/components/volsync/Readme.md`](../kubernetes/components/volsync/Readme.md). |

## Ceph

Flat `docs/ceph-cluster-changelog.md` and `docs/ceph-performance-review.md` are listed with `docs/ceph/`.

| Path | What it covers |
|---|---|
| [`ceph-cluster-changelog.md`](ceph-cluster-changelog.md) | Authoritative log of applied Ceph changes (CephX rotation, `rgw_sigv4_insecure`, PG/OSD tunables) and rollback notes. Start here for current Ceph truth. |
| [`ceph-performance-review.md`](ceph-performance-review.md) | **Historical snapshot** of the 2026-06-01 performance review. Do not re-apply; current tunables live in the changelog. |
| [`ceph/backup-recovery-strategy.md`](ceph/backup-recovery-strategy.md) | Live Ceph metadata CronJob backup (not last-resort DR) and pointer to emergency recovery procedures. |
| [`ceph/lan-isolation-audit-plan.md`](ceph/lan-isolation-audit-plan.md) | Host-policy gates, rollback, and current status (stage 2 enforcing since 2026-08-25). Read before changing the Ceph CCNP. |
| [`ceph/lan-isolation-audit-results.md`](ceph/lan-isolation-audit-results.md) | **Historical snapshot** of the 25h stage-1 audit window that justified flipping `policyAuditMode` off. |
| [`ceph/osd-device-path-recovery.md`](ceph/osd-device-path-recovery.md) | Runbook when an OSD pod is stuck `Init` after reboot (`/dev/nvmeXn1` drift). Run `task rook:check-osd-device-paths` first. |
| [`ceph/osd-store-corruption-recovery.md`](ceph/osd-store-corruption-recovery.md) | Runbook when an OSD crash-loops in `load_pgs` (BlueStore OMAP corruption), distinct from device-path drift. |
| [`ceph/pg.md`](ceph/pg.md) | Placement-group autoscaler / `bulk` background. Changelog owns current counts; this is generic PG context. |
| [`ceph/toolbox.md`](ceph/toolbox.md) | How to enable and use the Rook Ceph toolbox for `ceph` CLI operations. |

## Downloads and media

| Path | What it covers |
|---|---|
| [`downloads/sabnzbd-disk-space-runbook.md`](downloads/sabnzbd-disk-space-runbook.md) | SABnzbd "Too little diskspace" / `shared-downloads` CephFS full. Use when the movie pipeline is paused. |
| [`media-stack.md`](media-stack.md) | Architecture of downloads to *arr to transcode to media servers. Use to see how the pipeline fits together. |

## Network

`docs/network/` and `docs/networking/` are listed together. They were not merged.

| Path | What it covers |
|---|---|
| [`networking/bgp.md`](networking/bgp.md) | Current Cilium BGP facts (ASNs, peers, LB pool, L2 off). Start here for BGP. |
| [`network/cmd.md`](network/cmd.md) | **Historical snapshot** (2025-08-16) of node networking during the `10.10.3.0/24` to `10.10.10.0/24` cutover. Not current node networking. |
| [`network/envoy-gateway-internal-domains-analysis-2026-07.md`](network/envoy-gateway-internal-domains-analysis-2026-07.md) | **Historical snapshot** (2026-07-03/04) of an internal-domain outage analysis. Do not use for current chart versions or hostname counts. |

## Persistent volumes

| Path | What it covers |
|---|---|
| [`pvc/pvc-health-checks.md`](pvc/pvc-health-checks.md) | How Flux waits for PVCs to bind before deploying, plus troubleshooting unbound claims. |

## Cluster and repo

| Path | What it covers |
|---|---|
| [`branch-protection.md`](branch-protection.md) | `main`'s GitHub ruleset: what is enforced, why only Labeler is a required check today, and what closes the gap. |
| [`hardware-incidents.md`](hardware-incidents.md) | Hardware / Talos incident log (root cause, evidence, resolution). Use for node memory, DIMM RMA, and similar failures. |
| [`talosctl.md`](talosctl.md) | Node IPs (not the VIP) and a short pointer to `just talos` recipes. |
| [`organisation-services-1password-setup.md`](organisation-services-1password-setup.md) | How to create 1Password items for a new organisation service consumed by ExternalSecret. |
| [`flux-migration-validation-report.md`](flux-migration-validation-report.md) | **Historical snapshot** from 2026-03-29 of the pre-base/main Flux layout. Do not treat its FAILs as open work. |
| [`grafana-operator-removal.md`](grafana-operator-removal.md) | Why the 10 `GrafanaDashboard` CRs and the grafana-operator CRD bootstrap were dead, what replaced them, and outstanding live-cluster cleanup. |
| [`reference.md`](reference.md) | This index. |
