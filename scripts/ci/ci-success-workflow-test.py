#!/usr/bin/env python3
# ci: gate
"""Contract for the single required PR check, `CI Success` (.github/workflows/ci.yaml).

Prevents: the required gate going green while Flate or Validate failed, or
going missing when the cluster (and its ARC runners) is down, which would
block the owner from merging the fix for a broken cluster.

Asserts:

  1. ci.yaml calls flate.yaml and validate.yaml with `secrets: inherit`.
  2. Its `success` job is named `CI Success`, needs both, runs with always()
     (a skipped required check counts as passing), fails when any needed
     workflow failed or was cancelled (a skipped workflow passes), and fails a
     fork PR, whose jobs all skip.
  3. flate.yaml and validate.yaml are call-only (`workflow_call`) and carry no
     top-level `concurrency` (the caller owns it).
  4. Every job in ci, flate and validate runs on `ubuntu-latest`, never ARC.
  5. Every flate/validate job that reads a secret keeps the fork guard, directly
     or through a guarded `needs`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


def load(name: str) -> dict:
    path = WF / name
    if not path.is_file():
        check(False, f"{name} exists")
        return {}
    data = yaml.safe_load(path.read_text()) or {}
    if True in data:  # YAML 1.1 reads the bare key `on` as True
        data["on"] = data.pop(True)
    return data


def test_ci_calls_both(ci: dict) -> None:
    jobs = ci.get("jobs") or {}
    for name in ("flate", "validate"):
        job = jobs.get(name) or {}
        check(job.get("uses") == f"./.github/workflows/{name}.yaml", f"ci.yaml {name} calls {name}.yaml")
        check(job.get("secrets") == "inherit", f"ci.yaml {name} inherits secrets")


def test_success_job(ci: dict) -> None:
    job = (ci.get("jobs") or {}).get("success") or {}
    check(job.get("name") == "CI Success", "success job is named `CI Success`")
    check(sorted(job.get("needs") or []) == ["flate", "validate"], "CI Success needs flate and validate")
    check("always()" in str(job.get("if", "")) and "!cancelled()" not in str(job.get("if", "")),
          "CI Success runs with always() (a skipped required check counts as passing)")
    check(job.get("runs-on") == "ubuntu-latest", "CI Success runs on ubuntu-latest")
    step_ifs = [str(step.get("if", "")) for step in job.get("steps") or []]
    check(any("'failure'" in s and "'cancelled'" in s for s in step_ifs),
          "CI Success fails on a failed or cancelled workflow")
    check(any("head.repo.full_name != github.repository" in s for s in step_ifs),
          "CI Success fails a fork PR instead of passing with nothing run")


def test_called_workflows_are_call_only(flate: dict, validate: dict) -> None:
    for name, data in (("flate.yaml", flate), ("validate.yaml", validate)):
        on = data.get("on") or {}
        keys = set(on) if isinstance(on, dict) else {on}
        check(keys == {"workflow_call"}, f"{name} triggers only on workflow_call (got {sorted(keys)})")
        check("concurrency" not in data, f"{name} has no top-level concurrency")


def test_no_arc_in_gate(ci: dict, flate: dict, validate: dict) -> None:
    for name, data in (("ci.yaml", ci), ("flate.yaml", flate), ("validate.yaml", validate)):
        for jid, job in (data.get("jobs") or {}).items():
            if "uses" in job:
                continue
            check(job.get("runs-on") == "ubuntu-latest", f"{name} {jid} runs on ubuntu-latest")


def _guarded(jobs: dict, jid: str, seen: frozenset = frozenset()) -> bool:
    """A job is guarded by its own `if`, or by needing a guarded job (a skipped need
    skips it, unless its `if` uses always())."""
    job = jobs.get(jid) or {}
    cond = str(job.get("if", ""))
    if FORK_GUARD in cond:
        return True
    if "always()" in cond or jid in seen:
        return False
    needs = job.get("needs") or []
    needs = [needs] if isinstance(needs, str) else needs
    return any(_guarded(jobs, n, seen | {jid}) for n in needs)


def test_fork_guard_kept(flate: dict, validate: dict) -> None:
    for name, data in (("flate.yaml", flate), ("validate.yaml", validate)):
        jobs = data.get("jobs") or {}
        for jid, job in jobs.items():
            if "secrets." in yaml.safe_dump(job):
                check(_guarded(jobs, jid), f"{name} {jid} reads a secret behind the fork guard")


def main() -> int:
    ci, flate, validate = load("ci.yaml"), load("flate.yaml"), load("validate.yaml")
    if ci:
        test_ci_calls_both(ci)
        test_success_job(ci)
    test_called_workflows_are_call_only(flate, validate)
    test_no_arc_in_gate(ci, flate, validate)
    test_fork_guard_kept(flate, validate)
    print(f"Summary: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
