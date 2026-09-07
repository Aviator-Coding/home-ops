#!/usr/bin/env python3
"""Guard: jdx/mise-action install_args must not re-pin tools .mise.toml already declares.

Measured 2026-09-07: Renovate PR #1601 bumped aqua:prometheus/prometheus in
.mise.toml from 3.2.1 to 3.14.0. validate.yaml python-tests still passed
aqua:prometheus/prometheus@3.2.1 to mise-action. mise-action installed 3.2.1;
the shim then looked up 3.14.0 from .mise.toml and failed with
"No version is set for shim: promtool", red on every PR that runs python-tests.

The rest of this repo already avoids that class of defect: talos, terraform,
terraform-diff, and terraform-publish list tool names in install_args and let
.mise.toml supply the version. This test makes re-introducing a second pin fail
closed, and proves the checker by feeding it the measured defect rather than
trusting a reading of the live file.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
MISE_TOML = ROOT / ".mise.toml"
WF_DIR = ROOT / ".github" / "workflows"
VALIDATE_WF = WF_DIR / "validate.yaml"

# Exact python-tests install_args that broke main after PR #1601.
MEASURED_DEFECT_INSTALL_ARGS = (
    "python uv kubectl opentofu aqua:helm/helm "
    "aqua:kubernetes-sigs/kustomize aqua:prometheus/prometheus@3.2.1 "
    "aqua:mitsuhiko/minijinja"
)

PYTHON_TESTS_REQUIRED_TOOLS = (
    "python",
    "uv",
    "kubectl",
    "opentofu",
    "aqua:helm/helm",
    "aqua:kubernetes-sigs/kustomize",
    "aqua:prometheus/prometheus",
    "aqua:mitsuhiko/minijinja",
)


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_mise_tools(path: Path = MISE_TOML) -> dict[str, str]:
    require(path.is_file(), f"missing {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    tools = data.get("tools")
    require(isinstance(tools, dict) and tools, f"{path} has no [tools] table")
    return {str(k): str(v) for k, v in tools.items()}


def install_args_tokens(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return raw.split()
    if isinstance(raw, list):
        out: list[str] = []
        for item in raw:
            out.extend(str(item).split())
        return out
    raise Failure(f"install_args must be str or list, got {type(raw)}")


def pinned_mise_tools(
    tokens: list[str], mise_tools: set[str]
) -> list[tuple[str, str]]:
    """Return (tool, version) for any install_args token that re-pins a mise tool."""
    out: list[tuple[str, str]] = []
    for token in tokens:
        if "@" not in token:
            continue
        name, version = token.split("@", 1)
        if name in mise_tools:
            out.append((name, version))
    return out


def load_workflow(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    require(isinstance(data, dict), f"{path}: expected mapping, got {type(data)}")
    return data


def mise_action_steps(
    data: dict[str, Any],
) -> list[tuple[str, str, Any]]:
    """Return (job_id, step_name, install_args) for each jdx/mise-action step."""
    jobs = data.get("jobs") or {}
    require(isinstance(jobs, dict), "workflow jobs must be a mapping")
    found: list[tuple[str, str, Any]] = []
    for jid, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses = str(step.get("uses") or "")
            if "jdx/mise-action" not in uses:
                continue
            raw = (step.get("with") or {}).get("install_args")
            found.append((str(jid), str(step.get("name") or uses), raw))
    return found


def python_tests_install_args(data: dict[str, Any]) -> str:
    job = (data.get("jobs") or {}).get("python-tests")
    require(isinstance(job, dict), "validate.yaml missing python-tests job")
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if "jdx/mise-action" not in str(step.get("uses") or ""):
            continue
        raw = (step.get("with") or {}).get("install_args")
        require(isinstance(raw, str) and raw.strip(), "python-tests missing install_args")
        return raw
    raise Failure("python-tests missing jdx/mise-action step")


def scan_pins(
    data: dict[str, Any], mise_tools: set[str], label: str
) -> list[str]:
    failures: list[str] = []
    for jid, step_name, raw in mise_action_steps(data):
        pins = pinned_mise_tools(install_args_tokens(raw), mise_tools)
        for name, version in pins:
            failures.append(
                f"{label} job {jid!r} step {step_name!r} re-pins "
                f"{name}@{version} which .mise.toml already declares; "
                "drop the @version and let mise-action read .mise.toml"
            )
    return failures


def test_checker_refuses_measured_defect() -> dict[str, Any]:
    mise_tools = set(load_mise_tools())
    pins = pinned_mise_tools(
        install_args_tokens(MEASURED_DEFECT_INSTALL_ARGS), mise_tools
    )
    require(
        pins == [("aqua:prometheus/prometheus", "3.2.1")],
        f"checker must refuse the measured @3.2.1 pin; got {pins}",
    )
    return {"pins": pins}


def test_checker_accepts_unversioned_install_args() -> dict[str, Any]:
    mise_tools = set(load_mise_tools())
    good = MEASURED_DEFECT_INSTALL_ARGS.replace(
        "aqua:prometheus/prometheus@3.2.1", "aqua:prometheus/prometheus"
    )
    pins = pinned_mise_tools(install_args_tokens(good), mise_tools)
    require(pins == [], f"unversioned install_args must be clean; got {pins}")
    return {"tokens": install_args_tokens(good), "pins": pins}


def test_live_workflows_do_not_repin_mise_tools() -> dict[str, Any]:
    mise_tools = set(load_mise_tools())
    workflows = sorted(WF_DIR.glob("*.yml")) + sorted(WF_DIR.glob("*.yaml"))
    require(workflows, f"no workflows under {WF_DIR}")
    scanned: list[str] = []
    failures: list[str] = []
    for path in workflows:
        data = load_workflow(path)
        scanned.append(path.name)
        failures.extend(scan_pins(data, mise_tools, path.name))
    require(not failures, "; ".join(failures))
    return {"workflows": scanned, "mise_tools": sorted(mise_tools)}


def test_python_tests_still_installs_prometheus_from_mise() -> dict[str, Any]:
    data = load_workflow(VALIDATE_WF)
    raw = python_tests_install_args(data)
    tokens = install_args_tokens(raw)
    missing = [t for t in PYTHON_TESTS_REQUIRED_TOOLS if t not in tokens]
    require(
        not missing,
        f"python-tests install_args dropped required tools {missing}; got {tokens}",
    )
    require(
        not any(t.startswith("aqua:prometheus/prometheus@") for t in tokens),
        f"python-tests still pins prometheus in install_args: {raw}",
    )
    tools = load_mise_tools()
    require(
        "aqua:prometheus/prometheus" in tools,
        ".mise.toml must still declare aqua:prometheus/prometheus",
    )
    return {
        "install_args": raw,
        "mise_prometheus": tools["aqua:prometheus/prometheus"],
    }


def test_guard_refuses_a_restored_prometheus_pin() -> dict[str, Any]:
    """Prove the live-workflow scanner fails closed on the measured defect.

    A guard verified only by reading the now-clean file is the same class of
    defect this repo keeps hitting. Mutate a parsed copy of validate.yaml back
    to @3.2.1 and require scan_pins to refuse it.
    """
    mise_tools = set(load_mise_tools())
    data = load_workflow(VALIDATE_WF)
    live = python_tests_install_args(data)
    require(
        "aqua:prometheus/prometheus@" not in live,
        f"live python-tests already re-pinned prometheus: {live}",
    )
    mutated = False
    for step in data["jobs"]["python-tests"]["steps"]:
        if not isinstance(step, dict):
            continue
        if "jdx/mise-action" not in str(step.get("uses") or ""):
            continue
        original = step["with"]["install_args"]
        step["with"]["install_args"] = original.replace(
            "aqua:prometheus/prometheus",
            "aqua:prometheus/prometheus@3.2.1",
        )
        mutated = True
    require(mutated, "failed to restore the measured pin on a copy of python-tests")
    failures = scan_pins(data, mise_tools, "mutated validate.yaml")
    require(
        failures,
        "scan_pins accepted a restored aqua:prometheus/prometheus@3.2.1 pin",
    )
    require(
        any("prometheus" in f and "3.2.1" in f for f in failures),
        f"scan_pins failed for the wrong reason: {failures}",
    )
    # The live file must still be clean after mutating the in-memory copy.
    live_failures = scan_pins(load_workflow(VALIDATE_WF), mise_tools, "validate.yaml")
    require(not live_failures, f"mutating the copy dirtied the live file: {live_failures}")
    return {"refused": failures}


def test_unversioned_install_follows_a_mise_toml_bump() -> dict[str, Any]:
    """A .mise.toml version bump must be what `mise current` reports.

    Combined with unversioned install_args, that is how python-tests follows
    the next prometheus bump automatically instead of shipping a second pin.
    """
    tools = load_mise_tools()
    declared = tools.get("aqua:prometheus/prometheus")
    require(declared, ".mise.toml missing aqua:prometheus/prometheus")

    data = load_workflow(VALIDATE_WF)
    tokens = install_args_tokens(python_tests_install_args(data))
    require(
        "aqua:prometheus/prometheus" in tokens,
        "python-tests must list unversioned aqua:prometheus/prometheus",
    )

    mise = shutil.which("mise")
    require(mise is not None, "mise is required to prove a toml bump is followed")

    evidence: dict[str, Any] = {"declared": declared, "install_args_tokens": tokens}
    with tempfile.TemporaryDirectory(prefix="mise-install-args-") as tmp:
        work = Path(tmp)
        (work / ".mise.toml").write_text(
            '[tools]\n"aqua:prometheus/prometheus" = "3.2.1"\n',
            encoding="utf-8",
        )
        first = subprocess.run(
            [mise, "--cd", str(work), "current", "aqua:prometheus/prometheus"],
            check=False,
            capture_output=True,
            text=True,
        )
        require(
            first.returncode == 0 and first.stdout.strip() == "3.2.1",
            f"mise current did not follow 3.2.1 pin: rc={first.returncode} "
            f"stdout={first.stdout!r} stderr={first.stderr!r}",
        )
        (work / ".mise.toml").write_text(
            f'[tools]\n"aqua:prometheus/prometheus" = "{declared}"\n',
            encoding="utf-8",
        )
        second = subprocess.run(
            [mise, "--cd", str(work), "current", "aqua:prometheus/prometheus"],
            check=False,
            capture_output=True,
            text=True,
        )
        require(
            second.returncode == 0 and second.stdout.strip() == declared,
            f"mise current did not follow bump to {declared}: rc={second.returncode} "
            f"stdout={second.stdout!r} stderr={second.stderr!r}",
        )
        evidence["mise_current_before"] = first.stdout.strip()
        evidence["mise_current_after"] = second.stdout.strip()
    return evidence


def main() -> int:
    tests = [
        test_checker_refuses_measured_defect,
        test_checker_accepts_unversioned_install_args,
        test_live_workflows_do_not_repin_mise_tools,
        test_python_tests_still_installs_prometheus_from_mise,
        test_guard_refuses_a_restored_prometheus_pin,
        test_unversioned_install_follows_a_mise_toml_bump,
    ]
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            evidence = test()
        except Failure as exc:
            failed += 1
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {name}: unexpected {type(exc).__name__}: {exc}")
        else:
            print(f"[PASS] {name} {evidence}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
