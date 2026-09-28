---
name: node-scheduling
description: "Read before changing any container requests or limits, tolerations, affinity or topology spread, placing a workload on talos-3, or triaging CPU throttling, OOMKills or evictions. Covers limit-without-request reserving the whole limit, CFS quota and peak sampling, Guaranteed QoS demotion, limits: {} not clearing a chart default, and the talos-3 PreferNoSchedule taint."
---

# Node scheduling: requests, limits, QoS and talos-3 placement

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`docs/talos-3-scheduling-truth.md`](../../../docs/talos-3-scheduling-truth.md) - talos-3 ledger and taint inventory
- [`scripts/ci/memory-request-declared-test.py`](../../../scripts/ci/memory-request-declared-test.py) - memory-request gate
- [`scripts/ci/cpu-throttle-contract-test.py`](../../../scripts/ci/cpu-throttle-contract-test.py) - CPU throttle gate

`AGENTS.md` entries (search for the opening words):

- A container declaring `limits.memory` with no `requests.memory`
- A CPU limit is an ABSOLUTE CFS quota
- An empty `resources.limits: {}` override

## Related skills

- `talos-nodes`
- `b70-llm-serving`
