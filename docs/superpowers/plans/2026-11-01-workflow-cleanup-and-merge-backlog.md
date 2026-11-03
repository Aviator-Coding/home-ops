# Workflow Cleanup and Merge Backlog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the agent workflow from generating GitHub issue and comment traffic, close the noise already created, and drain the 18 open Renovate PRs in safe order.

**Architecture:** The goal doc (`docs/goals/2026-10-30-agent-maintainable-home-ops.md` §1, §5) made GitHub issues the work queue: plan comments on issues, an issue per major bump, a daily metrics issue. We keep the PR gates (`CI Success`, risk labels, AI review) and drop the issue ceremony. Plans stay in the session or the PR body. Issues are for things the owner files.

**Tech Stack:** `gh`, GitHub Actions, markdown skills, Flux/Renovate PRs.

**Spec:** `docs/goals/2026-10-30-agent-maintainable-home-ops.md`

## Global Constraints

- Agents never merge (`implement-task`, `renovate-review`); the owner merges, or explicitly says "merge these".
- Closing issues and any `gh api` repo/label/ruleset change need the owner's go-ahead (goal doc §3).
- Any change to `.renovaterc.json5` or `.renovate/**` needs the in-cluster Renovate CronJob suspended first. This plan touches neither.
- `talosupgrade.yaml`/tuppr/Talos/Cilium/Ceph bumps are unattended node or cluster changes: owner picks the window.
- Commits `type(scope): description`; pre-commit hooks must run; skills/docs are bound by `scripts/ci/docs-budget-test.py`.

## Review Focus

- `goal-metrics.yaml` posts to issue #2161 daily; closing the issue without changing the workflow makes it fail or reopen noise.
- `nightly-tests.yaml` opens/comments an issue on failure (`scripts/ci/ci-test-gate-test.py:137` pins `"gh issue"`); changing it needs that test updated together.
- Removing the plan-comment step may break a doc-reading CI test; grep `scripts/ci/` for `plan-task` and "plan comment" before editing.
- Merging several PRs touching the same file (e.g. Talos pins in #2126/#2087/#2114) causes rebase conflicts; merge one, let Renovate rebase.
- A "green" PR with an empty `CI Success` result (most PRs below show none yet) is not mergeable evidence.

---

## Current state (measured 2026-11-01)

- 11 open issues: 8 by the owner's account (agent-written), 2 by github-actions, 1 Renovate dashboard (#489).
  - Pure workflow noise: #2161 (metrics), #2188-#2192 (one per major bump, each duplicating an open PR or a dashboard bump).
  - Auto-generated: #2159 (nightly failed: `scripts/ci/litellm-sso-test.py`).
  - Real work: #2215 (kubectl MCP crash-loop), #2210 (guard gaps), #2202 (retire VolSync carve-outs).
- 18 open PRs, all Renovate. `CI Success` SUCCESS on 8: #2213 #2174 #2171 #2152 #2151 #2135 #2134 #2126 #2104 (checks pending/empty on the rest); FAILURE on #2115.

## Task 1: Merge the green, low-risk PRs

**Files:** none (GitHub only)

**Interfaces:** Produces: a shorter PR list for Task 3. Consumes: owner go-ahead to merge.

- [ ] **Step 1: Re-check state right before merging**

Run: `gh pr list --state open --json number,mergeStateStatus,statusCheckRollup`
Expected: each PR below shows `CI Success` = SUCCESS and mergeable.

- [ ] **Step 2: Owner merges, in this order, one at a time (let Renovate rebase between)**

  1. #2213 cli-proxy-api patch (risk:low)
  2. #2135 uv minor (mise)
  3. #2134 helm 4.2.4 → 4.3.1 (mise tool; read release notes, Helm 4 is a tripwire for postRenderer)
  4. #2104 ntfy minor
  5. #2174 Node Exporter dashboard 45 → 46 (after Step 3 in Task 2 closes #2192; check dashboard still renders in Grafana)
  6. #2152 intel-device-plugins 0.34.1 → 0.37.1 (**intel-gpu skill: run the VA-API check after reconcile**)

- [ ] **Step 3: After each merge**: `flux get ks -A | grep -v True` shows nothing new; `flux get hr -A | grep -v True` likewise.

## Task 2: Close the issue noise

**Files:**
- Modify: `.github/workflows/goal-metrics.yaml` (write to a committed CSV, or delete the workflow)
- Test: `scripts/ci/` tests referencing goal-metrics (grep first)

**Interfaces:** Consumes: owner go-ahead to close issues. Produces: open-issue list reduced to real work.

- [ ] **Step 1: Find tests pinning the workflow**

Run: `grep -rn "goal-metrics\|2161" scripts/ci .github docs | head`
Expected: list of everything to update together.

- [ ] **Step 2: Decide metrics sink (owner)**: recommend deleting `goal-metrics.yaml`; the D2/D3 numbers are derivable from `gh` on demand. Alternative: append to `docs/goals/metrics.csv` by PR.

- [ ] **Step 3: Remove/convert the workflow, update pinned tests, run** `for t in scripts/ci/*-test.py; do python3 "$t"; done` Expected: all pass.

- [ ] **Step 4: Close with a one-line reason** (after owner OK):
  `gh issue close 2161 2188 2189 2190 2191 2192 --comment "Workflow cleanup: tracked on the PR/dashboard instead."`
  Each major bump still has its open Renovate PR (#2115/#2116, #2119, #2100, #2120, #2174), which is where the recommendation comment already lives.

- [ ] **Step 5: Handle #2159** by fixing `scripts/ci/litellm-sso-test.py` (separate small PR) or, if the failure is a known offline-only flake, closing it; the nightly workflow re-opens only on a new failure.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "ci(github-action): drop the daily goal-metrics issue"
```

## Task 3: Triage the remaining PRs (no merging without a window)

**Files:** none

**Interfaces:** Consumes: Task 1 result. Produces: a single hand-off table (short, per the owner's "fewer round trips" preference).

- [ ] **Step 1: Hold list, each with the reason, nothing posted as a new issue**

| PR | Action |
|---|---|
| #2171 tuppr patch (risk:high), #2087/#2126 Talos 1.14.5, #2114 Kubernetes 1.36.6 | hold; unattended node roll, owner picks the window, one at a time with the Ceph HEALTH_OK gate |
| #2151 Cilium 1.18.6 → 1.18.16 (risk:high) | hold; read release notes, merge in a window |
| #2119 Ceph v21 | hold; needs Rook 1.21 first, one-way |
| #2100 n8n 3.0 (pre-release) | close or hold |
| #2116/#2115 Node 26 | #2115 fails CI; hold both |
| #2120 ha-mcp 9 | read release notes, then decide |
| #2088 mcp-server-kubernetes | merge together with fixing #2215 (crash-loop since 2.0.0) |
| #1991 python 3.15 | hold (no fastuuid wheel) |

- [ ] **Step 2: For PRs with empty checks** run `gh pr checks <n>` and `gh run list --branch <head>`; if never triggered, comment `@renovate rebase` is not needed—push a rebase by closing/reopening is **not** done; ask the owner.

## Task 4: Change the workflow so this does not recur

**Files:**
- Modify: `docs/goals/2026-10-30-agent-maintainable-home-ops.md` §1 (steps 1-2), §5 (step 2, "Post a plan comment"), 4.2 and 4.5 checklist rows
- Modify: `.agents/skills/plan-task/SKILL.md`, `.agents/skills/renovate-review/SKILL.md` ("a separate issue per major bump")
- Test: `scripts/ci/docs-budget-test.py` and any test grepping these strings

**Interfaces:** Consumes: Task 2 decisions. Produces: rules agents read.

- [ ] **Step 1: Grep** `grep -rn "plan comment\|separate issue\|issue per" .agents docs/goals scripts/ci`
- [ ] **Step 2: New rule text**: the plan is shown to the owner in the session (or the PR body for `risk:high`); agents open an issue only when the owner asks; a major bump is tracked by its Renovate PR plus one recommendation comment.
- [ ] **Step 3: Run all CI gates** `for t in scripts/ci/*-test.py; do python3 "$t"; done`; Expected: pass, including docs-budget (shrinks the files, no allowlist needed).
- [ ] **Step 4: Commit** `git commit -m "docs(github): plans stay in session or PR; no issue per task or major bump"`

## Task 5: Real work from the issues (separate PRs, after Tasks 1-4)

- [ ] #2215 fix kubectl MCP crash-loop (`.env` permission denied after 2.0.0); pair with #2088.
- [ ] #2210 guard gaps in `.claude/hooks/guard.py` (assignment forms, `*-agent` basenames); TDD against its test file.
- [ ] #2202 VolSync retirement carve-outs: owner-approved already per issue; the operator removal landed in #2222. Re-read the issue and check what remains.

## Self-review

Spec coverage: cleanup (Tasks 2, 4), merges (Tasks 1, 3), real issues (Task 5). Uncertain: I did not verify CI state on PRs showing an empty `CI Success`; Task 3 Step 2 covers it. I did not check whether an `agent_merge.py` merge helper exists (memory mentions one); if the owner wants agents to merge, Task 1 can use it.
