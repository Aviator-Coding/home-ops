#!/usr/bin/env python3
# ci: gate
"""Keep the PR gate on scripts/ci small and honest.

Prevents: the required python-tests job silently dropping the data-loss and
outage tests, or growing back into a suite that is red on every PR (59 of 60
Validate PR runs before 2026-10-31 failed on brittle tests, not real catches).

Only files carrying the exact line `# ci: gate` right after the shebang block
PRs. Every other test runs in the advisory job, which never blocks. Asserts:

  1. The gated files are exactly EXPECTED_GATE (owner-approved 2026-10-31;
     changing it is an owner decision), at most MAX_GATE, and each docstring
     has a `Prevents:` line naming the data loss or outage it guards.
  2. validate.yaml's `python-tests` job runs only gate files, fails when it
     finds none, has no continue-on-error, and is in `success` needs.
  3. `python-tests-advisory` runs only files with neither marker, has
     `continue-on-error: true`, and is NOT in `success` needs.
  4. `# ci: nightly` files (network-calling, goal D2) run only in
     nightly-tests.yaml, on a schedule; no file carries both markers.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / "scripts" / "ci"
VALIDATE = ROOT / ".github" / "workflows" / "validate.yaml"
MARKER = "# ci: gate"
NIGHTLY = "# ci: nightly"
NIGHTLY_WF = ROOT / ".github" / "workflows" / "nightly-tests.yaml"
# Measured 2026-10-31 (Opus code read plus a no-network sandbox run): these call out.
EXPECTED_NIGHTLY = {
    "grafana-mcp-deploy-test.py",
    "litellm-pr-reviewer-test.py",
    "litellm-sso-test.py",
    "samba-shared-xml-test.py",
    "schematic-pcie-port-pm-test.py",
    "terraform-ci-workflows-test.py",
    "tofu-authentik-stack-test.py",
}
MAX_GATE = 15
EXPECTED_GATE = {
    "alert-coverage-gaps-test.py",
    "backup-silent-failure-alerting-test.py",
    "ci-test-gate-test.py",
    "ci-success-workflow-test.py",
    "corrupt-claim-recreation-contract-test.py",
    "flux-annotation-values-test.py",
    "hostpolicy-ceph-selector-test.py",
    "kopiur-stage0-test.py",
    "kopiur-stage1-test.py",
    "kopiur-stage3-test.py",
    "kopiur-stage5-test.py",
    "pvc-mover-readable-check-test.py",
    "pvc-writable-check-test.py",
    "risk-tier-test.py",
    "volsync-retired-expiry-test.py",
}

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


def gate_files() -> list[Path]:
    out = []
    for path in sorted(CI.glob("*-test.py")):
        raw = path.read_bytes()
        second_line = raw.split(b"\n", 2)[1] if raw.count(b"\n") >= 2 else b""
        if second_line.endswith(b"\r"):
            failures.append(f"{path.name}: CRLF line 2 is invisible to `grep -x`")
            print(f"[FAIL] {path.name}: CRLF line 2 is invisible to `grep -x`")
        lines = raw.decode().splitlines()
        if len(lines) > 1 and lines[0].startswith("#!") and lines[1] == MARKER:
            out.append(path)
        elif MARKER in lines:
            failures.append(f"{path.name}: `{MARKER}` must be line 2, right after the shebang")
            print(f"[FAIL] {path.name}: `{MARKER}` must be line 2, right after the shebang")
    return out


def run_script(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


def main() -> int:
    gates = gate_files()
    names = {p.name for p in gates}
    check(len(gates) <= MAX_GATE, f"{len(gates)} gate files (<= {MAX_GATE})")
    check(names == EXPECTED_GATE,
          f"gate set matches the approved list (missing {sorted(EXPECTED_GATE - names)}, "
          f"unexpected {sorted(names - EXPECTED_GATE)})")
    for path in gates:
        doc = ast.get_docstring(ast.parse(path.read_text())) or ""
        check(
            any(line.startswith("Prevents:") for line in doc.splitlines()),
            f"{path.name} docstring has a `Prevents:` line",
        )

    jobs = yaml.safe_load(VALIDATE.read_text())["jobs"]
    gate_job = jobs.get("python-tests") or {}
    advisory = jobs.get("python-tests-advisory") or {}
    success_needs = (jobs.get("success") or {}).get("needs") or []

    gate_run = run_script(gate_job)
    check(f"grep -lx '{MARKER}'" in gate_run, "python-tests selects files by the gate marker")
    check("${#gates[@]} == 0" in gate_run, "python-tests fails when it selects no gate files")
    check("continue-on-error" not in gate_job, "python-tests has no continue-on-error")
    check("python-tests" in success_needs, "python-tests is in success.needs")

    adv_run = run_script(advisory)
    check("grep -LxE '# ci: (gate|nightly)'" in adv_run,
          "python-tests-advisory selects files with neither marker")
    check(advisory.get("continue-on-error") is True, "python-tests-advisory has continue-on-error: true")
    check("python-tests-advisory" not in success_needs, "python-tests-advisory is not in success.needs")

    nightly = sorted(p.name for p in CI.glob("*-test.py") if p.read_text().split("\n")[1:2] == [NIGHTLY])
    check(set(nightly) == EXPECTED_NIGHTLY,
          f"nightly set matches (missing {sorted(EXPECTED_NIGHTLY - set(nightly))}, "
          f"unexpected {sorted(set(nightly) - EXPECTED_NIGHTLY)})")
    check(not set(nightly) & names, "no file is both gate and nightly")
    wf = yaml.safe_load(NIGHTLY_WF.read_text()) if NIGHTLY_WF.is_file() else {}
    on = wf.get(True) or wf.get("on") or {}
    check("schedule" in on and "pull_request" not in on,
          "nightly-tests.yaml runs on a schedule, never on pull_request")
    nightly_run = "\n".join(run_script(j) for j in (wf.get("jobs") or {}).values())
    check(f"grep -lx '{NIGHTLY}'" in nightly_run, "nightly-tests.yaml runs the nightly files")
    check("gh issue" in nightly_run, "nightly-tests.yaml reports failures in an issue")

    print(f"Summary: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
