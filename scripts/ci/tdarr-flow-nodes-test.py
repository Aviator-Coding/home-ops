#!/usr/bin/env python3
"""CI bridge for the Tdarr safe-transcode behavioral harness.

docs/tdarr-errored-remuxes.md labels several recovery claims as "Re-checked by
CI" and points at docs/tdarr/flow-nodes/behavior-test.js. That harness already
executes the reviewable node sources and after-flow embedded code and asserts
observable outputs (guard_scope refusal, encoder-aware cargs, subconform track
preservation, after-flow inputsDB.code, forceConform false, live/CI provenance
labels). This script is the thin scripts/ci entrypoint that validate.yaml's
python-tests job actually runs: it invokes that Node harness as a subprocess
and fails closed if the harness is missing, Node is missing, or the harness
exits non-zero.

This does not re-implement the node proofs in Python, and it does not grep
source for tokens. The behavioral contract lives in behavior-test.js; CI's job
is to execute it on every scripts/ci or docs/tdarr change.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "docs" / "tdarr" / "flow-nodes" / "behavior-test.js"
PASS_MARKER = "ALL BEHAVIORAL CHECKS PASSED"

passed = 0
failed = 0


def require(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {msg}")
    else:
        failed += 1
        print(f"[FAIL] {msg}")


def resolve_node() -> str | None:
    """Prefer PATH node; fall back to common mise/homebrew locations."""
    found = shutil.which("node")
    if found:
        return found
    candidates = [
        Path(os.environ.get("HOME", "")) / ".local/share/mise/shims/node",
        Path("/opt/homebrew/bin/node"),
        Path("/usr/local/bin/node"),
    ]
    for path in candidates:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def main() -> int:
    require(HARNESS.is_file(), f"harness present: {HARNESS.relative_to(ROOT)}")

    node = resolve_node()
    require(node is not None, "node binary available on PATH (or known shim)")
    if node is None:
        print(
            "Node.js is required to execute docs/tdarr/flow-nodes/behavior-test.js.\n"
            "CI installs it via actions/setup-node on the python-tests job.",
            file=sys.stderr,
        )
        print(f"\n{failed} failed, {passed} passed")
        return 1

    print(f"Running: {node} {HARNESS.relative_to(ROOT)}")
    proc = subprocess.run(
        [node, str(HARNESS)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    # Stream harness output so CI logs keep the per-case evidence.
    if proc.stdout:
        sys.stdout.write(proc.stdout)
        if not proc.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        if not proc.stderr.endswith("\n"):
            sys.stderr.write("\n")

    require(proc.returncode == 0, f"behavior-test.js exit 0 (got {proc.returncode})")
    combined = (proc.stdout or "") + (proc.stderr or "")
    require(
        PASS_MARKER in combined,
        f"harness reported {PASS_MARKER!r}",
    )

    print(f"\n{failed} failed, {passed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
