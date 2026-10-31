#!/usr/bin/env python3
"""Merge a risk:low or risk:normal PR for an agent (owner decision 2026-10-31, goal §3 and §5).

risk:high, Renovate minor/major bumps, agent-steering files and the merge gate's own files
stay with the owner. Direct `gh pr merge` stays denied to agents (.claude/settings.json,
.claude/hooks/guard.py). This is a policy check, not a security boundary: it runs on the
agent's machine with the owner's token. The boundary is task 2.6 (bot identity) plus the ruleset.

    scripts/agent_merge.py <pr> [--dry-run]

Checks, all re-read from GitHub and origin/main (nothing the PR or the agent supplies is trusted):
  - open, not a draft, not from a fork, base main, mergeable, no `hold` label, no "DO NOT MERGE";
  - a `risk:low` or `risk:normal` label applied by the risk-tier bot, equal to the tier that
    origin/main's scripts/ci/risk_tier.py computes over every changed path (renames include the
    old path; the list must be complete);
  - no path is risk:high or agent-steering;
  - a Renovate PR carries `type/digest` or `type/patch`, set by Renovate itself;
  - every `CI Success` and `AI PR Review` check run on the head commit is from GitHub Actions
    (app 15368) and SUCCESS (the review may be SKIPPED for Renovate);
  - origin/main's scripts/ci/ai_review_gate.py (the Jev blocker gate) exits 0, run in a clean
    worktree of origin/main.
Everything except the Jev gate is read again just before `gh pr merge --squash --match-head-commit`.

Env: JEV_API_KEY (else `op read op://Homelab/litellm-consumer-jev-decisions/key`). Exit 0
merged (or would merge with --dry-run), 1 refused, 2 error. Tested by scripts/ci/agent-merge-test.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import shutil
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_TIERS = {"risk:low", "risk:normal"}
RISK_BOT = "github-actions[bot]"
RENOVATE_AUTHOR = "app/mortyops"  # `gh pr view --json author` login
RENOVATE_ACTOR = "mortyops[bot]"  # issue-event actor login
ACTIONS_APP_ID = 15368
JEV_URL = "https://litellm.sklab.dev"  # fixed: an env override could point at a stub
OP_REF = "op://Homelab/litellm-consumer-jev-decisions/key"


def _load_local() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("risk_tier", ROOT / "scripts" / "ci" / "risk_tier.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The offline test uses the working tree's classifier; main() swaps in origin/main's.
risk_tier = _load_local()


def decide(pr: dict) -> tuple[bool, list[str]]:
    """(merge?, reasons it may not). Pure: tested offline."""
    reasons: list[str] = []
    labels = set(pr.get("labels") or [])
    renovate = pr.get("author") == RENOVATE_AUTHOR
    if pr.get("state") != "OPEN":
        reasons.append("not open")
    if pr.get("isDraft"):
        reasons.append("draft")
    if pr.get("isCrossRepository"):
        reasons.append("fork PR")
    if pr.get("baseRefName") != "main":
        reasons.append("base is not main")
    if pr.get("mergeable") != "MERGEABLE":
        reasons.append(f"mergeable={pr.get('mergeable')}")
    if "hold" in labels or "DO NOT MERGE" in (pr.get("title") or "").upper():
        reasons.append("hold label or DO NOT MERGE title")

    files = pr.get("files") or []
    if not files or pr.get("files_complete") is not True:
        reasons.append("changed-file list missing or incomplete")
    high = [f for f in files if risk_tier.path_tier(f) == "risk:high"]
    if high:
        reasons.append(f"risk:high paths: {high[:5]}")
    steering = [f for f in files if any(r.match(f) for r in risk_tier._STEERING)]
    if steering:
        reasons.append(f"agent-steering files need the owner: {steering[:5]}")

    update = None
    if renovate:
        types_ = {lbl for lbl in labels if lbl.startswith("type/")}
        if types_ & {"type/minor", "type/major"} or not types_ & {"type/digest", "type/patch"}:
            reasons.append(f"Renovate {sorted(types_) or 'untyped'} needs the owner (digest or patch only)")
        elif pr.get("type_labelers") != [RENOVATE_ACTOR]:
            reasons.append(f"type label set by {pr.get('type_labelers')}, not Renovate")
        else:
            update = "digest" if "type/digest" in types_ else "patch"

    tiers = {lbl for lbl in labels if lbl.startswith("risk:")}
    if len(tiers) != 1 or not tiers <= ALLOWED_TIERS:
        reasons.append(f"risk label {sorted(tiers) or 'missing'} is not exactly one of risk:low/risk:normal")
    if pr.get("risk_labeler") != RISK_BOT:
        reasons.append(f"risk label set by {pr.get('risk_labeler')!r}, not the risk-tier bot")
    if files and tiers and risk_tier.tier(files, update) not in tiers:
        reasons.append(f"label {sorted(tiers)} differs from the computed {risk_tier.tier(files, update)}")

    checks = pr.get("checks") or {}
    for name, allowed in (("CI Success", {"SUCCESS"}),
                          ("AI PR Review", {"SUCCESS", "SKIPPED"} if renovate else {"SUCCESS"})):
        runs = checks.get(name) or []
        if not runs:
            reasons.append(f"{name} missing")
        elif any(r.get("app") != ACTIONS_APP_ID for r in runs):
            reasons.append(f"{name} has a check run from another app")
        elif any(r.get("state") not in allowed for r in runs):
            reasons.append(f"{name} is {sorted({r.get('state') for r in runs})}")

    if pr.get("jev_rc") != 0:
        reasons.append({1: "Jev gate found a blocker", 2: "Jev gate could not decide"}.get(
            pr.get("jev_rc"), f"Jev gate exit {pr.get('jev_rc')}"))
    return not reasons, reasons


def sh(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, cwd=cwd).stdout


def gh_pages(path: str) -> list:
    """Every page of a list endpoint (--slurp gives one JSON array of pages)."""
    return json.loads(sh("gh", "api", "--paginate", "--slurp", path))


def gather(number: int, repo: str) -> dict:
    """Everything decide() needs except the Jev result, read from GitHub."""
    fields = "state,isDraft,isCrossRepository,baseRefName,mergeable,title,author,labels,headRefOid,changedFiles"
    for _ in range(6):  # GitHub computes mergeability lazily; the first read is often UNKNOWN
        data = json.loads(sh("gh", "pr", "view", str(number), "--json", fields))
        if data.get("mergeable") != "UNKNOWN":
            break
        time.sleep(5)
    head = data["headRefOid"]

    files: list[str] = []
    listed = 0
    for page in gh_pages(f"repos/{repo}/pulls/{number}/files?per_page=100"):
        for f in page:
            listed += 1
            files.append(f["filename"])
            if f.get("previous_filename"):
                files.append(f["previous_filename"])

    events = [e for page in gh_pages(f"repos/{repo}/issues/{number}/events?per_page=100") for e in page]

    def last_actor(prefix: str):
        hits = [e for e in events if e.get("event") == "labeled"
                and (e.get("label") or {}).get("name", "").startswith(prefix)]
        return (hits[-1].get("actor") or {}).get("login") if hits else None

    type_labelers = sorted({(e.get("actor") or {}).get("login") for e in events
                            if e.get("event") == "labeled"
                            and (e.get("label") or {}).get("name", "").startswith("type/")})

    checks: dict[str, list[dict]] = {}
    for page in gh_pages(f"repos/{repo}/commits/{head}/check-runs?per_page=100"):
        for run in page.get("check_runs") or []:
            state = (run.get("conclusion") or run.get("status") or "").upper()
            checks.setdefault(run["name"], []).append({"app": (run.get("app") or {}).get("id"), "state": state})

    return {
        "number": number, "head": head,
        "state": data["state"], "isDraft": data["isDraft"], "isCrossRepository": data["isCrossRepository"],
        "baseRefName": data["baseRefName"], "mergeable": data["mergeable"], "title": data["title"],
        "author": (data.get("author") or {}).get("login", ""),
        "labels": [lbl["name"] for lbl in data.get("labels") or []],
        "risk_labeler": last_actor("risk:"), "type_labelers": type_labelers,
        "files": files, "files_complete": listed == data["changedFiles"] and listed < 3000,
        "checks": checks,
    }


def load_main_classifier() -> None:
    """Replace the working tree's risk_tier with origin/main's copy."""
    global risk_tier
    mod = types.ModuleType("risk_tier")
    exec(compile(sh("git", "show", "origin/main:scripts/ci/risk_tier.py"), "origin/main:risk_tier.py", "exec"),
         mod.__dict__)
    risk_tier = mod


def jev_gate(number: int, head: str) -> int:
    """Run origin/main's ai_review_gate.py on the PR in a clean worktree of origin/main."""
    key = os.environ.get("JEV_API_KEY", "").strip()
    if not key:
        try:
            key = subprocess.run(["op", "read", OP_REF], capture_output=True, text=True, timeout=60).stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            key = ""
        if not key:
            print("Jev gate: no key (JEV_API_KEY or op signin)")
            return 2
    base = sh("git", "merge-base", "origin/main", head).strip()
    tmp = Path(tempfile.mkdtemp(prefix="agent-merge-"))
    tree = tmp / "main"
    sh("git", "worktree", "add", "-q", "--detach", str(tree), "origin/main")
    try:
        env = dict(os.environ, LITELLM_KEY=key, LITELLM_URL=JEV_URL)
        # python.org's macOS build ships without a CA bundle; fall back to the system one.
        if "SSL_CERT_FILE" not in env and Path("/etc/ssl/cert.pem").is_file():
            env["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"
        res = subprocess.run([sys.executable, "scripts/ci/ai_review_gate.py", "--base", base, "--head", head],
                             capture_output=True, text=True, cwd=tree, env=env)
        print(res.stdout.strip())
        return res.returncode
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=ROOT, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pr", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        repo = sh("gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
        sh("git", "fetch", "-q", "origin", "main", f"pull/{args.pr}/head")
        load_main_classifier()
        pr = gather(args.pr, repo)
        pr["jev_rc"] = jev_gate(args.pr, pr["head"])
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as exc:
        print(f"agent-merge: could not gather PR #{args.pr}: {exc}")
        return 2
    ok, reasons = decide(pr)
    print(json.dumps({"pr": args.pr, "merge": ok, "reasons": reasons, "head": pr["head"]}, indent=1))
    if not ok or args.dry_run:
        return 0 if ok else 1

    # The Jev call takes time: re-read everything else, so a hold label, draft or push in between counts.
    try:
        again = gather(args.pr, repo)
    except (subprocess.CalledProcessError, json.JSONDecodeError, KeyError) as exc:
        print(f"agent-merge: re-check failed: {exc}")
        return 2
    again["jev_rc"] = 0 if again["head"] == pr["head"] else 2
    ok, reasons = decide(again)
    if not ok:
        print(json.dumps({"pr": args.pr, "merge": False, "reasons": ["changed during the check"] + reasons}))
        return 1
    try:
        sh("gh", "pr", "merge", str(args.pr), "--squash", "--match-head-commit", pr["head"])
    except subprocess.CalledProcessError as exc:
        print(f"agent-merge: gh pr merge failed: {exc.stderr.strip()}")
        return 2
    tier = sorted(set(again["labels"]) & ALLOWED_TIERS)
    print(f"merged #{args.pr}")
    try:
        sh("gh", "pr", "comment", str(args.pr), "--body",
           f"Merged by an agent through `scripts/agent_merge.py` (owner decision 2026-10-31) at "
           f"`{pr['head'][:8]}`: {tier}, CI Success and AI PR Review green from GitHub Actions, Jev blocker "
           f"gate clear, no risk:high, steering or merge-gate paths.")
    except subprocess.CalledProcessError as exc:
        print(f"agent-merge: merged, but the evidence comment failed: {exc.stderr.strip()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
