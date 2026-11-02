#!/usr/bin/env python3
"""Offline test of the agent merge decision (scripts/agent_merge.py decide()).

Prevents: an agent merging a PR the owner must merge (risk:high, Renovate minor/major,
agent-steering or merge-gate files, a spoofed or red check, a Jev blocker) - owner decision
2026-10-31 lets agents merge only risk:low and risk:normal PRs through this wrapper.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("agent_merge", ROOT / "scripts" / "agent_merge.py")
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)

failures: list[str] = []
GREEN = [{"app": 15368, "state": "SUCCESS"}]
REN = "app/mortyops"


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


def base(**over):
    pr = {
        "state": "OPEN", "isDraft": False, "isCrossRepository": False, "baseRefName": "main",
        "mergeable": "MERGEABLE", "author": "Aviator-Coding", "labels": ["risk:normal"],
        "risk_labeler": "github-actions[bot]", "type_labelers": [],
        "files": ["kubernetes/apps/base/media/plex/app/helmrelease.yaml"], "files_complete": True,
        "checks": {"CI Success": GREEN, "AI PR Review": GREEN}, "jev_rc": 0,
    }
    pr.update(over)
    return pr


def renovate(update: str, tier: str, **over):
    return base(**{"author": REN, "labels": [tier, f"type/{update}"], "type_labelers": ["mortyops[bot]"], **over})


def ok(pr) -> bool:
    return am.decide(pr)[0]


check(ok(base()), "a green risk:normal PR with a clear Jev gate merges")
check(ok(base(labels=["risk:low"], files=["docs/goals/knowledge-ledger.md"])), "a green risk:low PR merges")
check(not ok(base(labels=["risk:high"])), "risk:high never merges")
check(not ok(base(files=["talos/machineconfig.yaml.j2"])), "a high path never merges, whatever the label says")
check(not ok(base(labels=["risk:low"])), "a label below the computed tier is refused")
check(not ok(base(labels=["risk:low", "risk:normal"])), "two risk labels are refused")
check(not ok(base(labels=[])), "no risk label, no merge")
check(not ok(base(risk_labeler="Aviator-Coding")), "a risk label not set by the risk-tier bot is not trusted")
check(not ok(base(files=["AGENTS.md"])), "agent-steering files need the owner")
check(not ok(base(files=[".agents/skills/jev-decisions/scripts/jev_decide.py"])), "skill scripts need the owner")
for gate in ("scripts/agent_merge.py", "scripts/ci/risk_tier.py", "scripts/ci/ai_review_gate.py",
             "scripts/ci/agent-merge-test.py"):
    check(not ok(base(files=[gate])), f"the merge gate cannot merge a change to {gate}")
check(not ok(base(labels=["risk:low"], files=[".agents/skills/implement-task/SKILL.md"])),
      "the skill stating the merge policy needs the owner")
check(ok(base(labels=["risk:low"], files=[".agents/skills/media-stack/SKILL.md"])), "other skill docs stay low")
check(not ok(base(files_complete=False)), "an incomplete file list is refused")
check(not ok(base(files=["kubernetes/apps/base/media/plex/app/x.yaml", "talos/old.yaml.j2"])),
      "a rename out of a high path (old path listed) is refused")
check(not ok(base(isCrossRepository=True)), "fork PRs are refused")
check(not ok(renovate("minor", "risk:normal")), "Renovate minor needs the owner")
check(not ok(renovate("major", "risk:normal")), "Renovate major needs the owner")
check(not ok(base(author=REN, labels=["risk:normal"])), "an untyped Renovate PR needs the owner")
check(not ok(renovate("patch", "risk:low", type_labelers=["Aviator-Coding"])),
      "a type label not set by Renovate is not trusted")
check(ok(renovate("patch", "risk:low", checks={"CI Success": GREEN,
                                              "AI PR Review": [{"app": 15368, "state": "SKIPPED"}]})),
      "a Renovate patch may merge with the AI review skipped")
check(ok(renovate("digest", "risk:low")), "a Renovate digest merges")
check(not ok(base(checks={"CI Success": [{"app": 15368, "state": "FAILURE"}], "AI PR Review": GREEN})),
      "red CI Success blocks")
check(not ok(base(checks={"AI PR Review": GREEN})), "missing CI Success blocks")
check(not ok(base(checks={"CI Success": GREEN + [{"app": 99999, "state": "SUCCESS"}], "AI PR Review": GREEN})),
      "a CI Success check run from another app blocks")
check(not ok(base(checks={"CI Success": GREEN + [{"app": 15368, "state": "FAILURE"}], "AI PR Review": GREEN})),
      "any red run of a duplicated check name blocks")
check(not ok(base(checks={"CI Success": GREEN, "AI PR Review": [{"app": 15368, "state": "SKIPPED"}]})),
      "a skipped AI review blocks a non-Renovate PR")
check(not ok(base(checks={"CI Success": GREEN, "AI PR Review": [{"app": 15368, "state": "IN_PROGRESS"}]})),
      "a running AI review blocks")
check(not ok(base(jev_rc=1)), "a Jev blocker blocks")
check(not ok(base(jev_rc=2)), "Jev could not decide: no merge")
check(not ok(base(isDraft=True)), "drafts never merge")
check(not ok(base(mergeable="CONFLICTING")), "conflicts never merge")
check(not ok(base(baseRefName="release")), "only PRs into main")
check(not ok(base(title="DO NOT MERGE: x")), "a DO NOT MERGE title never merges")
check(not ok(base(labels=["risk:normal", "hold"])), "the hold label blocks")
check(len(am.decide(base(labels=["risk:high"], jev_rc=1))[1]) >= 2, "every failed condition is reported")

print(f"Summary: {len(failures)} failed")
sys.exit(1 if failures else 0)
