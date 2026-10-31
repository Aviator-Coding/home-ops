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
import math
import os
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
    """One table row. The rate is floored, so 94.99% never displays as 95.0%."""
    shown = "n/a" if runs == 0 else f"{math.floor(rate * 1000) / 10:.1f}%"
    return f"| {day} | {shown} | {runs} | {open_renovate} |"


class TableMissing(Exception):
    """The issue body has text but no metrics table; refuse to overwrite it."""


def append_row(body: str | None, row: str) -> str:
    """Add `row` after the table's last row, replacing a row for the same day."""
    day = row.split("|")[1].strip()
    body = (body or "").replace("\r\n", "\n")
    if not body.strip():
        body = INTRO + HEADER
    elif HEADER not in body:
        raise TableMissing("issue body has no metrics table; fix it by hand, history is not overwritten")
    lines = [line for line in body.rstrip("\n").split("\n") if not line.startswith(f"| {day} |")]
    header_end = lines.index(HEADER.rstrip("\n").split("\n")[1])
    last = header_end
    for i in range(header_end + 1, len(lines)):
        if lines[i].startswith("| 20"):
            last = i
        elif lines[i].strip():
            break
    lines.insert(last + 1, row)
    return "\n".join(lines) + "\n"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main() -> int:
    today = dt.date.today()
    since = (today - dt.timedelta(days=13)).isoformat()  # 14 calendar days, today included
    runs = json.loads(gh(
        "run", "list", "--workflow", "ci.yaml", "--event", "pull_request",
        "--created", f">={since}", "--json", "conclusion", "--limit", "1000",
    ))
    conclusions = [r.get("conclusion") or "" for r in runs]
    renovate = json.loads(gh("pr", "list", "--author", "app/mortyops", "--state", "open",
                             "--json", "number", "--limit", "200"))
    row = render_row(today.isoformat(), pass_rate(conclusions),
                     sum(c in {"success", "failure"} for c in conclusions), len(renovate))
    # Only the workflow's own issue: the repo is public, so a same-titled issue by
    # anyone else must not be edited. Any state, so a closed issue is reopened, not duplicated.
    issues = json.loads(gh("issue", "list", "--state", "all", "--author", "app/github-actions",
                           "--search", f'in:title "{TITLE}"', "--json", "number,title,body,state",
                           "--limit", "20"))
    issue = next((i for i in issues if i["title"] == TITLE), None)
    try:
        body = append_row(issue["body"] if issue else None, row)
    except TableMissing as exc:
        print(f"FAIL: issue #{issue['number']}: {exc}", file=sys.stderr)
        return 1
    if issue:
        repo = os.environ.get("GH_REPO", "")
        gh("api", "--method", "PATCH", f"repos/{repo}/issues/{issue['number']}",
           "-f", f"body={body}", "-f", "state=open")
    else:
        gh("issue", "create", "--title", TITLE, "--body", body)
    print(row)
    return 0


if __name__ == "__main__":
    sys.exit(main())
