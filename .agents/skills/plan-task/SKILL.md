---
name: plan-task
description: "Read before starting any GitHub issue or owner request that changes the repo. Covers the risk tier from planned paths, the 2-open-PR limit, the plan comment format, and stopping for owner approval on risk:high. Workflow step 1-2 of docs/goals/2026-10-30-agent-maintainable-home-ops.md §5."
---

# Plan a task

The goal doc is the authority: [§3 boundaries](../../../docs/goals/2026-10-30-agent-maintainable-home-ops.md#3-boundaries),
[§5 loop](../../../docs/goals/2026-10-30-agent-maintainable-home-ops.md#5-workflow-target),
[§6 models](../../../docs/goals/2026-10-30-agent-maintainable-home-ops.md#6-subagent-model-selection).
This skill is the checklist for steps 1 and 2. Then use `implement-task`.

## Before planning

1. **Capacity.** At most 2 open agent PRs. Count them:
   `gh pr list --author @me --state open --json number,title`. Until agents have their own
   identity (goal task 2.6) this also lists the owner's PRs; count only yours. At 2, stop and
   report; read-only investigation is fine, opening a third PR is not.
2. **Boundaries.** If the task needs a cluster write, a ruleset/label/settings change via
   `gh api`, retiring an app, changing a backup, or merging Renovate config, it needs the
   owner's go-ahead in that moment. Plan it as an **OWNER** step; never do it yourself.
3. **Read** the issue, the skill for every subsystem the paths touch (AGENTS.md SKILL INDEX),
   and `git log --oneline -10 -- <paths>`.
4. **Deleting a test or skill text?** Each removed item first gets a row in
   [`knowledge-ledger.md`](../../../docs/goals/knowledge-ledger.md) with a verdict
   (`enforce`, `keep`, `archive`). A row that prevents data loss or an outage is never
   `archive`. Put the rows in the same PR.

## Risk tier

The PR's tier is set by CI from its paths (`risk-tier.yaml`); forecast it the same way:

```bash
printf '%s\n' <planned paths> | python3 scripts/ci/risk_tier.py
```

If the script is absent, apply the goal §5 table by hand.
The highest path wins. `risk:high` includes talos/, bootstrap/, terraform/, clusters/,
flux-system, kube-system, rook-ceph, network, security, cert-manager, system-upgrade,
renovate, kopiur/volsync components, `.github/workflows/`, Renovate config and the agent
guard. Agent-steering files (AGENTS.md, `.claude/**`, skill scripts) are never low.

## The plan comment

Post it on the issue (`gh issue comment <n> --body-file plan.md`):

```markdown
## Plan
**Tier:** risk:<tier> (paths: …)  **Implementer:** <tier: cheap|standard|strongest>  **Reviewer:** <tier>
**Approach:** 2-5 bullets, the decision and why, not a diff.
**Files:** each path and what changes.
**Rollback:** revert the PR, plus any state or manual step.
**Verification:** commands before merge (local gates) and after (Flux, workload health).
**Owner steps:** anything only the owner may run, in order.
```

- `risk:high`: write the plan with the strongest model (§6), then stop. Start only after the
  owner replies approving the plan.
- `risk:normal` / `risk:low`: continue with `implement-task`.

## Splitting

One task, one branch, one PR. If the plan needs two tiers or two subsystems that can merge
independently, split it into two issues. Goal checkbox ticks and §8 evidence rows that can
only be written after the merge go in a later `risk:low` docs PR (it counts toward the
2-PR limit).
