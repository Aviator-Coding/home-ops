#!/usr/bin/env python3
"""Record the agent-maintainable goal's daily metrics in one GitHub issue.

D2: CI workflow pass rate on pull requests over the last 14 days (cancelled
and skipped runs ignored). D3: open Renovate PRs (author app/mortyops) today.
Run daily by .github/workflows/goal-metrics.yaml; tested by goal-metrics-test.py.
Stdlib only; needs the `gh` CLI with GH_TOKEN.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys

TITLE = "Goal metrics: agent-maintainable home-ops"
HEADER = (
    "| Date | CI Success pass rate (14d) | Runs | Open Renovate PRs |\n"
    "|---|---|---|---|\n"
)
INTRO = (
    "Written daily by `.github/workflows/goal-metrics.yaml` for "
    "`docs/goals/2026-10-30-agent-maintainable-home-ops.md` (D2: >= 95% for 14 days; "
    "D3: <= 5 open Renovate PRs every day).\n\n"
)


def pass_rate(conclusions: list[str]) -> float:
    """success / (success + failure); cancelled, skipped and others are ignored."""
    ok = conclusions.count("success")
    bad = conclusions.count("failure")
    return 1.0 if ok + bad == 0 else ok / (ok + bad)


def render_row(day: str, rate: float, runs: int, open_renovate: int) -> str:
    return f"| {day} | {rate:.1%} | {runs} | {open_renovate} |"


def append_row(body: str | None, row: str) -> str:
    """Add `row` to the issue body's table, replacing a row for the same day."""
    day = row.split("|")[1].strip()
    if not body or HEADER not in body:
        body = INTRO + HEADER
    lines = [line for line in body.rstrip("\n").split("\n") if not line.startswith(f"| {day} |")]
    return "\n".join(lines) + "\n" + row + "\n"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main() -> int:
    today = dt.date.today()
    since = (today - dt.timedelta(days=14)).isoformat()
    runs = json.loads(gh(
        "run", "list", "--workflow", "ci.yaml", "--event", "pull_request",
        "--created", f">={since}", "--json", "conclusion", "--limit", "1000",
    ))
    conclusions = [r.get("conclusion") or "" for r in runs]
    renovate = json.loads(gh("pr", "list", "--author", "app/mortyops", "--state", "open",
                             "--json", "number", "--limit", "200"))
    row = render_row(today.isoformat(), pass_rate(conclusions),
                     sum(c in {"success", "failure"} for c in conclusions), len(renovate))
    issues = json.loads(gh("issue", "list", "--state", "open", "--search", f'in:title "{TITLE}"',
                           "--json", "number,title,body", "--limit", "20"))
    issue = next((i for i in issues if i["title"] == TITLE), None)
    body = append_row(issue["body"] if issue else None, row)
    if issue:
        gh("issue", "edit", str(issue["number"]), "--body", body)
    else:
        gh("issue", "create", "--title", TITLE, "--body", body)
    print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
