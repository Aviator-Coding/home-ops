#!/usr/bin/env python3
"""Unit test for scripts/ci/goal_metrics.py, the daily goal-metrics recorder.

It feeds goal D2 (CI Success pass rate on PRs over 14 days) and D3 (open
Renovate PRs per day), so the 14-day windows measure themselves.

Checks the pure functions; the gh calls run only in .github/workflows/goal-metrics.yaml.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("goal_metrics", HERE / "goal_metrics.py")
if spec is None or spec.loader is None:
    print("FAIL: scripts/ci/goal_metrics.py not found")
    sys.exit(1)
gm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gm)

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


check(gm.pass_rate(["success", "success", "failure", "cancelled", "skipped"]) == 2 / 3,
      "pass_rate ignores cancelled and skipped")
check(gm.pass_rate([]) == 1.0, "pass_rate of no runs is 1.0")
check(gm.pass_rate(["failure"]) == 0.0, "pass_rate of one failure is 0.0")
check(gm.render_row("2026-11-20", 0.9567, 230, 3) == "| 2026-11-20 | 95.7% | 230 | 3 |",
      "render_row formats one Markdown row")
body = gm.append_row(None, "| 2026-11-20 | 95.7% | 230 | 3 |")
check(gm.HEADER in body and body.rstrip().endswith("| 2026-11-20 | 95.7% | 230 | 3 |"),
      "append_row creates the table")
body2 = gm.append_row(body, "| 2026-11-20 | 96.0% | 231 | 2 |")
check(body2.count("| 2026-11-20 |") == 1 and "96.0%" in body2, "append_row replaces the same day's row")
body3 = gm.append_row(body2, "| 2026-11-21 | 97.0% | 240 | 1 |")
check(body3.count("\n| 2026-") == 2, "append_row adds a new day")

print(f"Summary: {len(failures)} failed")
sys.exit(1 if failures else 0)
