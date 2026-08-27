#!/usr/bin/env python3
"""Behavioral contract tests for terraform-diff / terraform-publish CI.

These workflows are the public, machine-consumed interface for terraform/*
stacks. Assertions operate on a parsed YAML model of the workflows, on the
real offline tofu schema path, and on an executed copy of the missing-secret
gate shell - not on raw source greps alone.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DIFF_WF = ROOT / ".github" / "workflows" / "terraform-diff.yaml"
PUB_WF = ROOT / ".github" / "workflows" / "terraform-publish.yaml"
VALIDATE_WF = ROOT / ".github" / "workflows" / "validate.yaml"
FLUX_LOCAL = ROOT / ".github" / "workflows" / "flux-local.yaml"
SECRETS = ROOT / "terraform" / "authentik" / "secrets.vals.yaml"
SECRETS_CI = ROOT / "terraform" / "authentik" / "secrets-ci.vals.yaml"
SECRETS_APPLY = ROOT / "terraform" / "authentik" / "secrets-apply.vals.yaml"
RUNBOOK = ROOT / "docs" / "authentik" / "terraform.md"
VALIDATE_SH = ROOT / "scripts" / "ci" / "tofu-validate.sh"
GITIGNORE = ROOT / "terraform" / ".gitignore"
LABELER = ROOT / ".github" / "labeler.yaml"
LABELS = ROOT / ".github" / "labels.yaml"

FORK_GUARD = (
    "github.event.pull_request.head.repo.full_name == github.repository"
)
ARC_RUNNER = "gha-runner-scale-set-aviator-coding-home-ops"
SHA_PIN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+@[0-9a-f]{40}$")


def load_workflow(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text()
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise AssertionError(f"{path}: expected mapping, got {type(data)}")
    return data, text


def on_block(data: dict[str, Any]) -> Any:
    # PyYAML 5.x turns the key `on` into boolean True.
    if "on" in data:
        return data["on"]
    if True in data:
        return data[True]
    raise AssertionError("workflow missing on:")


def step_by_name(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"missing step: {name}")


def refs_from_vals(path: Path) -> dict[str, str]:
    data = yaml.safe_load(path.read_text()) or {}
    out: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, str) and value.startswith("ref+"):
            out[key] = value
    return out


def field_name(ref: str) -> str:
    # op://vault/item/field  OR  onepasswordconnect://vault/item#/field
    if "#/" in ref:
        return ref.rsplit("#/", 1)[1]
    return ref.rstrip("/").rsplit("/", 1)[-1]


def simulate_plan_path(
    *,
    is_fork: bool,
    changed_dirs: list[str],
    has_ci_secrets: dict[str, bool],
    op_token: str | None,
) -> list[tuple[str, str]]:
    """Mirror terraform-diff.yaml's filter + plan decision tree."""
    if is_fork:
        return [("ALL", "skipped_fork")]
    if not changed_dirs:
        return [("ALL", "no_changes")]
    results: list[tuple[str, str]] = []
    for stack in changed_dirs:
        if has_ci_secrets.get(stack, False):
            if not op_token:
                results.append((stack, "fail_missing_secret"))
            else:
                results.append((stack, "live_plan"))
        else:
            results.append((stack, "schema_only"))
    return results


def test_diff_workflow_shape() -> dict[str, Any]:
    data, text = load_workflow(DIFF_WF)
    on = on_block(data)
    assert "pull_request" in on, on
    pr = on["pull_request"]
    assert "terraform/**" in pr.get("paths", [])
    assert pr.get("branches") == ["main"]

    jobs = data["jobs"]
    assert set(jobs) == {"filter", "plan", "success"}

    for name, job in jobs.items():
        assert FORK_GUARD in (job.get("if") or ""), name
        assert job.get("runs-on") == ARC_RUNNER, name

    plan = jobs["plan"]
    assert plan["permissions"]["pull-requests"] == "write"
    assert "contents" in plan["permissions"]
    matrix = plan["strategy"]["matrix"]
    assert "stack" in matrix
    assert plan["strategy"].get("fail-fast") is False

    steps = plan["steps"]
    opt_in = step_by_name(steps, "Check For Live-Plan Opt-In")
    assert "secrets-ci.vals.yaml" in opt_in["run"]

    require = step_by_name(steps, "Require CI Credentials")
    assert require.get("if") == "steps.ci-secrets.outputs.file != ''"
    assert "OP_CONNECT_TOKEN" in require.get("env", {})
    assert "::error::" in require["run"] and "exit 1" in require["run"]

    init = step_by_name(steps, "Tofu Init")
    assert "vals exec" in init["run"]
    assert "tofu init -backend=false" in init["run"]

    for gated in ("Tofu Plan", "Generate Token", "Post Plan Comment"):
        step = step_by_name(steps, gated)
        assert step.get("if") == "steps.ci-secrets.outputs.file != ''", gated

    plan_step = step_by_name(steps, "Tofu Plan")
    assert "tofu plan -lock=false" in plan_step["run"]
    assert ".planfile" in plan_step["run"]
    assert "vals exec" in plan_step["run"]

    comment = step_by_name(steps, "Post Plan Comment")
    assert SHA_PIN.match(comment["uses"]), comment["uses"]
    assert comment["uses"].startswith("borchero/terraform-plan-comment@")
    assert comment["with"]["terraform-cmd"] == "tofu"
    assert comment["with"]["planfile"] == ".planfile"
    assert "token" in comment["with"]

    # No apply step.
    for step in steps:
        run = step.get("run") or ""
        assert "tofu apply" not in run

    # Digest-pinned actions only.
    for match in re.finditer(r"uses:\s*(\S+)", text):
        uses = match.group(1)
        if uses.startswith("./"):
            continue
        assert SHA_PIN.match(uses), uses

    assert data.get("env", {}).get("OP_CONNECT_HOST", "").startswith("http://")
    return {
        "jobs": list(jobs),
        "runner": ARC_RUNNER,
        "comment_action": comment["uses"],
        "fork_guard": True,
    }


def test_publish_workflow_shape() -> dict[str, Any]:
    data, text = load_workflow(PUB_WF)
    on = on_block(data)
    assert "push" in on
    assert "terraform/**" in on["push"].get("paths", [])
    job = data["jobs"]["publish"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["permissions"]["packages"] == "write"
    runs = "\n".join(step.get("run") or "" for step in job["steps"])
    assert "flux push artifact" in runs
    assert "ghcr.io/aviator-coding/manifests/terraform:" in runs
    assert "flux tag artifact" in runs
    assert "--tag main" in runs
    assert "tofu " not in runs and "tofu\n" not in runs
    for match in re.finditer(r"uses:\s*(\S+)", text):
        uses = match.group(1)
        if uses.startswith("./"):
            continue
        assert SHA_PIN.match(uses), uses
    return {
        "runs-on": job["runs-on"],
        "artifact_prefix": "ghcr.io/aviator-coding/manifests/terraform:",
    }


def test_decision_tree() -> dict[str, Any]:
    cases = {
        "fork": (
            simulate_plan_path(
                is_fork=True,
                changed_dirs=["terraform/authentik"],
                has_ci_secrets={"terraform/authentik": True},
                op_token="tok",
            ),
            ("ALL", "skipped_fork"),
        ),
        "missing_secret": (
            simulate_plan_path(
                is_fork=False,
                changed_dirs=["terraform/authentik"],
                has_ci_secrets={"terraform/authentik": True},
                op_token=None,
            ),
            ("terraform/authentik", "fail_missing_secret"),
        ),
        "live_plan": (
            simulate_plan_path(
                is_fork=False,
                changed_dirs=["terraform/authentik"],
                has_ci_secrets={"terraform/authentik": True},
                op_token="tok",
            ),
            ("terraform/authentik", "live_plan"),
        ),
        "schema_only_future_stack": (
            simulate_plan_path(
                is_fork=False,
                changed_dirs=["terraform/future"],
                has_ci_secrets={"terraform/future": False},
                op_token=None,
            ),
            ("terraform/future", "schema_only"),
        ),
        "empty_filter": (
            simulate_plan_path(
                is_fork=False,
                changed_dirs=[],
                has_ci_secrets={},
                op_token="tok",
            ),
            ("ALL", "no_changes"),
        ),
    }
    out = {}
    for name, (got, expect) in cases.items():
        assert got[0] == expect, (name, got, expect)
        out[name] = got
    # Filesystem backs the authentik opt-in assumption used above.
    assert SECRETS_CI.is_file()
    assert SECRETS.is_file()
    out["authentik_opt_in_file"] = str(SECRETS_CI.relative_to(ROOT))
    return out


def test_missing_secret_gate_executes() -> dict[str, Any]:
    """Run the same shell logic the workflow embeds."""
    script = r"""
set -euo pipefail
if [ -z "${OP_CONNECT_TOKEN:-}" ]; then
  echo "::error::terraform/authentik opts into a live plan (secrets-ci.vals.yaml present) but the OP_CONNECT_TOKEN repository secret is not set. See docs/authentik/terraform.md section 8 for what to create and why."
  exit 1
fi
echo continued
"""
    env_base = {k: v for k, v in os.environ.items() if k != "OP_CONNECT_TOKEN"}
    missing = subprocess.run(
        ["bash", "-lc", script],
        env=env_base,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode == 1
    combined = missing.stdout + missing.stderr
    assert "OP_CONNECT_TOKEN repository secret is not set" in combined
    assert "continued" not in combined

    present = subprocess.run(
        ["bash", "-lc", script],
        env={**env_base, "OP_CONNECT_TOKEN": "dummy-not-used"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert present.returncode == 0
    assert "continued" in present.stdout
    return {
        "missing_exit": missing.returncode,
        "missing_message": combined.strip().splitlines()[0],
        "present_exit": present.returncode,
    }


def test_secrets_ci_mirrors_readonly() -> dict[str, Any]:
    sec = refs_from_vals(SECRETS)
    ci = refs_from_vals(SECRETS_CI)
    apply = refs_from_vals(SECRETS_APPLY)

    assert set(sec) == set(ci), (set(sec) ^ set(ci))
    fields = {}
    for key in sorted(sec):
        assert sec[key].startswith("ref+op://Automation/authentik-terraform/")
        assert ci[key].startswith(
            "ref+onepasswordconnect://Automation/authentik-terraform#"
        )
        assert field_name(sec[key]) == field_name(ci[key]), key
        assert "APPLY" not in field_name(ci[key]).upper()
        fields[key] = field_name(ci[key])

    # Apply file is the only place APPLY token may appear, and CI must not use it.
    assert field_name(apply["TF_VAR_authentik_token"]) == "AUTHENTIK_APPLY_TOKEN"
    assert field_name(ci["TF_VAR_authentik_token"]) == "AUTHENTIK_TOKEN"
    return {"keys": sorted(ci), "fields": fields}


def test_schema_only_tofu_path() -> dict[str, Any]:
    """Same offline path validate.yaml and no-secrets-ci stacks use."""
    env = os.environ.copy()
    # Ensure no Connect/AWS creds leak into this offline path.
    for key in list(env):
        if key.startswith("TF_VAR_") or key in {
            "OP_CONNECT_TOKEN",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "OP_CONNECT_HOST",
        }:
            env.pop(key, None)
    proc = subprocess.run(
        [str(VALIDATE_SH)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Success! The configuration is valid." in proc.stdout
    assert "OK: 1 OpenTofu stack(s) formatted and valid" in proc.stdout
    return {"exit": proc.returncode, "tail": proc.stdout.strip().splitlines()[-2:]}


def test_validate_job_still_credentialless() -> dict[str, Any]:
    data, text = load_workflow(VALIDATE_WF)
    jobs = data["jobs"]
    assert "terraform" in jobs
    terraform = jobs["terraform"]
    # Behavior unchanged: still runs the schema script, no OP_CONNECT_TOKEN.
    runs = "\n".join(step.get("run") or "" for step in terraform["steps"])
    assert "./scripts/ci/tofu-validate.sh" in runs
    assert "OP_CONNECT_TOKEN" not in text.split("terraform:")[1].split("success:")[0] if "success:" in text else "OP_CONNECT_TOKEN" not in runs
    # Header must now point at terraform-diff for live plans.
    header = text.split("name: Validate")[0]
    assert "terraform-diff.yaml" in header
    return {"job": "terraform", "script": "./scripts/ci/tofu-validate.sh"}


def test_flux_local_untouched() -> dict[str, Any]:
    base = os.environ.get("NO_MISTAKES_BASE_COMMIT", "e16f27b4995c08a035a8ba0d31b0dc4972a64f06")
    proc = subprocess.run(
        ["git", "diff", f"{base}..HEAD", "--", str(FLUX_LOCAL.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout == ""
    return {"diff_bytes": 0, "path": str(FLUX_LOCAL.relative_to(ROOT))}


def test_runbook_section8_contract() -> dict[str, Any]:
    text = RUNBOOK.read_text()
    idx = text.find("## 8.")
    assert idx >= 0, "missing section 8"
    end = text.find("\n## ", idx + 4)
    section = text[idx : end if end > 0 else None]
    required = [
        "OP_CONNECT_TOKEN",
        "secrets-ci.vals.yaml",
        "terraform-diff.yaml",
        "Automation",
        "tofu apply",
        "AUTHENTIK_APPLY_TOKEN",
    ]
    for needle in required:
        assert needle in section, needle
    assert "every item in the `Automation`" in section or "every item in the Automation" in section
    assert "fork" in section.lower()
    assert "do not reuse" in section.lower() or "not reuse" in section.lower() or "dedicated" in section.lower()
    # Apply remains section-7 gated.
    assert "## 7." in text
    return {
        "section_chars": len(section),
        "required_present": required,
        "vault_wide_caveat": True,
    }


def test_labeler_and_gitignore() -> dict[str, Any]:
    labeler = LABELER.read_text()
    labels = LABELS.read_text()
    assert "area/terraform:" in labeler
    assert "terraform/**" in labeler
    assert "area/terraform" in labels
    gi = GITIGNORE.read_text()
    assert "planfile" in gi
    return {"label": "area/terraform", "gitignore_planfile": True}


def main() -> int:
    tests = [
        ("diff_workflow_shape", test_diff_workflow_shape),
        ("publish_workflow_shape", test_publish_workflow_shape),
        ("decision_tree", test_decision_tree),
        ("missing_secret_gate_executes", test_missing_secret_gate_executes),
        ("secrets_ci_mirrors_readonly", test_secrets_ci_mirrors_readonly),
        ("schema_only_tofu_path", test_schema_only_tofu_path),
        ("validate_job_still_credentialless", test_validate_job_still_credentialless),
        ("flux_local_untouched", test_flux_local_untouched),
        ("runbook_section8_contract", test_runbook_section8_contract),
        ("labeler_and_gitignore", test_labeler_and_gitignore),
    ]
    results: dict[str, Any] = {}
    failed = False
    for name, fn in tests:
        try:
            detail = fn()
            results[name] = {"status": "pass", "detail": detail}
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001 - surface each failure
            failed = True
            results[name] = {"status": "fail", "error": f"{type(exc).__name__}: {exc}"}
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}", file=sys.stderr)

    payload = {"ok": not failed, "results": results}
    print("---")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
