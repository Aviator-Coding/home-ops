#!/usr/bin/env python3
# ci: gate
"""Contract for .github/workflows: the invariants the merge gate depends on.

Prevents: the required `CI Success` check going green while a job failed, was
cancelled or never ran (fork PR), or going missing while the cluster and its
ARC runners are down, which would block the fix for a broken cluster.

Asserts:
  1. ci.yaml `success` is named `CI Success`, runs with always(), needs every
     other ci.yaml job, fails a fork PR and fails on failure or cancellation.
  2. Every other ci.yaml job needs `changes` (the only fork guard) and carries
     no always()/!cancelled() that would run it past a skipped `changes`.
  3. Every ci.yaml job runs on ubuntu-latest and none is a reusable-workflow call.
  4. Every PR-triggered workflow sets concurrency and top-level
     `permissions: {contents: read}`. image-pull.yaml keeps
     cancel-in-progress false and only its pull job uses ARC.
  5. Every jdx/mise-action step names tools without `@version` (Renovate PR
     #1601: a re-pin made mise install a copy the shim never reads) and sets
     `cache: false`.
  6. tests.yaml never runs on pull_request, runs on a schedule, runs every test
     that is not a gate file or one of the three docs guards ci.yaml's lint
     job runs, and reports failures in an issue. No file still carries the
     retired `# ci: nightly` marker.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"
FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"
ARC = "gha-runner-scale-set-skrumbach-home-ops"
DOCS_GUARDS = ("docs-budget-test.py", "doc-links-test.py", "functional-comments-guard-test.py")

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


def load(name: str) -> dict:
    path = WF / name
    return yaml.safe_load(path.read_text()) if path.is_file() else {}


def on_block(data: dict) -> dict:
    # PyYAML reads a bare `on:` key as boolean True.
    on = data.get(True) or data.get("on") or {}
    return on if isinstance(on, dict) else {}


def needs_of(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def run_text(job: dict) -> str:
    return "\n".join(str(s.get("run", "")) for s in job.get("steps") or [])


def test_ci_success(ci: dict) -> None:
    jobs = ci.get("jobs") or {}
    success = jobs.get("success") or {}
    check(success.get("name") == "CI Success", "ci.yaml success job is named `CI Success`")
    check(str(success.get("if", "")).replace(" ", "") == "${{always()}}", "CI Success runs with always()")
    others = set(jobs) - {"success"}
    check(set(needs_of(success)) == others,
          f"CI Success needs every other job (missing {sorted(others - set(needs_of(success)))})")
    steps = success.get("steps") or []
    fork = [s for s in steps if "!=" in str(s.get("if", "")) and "head.repo.full_name" in str(s.get("if", ""))]
    check(bool(fork) and "exit 1" in str(fork[0].get("run", "")), "CI Success fails a fork PR")
    bad = [s for s in steps if "contains(needs.*.result, 'failure')" in str(s.get("if", ""))
           and "contains(needs.*.result, 'cancelled')" in str(s.get("if", ""))
           and not str(s.get("if", "")).lstrip("${ ").startswith("!")]
    check(bool(bad) and "exit 1" in str(bad[0].get("run", "")), "CI Success fails on failure or cancellation")


def test_ci_jobs(ci: dict) -> None:
    jobs = ci.get("jobs") or {}
    check(FORK_GUARD in str((jobs.get("changes") or {}).get("if", "")), "ci.yaml changes job carries the fork guard")
    for jid, job in jobs.items():
        check("uses" not in job, f"ci.yaml {jid} is not a reusable-workflow call")
        check(job.get("runs-on") == "ubuntu-latest", f"ci.yaml {jid} runs on ubuntu-latest")
        if jid in ("changes", "success"):
            continue
        cond = str(job.get("if", ""))
        check("changes" in needs_of(job), f"ci.yaml {jid} needs changes")
        check("always()" not in cond and "cancelled()" not in cond,
              f"ci.yaml {jid} cannot run past a skipped changes job")


def test_pr_workflows() -> None:
    for path in sorted(WF.glob("*.y*ml")):
        data = load(path.name)
        if "pull_request" not in on_block(data):
            continue
        check(bool(data.get("concurrency")), f"{path.name} declares concurrency")
        check(data.get("permissions") == {"contents": "read"}, f"{path.name} top-level permissions are contents: read")
    image = load("image-pull.yaml")
    check((image.get("concurrency") or {}).get("cancel-in-progress") is False,
          "image-pull.yaml keeps cancel-in-progress false")
    for jid, job in (image.get("jobs") or {}).items():
        want = ARC if jid == "pull" else "ubuntu-latest"
        check(job.get("runs-on") == want, f"image-pull.yaml {jid} runs on {want}")


def test_mise_action() -> None:
    seen = 0
    for path in sorted(WF.glob("*.y*ml")):
        for jid, job in (load(path.name).get("jobs") or {}).items():
            for step in (job or {}).get("steps") or []:
                if "jdx/mise-action" not in str(step.get("uses", "")):
                    continue
                seen += 1
                args = str((step.get("with") or {}).get("install_args", "")).split()
                pinned = [a for a in args if "@" in a]
                check(not pinned, f"{path.name} {jid} install_args has no @version {pinned}")
                check((step.get("with") or {}).get("cache") is False, f"{path.name} {jid} mise-action sets cache: false")
    check(seen > 0, "found jdx/mise-action steps")


def test_tests_workflow(ci: dict) -> None:
    data = load("tests.yaml")
    on = on_block(data)
    check(bool(data), "tests.yaml exists")
    check("pull_request" not in on and "schedule" in on, "tests.yaml runs on a schedule, never on pull_request")
    run = "\n".join(run_text(j) for j in (data.get("jobs") or {}).values())
    check("grep -Lx '# ci: gate'" in run, "tests.yaml selects every non-gate file")
    exclude = "grep -vE '/(" + "|".join(n.removesuffix("-test.py") for n in DOCS_GUARDS) + r")-test\.py$'"
    check(exclude in run, "tests.yaml excludes exactly the three docs guards")
    check("${#files[@]} == 0" in run, "tests.yaml fails on an empty selection")
    check("gh issue" in run, "tests.yaml reports failures in an issue")
    lint = run_text((ci.get("jobs") or {}).get("lint") or {})
    for name in DOCS_GUARDS:
        check(f"scripts/ci/{name}" in lint and (ROOT / "scripts" / "ci" / name).is_file(),
              f"ci.yaml lint runs {name}")
    stale = [p.name for p in (ROOT / "scripts" / "ci").glob("*-test.py")
             if "# ci: nightly" in p.read_text().splitlines()[:3]]
    check(not stale, f"no file carries the retired `# ci: nightly` marker {stale}")


def main() -> int:
    ci = load("ci.yaml")
    check(bool(ci), "ci.yaml exists")
    test_ci_success(ci)
    test_ci_jobs(ci)
    test_pr_workflows()
    test_mise_action()
    test_tests_workflow(ci)
    print(f"Summary: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
