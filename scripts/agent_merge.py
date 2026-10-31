#!/usr/bin/env python3
"""The only way an agent may merge a PR (owner decision 2026-10-31, goal §3 and §5).

Agents may merge risk:low and risk:normal PRs, and only when every check below holds.
risk:high, Renovate minor/major bumps and agent-steering files stay with the owner. Direct
`gh pr merge` stays denied to agents (.claude/settings.json, .claude/hooks/guard.py).

    scripts/agent_merge.py <pr> [--dry-run]

Checks, all re-read from GitHub and main (nothing the PR or the agent supplies is trusted):
  - open, not a draft, base main, mergeable, no `hold` label, no "DO NOT MERGE" title;
  - a `risk:low` or `risk:normal` label applied by the risk-tier bot (github-actions[bot]);
  - main's scripts/ci/risk_tier.py rates every changed path below risk:high, and none is an
    agent-steering file (AGENTS.md, CLAUDE.md, .claude/**, skill scripts, the AI review rules);
  - a Renovate PR (author mortyops[bot]) is a digest or patch, never minor or major;
  - `CI Success` is SUCCESS and `AI PR Review` is SUCCESS (SKIPPED allowed for Renovate) on the
    head commit;
  - main's scripts/ci/ai_review_gate.py (the Jev blocker gate) exits 0 on the PR diff.
It then comments the evidence on the PR and runs `gh pr merge --squash --match-head-commit`.

Env: JEV_API_KEY (else `op read op://Homelab/litellm-consumer-jev-decisions/key`),
JEV_BASE_URL (default https://litellm.sklab.dev). Exit 0 merged (or would merge with
--dry-run), 1 refused, 2 error. Tested by scripts/ci/agent-merge-test.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TIERS = {"risk:low", "risk:normal"}
RISK_BOT = "github-actions[bot]"
RENOVATE = "mortyops[bot]"
OP_REF = "op://Homelab/litellm-consumer-jev-decisions/key"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


risk_tier = _load("risk_tier", ROOT / "scripts" / "ci" / "risk_tier.py")


def decide(pr: dict) -> tuple[bool, list[str]]:
    """(merge?, reasons it may not). Pure: tested offline."""
    reasons: list[str] = []
    labels = set(pr.get("labels") or [])
    if pr.get("state") != "OPEN":
        reasons.append("not open")
    if pr.get("isDraft"):
        reasons.append("draft")
    if pr.get("baseRefName") != "main":
        reasons.append("base is not main")
    if pr.get("mergeable") != "MERGEABLE":
        reasons.append(f"mergeable={pr.get('mergeable')}")
    if "hold" in labels or "DO NOT MERGE" in (pr.get("title") or "").upper():
        reasons.append("hold label or DO NOT MERGE title")
    tiers = {lbl for lbl in labels if lbl.startswith("risk:")}
    if not tiers & ALLOWED_TIERS or tiers - ALLOWED_TIERS:
        reasons.append(f"risk label {sorted(tiers) or 'missing'} is not risk:low/risk:normal")
    if pr.get("risk_labeler") != RISK_BOT:
        reasons.append(f"risk label set by {pr.get('risk_labeler')!r}, not the risk-tier bot")
    files = pr.get("files") or []
    if not files:
        reasons.append("no changed files listed")
    high = [f for f in files if risk_tier.path_tier(f) == "risk:high"]
    if high:
        reasons.append(f"risk:high paths: {high[:5]}")
    steering = [f for f in files if any(r.match(f) for r in risk_tier._STEERING)]
    if steering:
        reasons.append(f"agent-steering files need the owner: {steering[:5]}")
    if pr.get("author") == RENOVATE and labels & {"type/minor", "type/major"}:
        reasons.append("Renovate minor/major needs the owner")
    checks = pr.get("checks") or {}
    if checks.get("CI Success") != "SUCCESS":
        reasons.append(f"CI Success is {checks.get('CI Success', 'missing')}")
    review_ok = {"SUCCESS"} | ({"SKIPPED"} if pr.get("author") == RENOVATE else set())
    if checks.get("AI PR Review") not in review_ok:
        reasons.append(f"AI PR Review is {checks.get('AI PR Review', 'missing')}")
    if pr.get("jev_rc") != 0:
        reasons.append({1: "Jev gate found a blocker", 2: "Jev gate could not decide"}.get(
            pr.get("jev_rc"), f"Jev gate exit {pr.get('jev_rc')}"))
    return not reasons, reasons


def sh(*args: str, check: bool = True) -> str:
    return subprocess.run(args, check=check, capture_output=True, text=True, cwd=ROOT).stdout


def gather(number: int) -> dict:
    """Everything decide() needs, read from GitHub and main."""
    fields = "state,isDraft,baseRefName,mergeable,title,author,labels,headRefOid,files"
    for _ in range(6):  # GitHub computes mergeability lazily; the first read is often UNKNOWN
        data = json.loads(sh("gh", "pr", "view", str(number), "--json", fields))
        if data.get("mergeable") != "UNKNOWN":
            break
        time.sleep(5)
    repo = sh("gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
    head = data["headRefOid"]
    events = json.loads(sh("gh", "api", "--paginate", f"repos/{repo}/issues/{number}/events"))
    risk_events = [e for e in events if e.get("event") == "labeled"
                   and (e.get("label") or {}).get("name", "").startswith("risk:")]
    runs = json.loads(sh("gh", "api", "--paginate", f"repos/{repo}/commits/{head}/check-runs",
                         "--jq", "[.check_runs[] | {name, status, conclusion}]"))
    checks = {}
    for run in runs:
        state = (run.get("conclusion") or run.get("status") or "").upper()
        checks.setdefault(run["name"], state)
    return {
        "number": number, "repo": repo, "head": head,
        "state": data["state"], "isDraft": data["isDraft"], "baseRefName": data["baseRefName"],
        "mergeable": data["mergeable"], "title": data["title"],
        "author": (data.get("author") or {}).get("login", ""),
        "labels": [lbl["name"] for lbl in data.get("labels") or []],
        "risk_labeler": (risk_events[-1].get("actor") or {}).get("login") if risk_events else None,
        "files": [f["path"] for f in data.get("files") or []],
        "checks": checks,
    }


def jev_gate(number: int, head: str) -> int:
    """Run main's ai_review_gate.py on the PR diff with the jev-decisions key."""
    key = os.environ.get("JEV_API_KEY", "").strip()
    if not key:
        out = subprocess.run(["op", "read", OP_REF], capture_output=True, text=True, timeout=60)
        key = out.stdout.strip()
        if not key:
            return 2
    sh("git", "fetch", "-q", "origin", "main", f"pull/{number}/head")
    base = sh("git", "merge-base", "origin/main", head).strip()
    gate = sh("git", "show", "origin/main:scripts/ci/ai_review_gate.py")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tmp:
        tmp.write(gate)
    env = dict(os.environ, LITELLM_KEY=key,
               LITELLM_URL=os.environ.get("JEV_BASE_URL", "https://litellm.sklab.dev"))
    # python.org's macOS build ships without a CA bundle; fall back to the system one.
    if "SSL_CERT_FILE" not in env and Path("/etc/ssl/cert.pem").is_file():
        env["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"
    res = subprocess.run([sys.executable, tmp.name, "--base", base, "--head", head],
                         capture_output=True, text=True, cwd=ROOT, env=env)
    os.unlink(tmp.name)
    print(res.stdout.strip())
    return res.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pr", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        pr = gather(args.pr)
        pr["jev_rc"] = jev_gate(args.pr, pr["head"])
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as exc:
        print(f"agent-merge: could not gather PR #{args.pr}: {exc}")
        return 2
    ok, reasons = decide(pr)
    print(json.dumps({"pr": args.pr, "merge": ok, "reasons": reasons, "head": pr["head"]}, indent=1))
    if not ok or args.dry_run:
        return 0 if ok else 1
    body = (f"Agent merge (scripts/agent_merge.py, owner decision 2026-10-31): all checks passed on "
            f"`{pr['head'][:8]}`: {sorted(set(pr['labels']) & ALLOWED_TIERS)}, CI Success and AI PR Review "
            f"green, Jev blocker gate clear, no risk:high or steering paths.")
    sh("gh", "pr", "comment", str(args.pr), "--body", body)
    sh("gh", "pr", "merge", str(args.pr), "--squash", "--match-head-commit", pr["head"])
    print(f"merged #{args.pr}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
