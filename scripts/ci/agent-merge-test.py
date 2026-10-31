#!/usr/bin/env python3
"""Offline test of the agent merge decision (scripts/agent_merge.py decide()).

Prevents: an agent merging a PR the owner must merge (risk:high, Renovate minor/major,
agent-steering files, a red or missing check, a Jev blocker) - owner decision 2026-10-31
lets agents merge only risk:low and risk:normal PRs through this wrapper.
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


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


def base(**over):
    pr = {
        "state": "OPEN", "isDraft": False, "baseRefName": "main", "mergeable": "MERGEABLE",
        "author": "Aviator-Coding", "labels": ["risk:normal", "type/patch"],
        "risk_labeler": "github-actions[bot]", "files": ["kubernetes/apps/base/media/plex/app/helmrelease.yaml"],
        "checks": {"CI Success": "SUCCESS", "AI PR Review": "SUCCESS"}, "jev_rc": 0,
    }
    pr.update(over)
    return pr


def ok(pr) -> bool:
    return am.decide(pr)[0]


check(ok(base()), "a green risk:normal PR with a clear Jev gate merges")
check(ok(base(labels=["risk:low"], files=["docs/goals/knowledge-ledger.md"])), "a green risk:low PR merges")
check(not ok(base(labels=["risk:high"])), "risk:high never merges")
check(not ok(base(files=["talos/machineconfig.yaml.j2"])), "a high path never merges, whatever the label says")
check(not ok(base(labels=[])), "no risk label, no merge")
check(not ok(base(risk_labeler="Aviator-Coding")), "a risk label not set by the risk-tier bot is not trusted")
check(not ok(base(files=["AGENTS.md"])), "agent-steering files need the owner")
check(not ok(base(files=[".agents/skills/jev-decisions/scripts/jev_decide.py"])), "skill scripts need the owner")
check(not ok(base(author="mortyops[bot]", labels=["risk:normal", "type/minor"])), "Renovate minor needs the owner")
check(not ok(base(author="mortyops[bot]", labels=["risk:normal", "type/major"])), "Renovate major needs the owner")
check(ok(base(author="mortyops[bot]", labels=["risk:low", "type/patch"],
              checks={"CI Success": "SUCCESS", "AI PR Review": "SKIPPED"})),
      "a Renovate patch may merge with the AI review skipped")
check(not ok(base(checks={"CI Success": "FAILURE", "AI PR Review": "SUCCESS"})), "red CI Success blocks")
check(not ok(base(checks={"AI PR Review": "SUCCESS"})), "missing CI Success blocks")
check(not ok(base(checks={"CI Success": "SUCCESS", "AI PR Review": "FAILURE"})), "red AI review blocks")
check(not ok(base(checks={"CI Success": "SUCCESS", "AI PR Review": "PENDING"})), "pending AI review blocks")
check(not ok(base(jev_rc=1)), "a Jev blocker blocks")
check(not ok(base(jev_rc=2)), "Jev could not decide: no merge")
check(not ok(base(isDraft=True)), "drafts never merge")
check(not ok(base(mergeable="CONFLICTING")), "conflicts never merge")
check(not ok(base(baseRefName="release")), "only PRs into main")
check(not ok(base(title="DO NOT MERGE: x")), "a DO NOT MERGE title never merges")
check(not ok(base(labels=["risk:normal", "hold"])), "the hold label blocks")
reasons = am.decide(base(labels=["risk:high"], jev_rc=1))[1]
check(len(reasons) >= 2, "every failed condition is reported, not just the first")

print(f"Summary: {len(failures)} failed")
sys.exit(1 if failures else 0)
