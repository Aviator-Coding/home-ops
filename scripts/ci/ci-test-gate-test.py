#!/usr/bin/env python3
# ci: gate
"""Keep the PR gate on scripts/ci small and honest.

Prevents: the required python-tests job silently dropping the data-loss and
outage tests, or growing back into a suite that is red on every PR (59 of 60
Validate PR runs before 2026-10-31 failed on brittle tests, not real catches).

Only files carrying the exact line `# ci: gate` right after the shebang block
PRs. Every other test runs in the advisory job, which never blocks. Asserts:

  1. At most MAX_GATE files are gated, and each gate file's docstring has a
     `Prevents:` line naming the data loss or outage it guards.
  2. validate.yaml's `python-tests` job runs only gate files, and is in the
     `success` job's needs.
  3. `python-tests-advisory` runs only non-gate files, has
     `continue-on-error: true`, and is NOT in `success` needs.
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
MAX_GATE = 15

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


def gate_files() -> list[Path]:
    out = []
    for path in sorted(CI.glob("*-test.py")):
        lines = path.read_text().splitlines()
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
    check(0 < len(gates) <= MAX_GATE, f"{len(gates)} gate files (1..{MAX_GATE})")
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
    check("python-tests" in success_needs, "python-tests is in success.needs")

    adv_run = run_script(advisory)
    check(f"grep -Lx '{MARKER}'" in adv_run, "python-tests-advisory selects the non-gate files")
    check(advisory.get("continue-on-error") is True, "python-tests-advisory has continue-on-error: true")
    check("python-tests-advisory" not in success_needs, "python-tests-advisory is not in success.needs")

    print(f"Summary: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
