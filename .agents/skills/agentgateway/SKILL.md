---
name: agentgateway
description: "Read before editing kubernetes/apps/base/ai/agentgateway/** or the agentgateway dashboards, adding a model, provider or backend, changing routes, listeners, API keys or the cost table, or when requests misroute, return 401 or do not fail over. Covers open http listeners, internal-noauth staying ClusterIP, rule order, failover needing the health policy and /v1, and never capturing response bodies."
---

# agentgateway: routes, listeners, backends and failover

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`docs/ai-system/agentgateway/README.md`](../../../docs/ai-system/agentgateway/README.md) - agentgateway doc set index
- [`kubernetes/apps/base/ai/agentgateway/app/gateways/README.md`](../../../kubernetes/apps/base/ai/agentgateway/app/gateways/README.md) - gateways, listeners and DNS
- [`docs/ai-system/agentgateway-testing-report.md`](../../../docs/ai-system/agentgateway-testing-report.md) - testing report

## Related skills

- `litellm-proxy`
- `networking`
- `ai-stack`
