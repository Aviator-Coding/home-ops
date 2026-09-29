---
name: hermes-agent
description: "Read before editing kubernetes/apps/base/ai/hermes/** (config.yaml, HelmRelease, runtime skills), restarting or debugging Hermes, working on its state.db or PVC capacity, its MCP/LLM connectivity, or its samba shared storage. Covers any config byte restarting the pod uncleanly, green status hiding a stuck agent, and never setting vacuum_after_prune: true."
---

# Hermes agent: config, restarts, state.db and routing

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`kubernetes/apps/base/ai/hermes/README.md`](../../../kubernetes/apps/base/ai/hermes/README.md) - setup, config and operations
- [`docs/ai-system/hermes-state-db-growth.md`](../../../docs/ai-system/hermes-state-db-growth.md) - state.db growth and retention

`AGENTS.md` entries (search for the opening words):

- NEVER set `sessions.vacuum_after_prune: true` on `ai/hermes`

## Related skills

- `ai-stack`
- `litellm-proxy`
- `kopiur-backups`
