#!/usr/bin/env python3
"""Behavioral contract tests for homeops-norms-iac CI workflow hardening.

Parses GitHub Actions workflow YAML into a semantic model and asserts the
permission, concurrency, trigger, and fork-guard contracts required by
findings 2–5 plus the image-pull cancel-in-progress rationale. Assertions
operate on the parsed workflow graph (triggers, permissions inheritance,
token wiring, concurrency policy) — not on raw source greps alone.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
WF = ROOT / ".github" / "workflows"

LABELER = WF / "labeler.yaml"
TAG = WF / "tag.yaml"
BUILD = WF / "build-talosctl-busybox.yaml"
IMAGE_PULL = WF / "image-pull.yaml"
FLUX_LOCAL = WF / "flux-local.yaml"

FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"
PEER_CONCURRENCY_GROUP = (
    "${{ github.workflow }}-${{ github.event.number || github.ref }}"
)
APP_TOKEN_EXPR = re.compile(r"steps\.app-token\.outputs\.token")
GITHUB_TOKEN_EXPR = re.compile(r"secrets\.GITHUB_TOKEN|github\.token")


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


def steps_of(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps") or [])


def uses_action(step: dict[str, Any], prefix: str) -> bool:
    uses = step.get("uses") or ""
    return uses.startswith(prefix)


def collect_token_bindings(job: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in steps_of(job):
        with_ = step.get("with") or {}
        binding = {
            "name": step.get("name") or step.get("uses"),
            "uses": step.get("uses"),
            "repo-token": with_.get("repo-token"),
            "github-token": with_.get("github-token"),
            "token": with_.get("token"),
            "password": with_.get("password"),
            "username": with_.get("username"),
        }
        if any(
            binding[k]
            for k in ("repo-token", "github-token", "token", "password")
        ):
            out.append(binding)
    return out


def github_script_api_calls(job: dict[str, Any]) -> list[str]:
    """Extract REST method names invoked via actions/github-script bodies."""
    calls: list[str] = []
    pattern = re.compile(
        r"github\.rest\.(?:repos|git)\.(\w+)\s*\("
    )
    for step in steps_of(job):
        uses = step.get("uses") or ""
        if "actions/github-script@" not in uses:
            continue
        script = (step.get("with") or {}).get("script") or ""
        calls.extend(pattern.findall(script))
    return calls


def concurrency_comment_for(path: Path, text: str) -> str | None:
    """Return the comment immediately above cancel-in-progress, if any.

    Comments are not in the YAML data model; the consumer-visible contract for
    image-pull is the documented rationale next to the concurrency key.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"\s*cancel-in-progress\s*:", line):
            # walk upward for contiguous comment lines inside concurrency block
            comments: list[str] = []
            j = i - 1
            while j >= 0 and lines[j].lstrip().startswith("#"):
                comments.append(lines[j].lstrip("# ").strip())
                j -= 1
            if comments:
                return " ".join(reversed(comments))
            return None
    return None


class Failures(list[str]):
    def check(self, cond: bool, msg: str) -> None:
        if not cond:
            self.append(msg)


def test_labeler(f: Failures) -> dict[str, Any]:
    data, text = load_workflow(LABELER)
    on = on_block(data)
    evidence: dict[str, Any] = {"workflow": "labeler.yaml"}

    f.check(data.get("name") == "Labeler", "labeler workflow name must be 'Labeler'")
    evidence["name"] = data.get("name")

    f.check(isinstance(on, dict), "labeler on: must be a mapping")
    f.check(
        "workflow_dispatch" not in on,
        "labeler must not declare dead workflow_dispatch trigger",
    )
    f.check("pull_request" in on, "labeler must keep pull_request trigger")
    evidence["on_keys"] = sorted(on.keys()) if isinstance(on, dict) else on

    conc = data.get("concurrency") or {}
    evidence["concurrency"] = conc
    f.check(isinstance(conc, dict) and conc, "labeler must declare concurrency")
    f.check(
        conc.get("group") == PEER_CONCURRENCY_GROUP,
        f"labeler concurrency.group must match peer pattern, got {conc.get('group')!r}",
    )
    f.check(
        conc.get("cancel-in-progress") is True,
        "labeler cancel-in-progress must be true",
    )

    top_perm = data.get("permissions")
    evidence["top_permissions"] = top_perm
    f.check(
        top_perm == {"contents": "read"},
        f"labeler top-level permissions must be contents:read only, got {top_perm}",
    )

    jobs = data.get("jobs") or {}
    f.check("main" in jobs, "labeler job id must remain 'main'")
    job = jobs.get("main") or {}
    evidence["job_name"] = job.get("name")
    f.check(
        job.get("name") == "Labeler - Labeler",
        "required status check identity is job name 'Labeler - Labeler' "
        f"(got {job.get('name')!r})",
    )
    f.check(
        "permissions" not in job,
        "labeler job-level permissions must be removed (inherit contents:read)",
    )
    f.check(
        FORK_GUARD in str(job.get("if") or ""),
        f"labeler fork-PR guard must be unchanged, got {job.get('if')!r}",
    )
    evidence["if"] = job.get("if")

    # No checkout; App token mint; labeler uses repo-token = app token
    steps = steps_of(job)
    f.check(
        not any(uses_action(s, "actions/checkout@") for s in steps),
        "labeler must not checkout (no GITHUB_TOKEN contents need beyond floor)",
    )
    f.check(
        any(uses_action(s, "actions/create-github-app-token@") for s in steps),
        "labeler must mint a GitHub App token",
    )
    labeler_steps = [
        s for s in steps if uses_action(s, "actions/labeler@")
    ]
    f.check(len(labeler_steps) == 1, "labeler must invoke actions/labeler exactly once")
    repo_token = (labeler_steps[0].get("with") or {}).get("repo-token", "")
    evidence["repo-token"] = repo_token
    f.check(
        bool(APP_TOKEN_EXPR.search(str(repo_token))),
        f"actions/labeler must authenticate with App token, got {repo_token!r}",
    )
    f.check(
        not GITHUB_TOKEN_EXPR.search(str(repo_token)),
        "actions/labeler must not use GITHUB_TOKEN (pull-requests:write not needed)",
    )
    evidence["token_bindings"] = collect_token_bindings(job)
    return evidence


def test_tag(f: Failures) -> dict[str, Any]:
    data, _text = load_workflow(TAG)
    evidence: dict[str, Any] = {"workflow": "tag.yaml"}

    top_perm = data.get("permissions")
    evidence["top_permissions"] = top_perm
    f.check(
        top_perm == {"contents": "read"},
        f"tag top-level permissions must be contents:read only, got {top_perm}",
    )

    jobs = data.get("jobs") or {}
    job = jobs.get("main") or {}
    f.check(job, "tag job 'main' missing")
    f.check(
        "permissions" not in job,
        "tag job-level contents:write must be removed (writes go through App token)",
    )

    steps = steps_of(job)
    f.check(
        not any(uses_action(s, "actions/checkout@") for s in steps),
        "tag must not checkout with GITHUB_TOKEN",
    )
    f.check(
        any(uses_action(s, "actions/create-github-app-token@") for s in steps),
        "tag must mint a GitHub App token",
    )

    script_steps = [
        s for s in steps if uses_action(s, "actions/github-script@")
    ]
    f.check(len(script_steps) >= 2, "tag must have github-script read + write steps")
    for s in script_steps:
        gt = (s.get("with") or {}).get("github-token", "")
        f.check(
            bool(APP_TOKEN_EXPR.search(str(gt))),
            f"github-script step {s.get('name')!r} must pass App token, got {gt!r}",
        )
        f.check(
            not GITHUB_TOKEN_EXPR.search(str(gt)),
            f"github-script step {s.get('name')!r} must not use GITHUB_TOKEN",
        )

    api_calls = github_script_api_calls(job)
    evidence["api_calls"] = api_calls
    for required in ("listTags", "createTag", "createRef", "createRelease"):
        f.check(
            required in api_calls,
            f"tag scripts must call github.rest.*.{required} (via App token)",
        )
    # Semantic: writes are App-token-backed; GITHUB_TOKEN needs no contents:write
    evidence["token_bindings"] = collect_token_bindings(job)
    return evidence


def test_build_talosctl(f: Failures) -> dict[str, Any]:
    data, _text = load_workflow(BUILD)
    evidence: dict[str, Any] = {"workflow": "build-talosctl-busybox.yaml"}

    top_perm = data.get("permissions")
    evidence["top_permissions"] = top_perm
    f.check(
        top_perm == {"contents": "read"},
        f"build-talosctl top-level permissions must establish contents:read floor, got {top_perm}",
    )

    jobs = data.get("jobs") or {}
    # Keep existing job-level elevation
    build_jobs = [j for j in jobs.values() if (j.get("permissions") or {}).get("packages") == "write"]
    f.check(build_jobs, "build job must keep packages:write elevation")
    job = build_jobs[0]
    job_perm = job.get("permissions") or {}
    evidence["job_permissions"] = job_perm
    f.check(
        job_perm.get("contents") == "read" and job_perm.get("packages") == "write",
        f"build job permissions must be contents:read + packages:write, got {job_perm}",
    )
    f.check(
        "actions" not in job_perm,
        "build job must not add unused actions:write",
    )

    steps = steps_of(job)
    checkout = [s for s in steps if uses_action(s, "actions/checkout@")]
    f.check(checkout, "build must checkout")
    ck_token = (checkout[0].get("with") or {}).get("token", "")
    evidence["checkout_token"] = ck_token
    f.check(
        bool(APP_TOKEN_EXPR.search(str(ck_token))),
        f"checkout must use App token, got {ck_token!r}",
    )

    logins = [s for s in steps if uses_action(s, "docker/login-action@")]
    f.check(logins, "build must docker login to GHCR")
    password = (logins[0].get("with") or {}).get("password", "")
    evidence["login_password"] = password
    f.check(
        "GITHUB_TOKEN" in str(password),
        "docker/login-action must authenticate with GITHUB_TOKEN (needs packages:write)",
    )
    pushes = [s for s in steps if uses_action(s, "docker/build-push-action@")]
    f.check(pushes, "build must invoke docker/build-push-action")
    evidence["token_bindings"] = collect_token_bindings(job)
    return evidence


def test_image_pull(f: Failures) -> dict[str, Any]:
    data, text = load_workflow(IMAGE_PULL)
    flux, _ = load_workflow(FLUX_LOCAL)
    evidence: dict[str, Any] = {"workflow": "image-pull.yaml"}

    conc = data.get("concurrency") or {}
    flux_conc = flux.get("concurrency") or {}
    evidence["concurrency"] = conc
    evidence["flux_local_concurrency"] = flux_conc

    f.check(
        conc.get("group") == PEER_CONCURRENCY_GROUP,
        f"image-pull concurrency.group mismatch: {conc.get('group')!r}",
    )
    f.check(
        conc.get("cancel-in-progress") is False,
        "image-pull cancel-in-progress must remain false "
        f"(got {conc.get('cancel-in-progress')!r})",
    )
    f.check(
        flux_conc.get("cancel-in-progress") is True,
        "sanity: flux-local peer still cancels in progress",
    )

    comment = concurrency_comment_for(IMAGE_PULL, text)
    evidence["cancel_in_progress_comment"] = comment
    f.check(
        comment is not None and len(comment) > 0,
        "image-pull must document why cancel-in-progress is false",
    )
    if comment:
        lowered = comment.lower()
        f.check(
            any(
                k in lowered
                for k in ("talosctl", "retry", "pull", "node i/o", "node io")
            ),
            f"image-pull concurrency comment must explain live-node pull cost, got {comment!r}",
        )

    # fork guards on PR jobs must remain
    jobs = data.get("jobs") or {}
    guarded = {
        jid: j.get("if")
        for jid, j in jobs.items()
        if FORK_GUARD in str(j.get("if") or "")
    }
    evidence["fork_guarded_jobs"] = list(guarded)
    f.check(
        "filter" in guarded and "success" in guarded,
        f"image-pull fork guards must remain on filter/success, got {list(guarded)}",
    )
    return evidence


def test_all_pr_workflows_have_concurrency(f: Failures) -> dict[str, Any]:
    """Every pull_request-triggered workflow must declare a concurrency group."""
    missing: list[str] = []
    present: dict[str, Any] = {}
    for path in sorted(WF.glob("*.y*ml")):
        data, _ = load_workflow(path)
        on = on_block(data)
        if not (isinstance(on, dict) and "pull_request" in on):
            continue
        conc = data.get("concurrency")
        present[path.name] = conc
        if not conc:
            missing.append(path.name)
    f.check(not missing, f"PR workflows missing concurrency: {missing}")
    return {"pr_workflow_concurrency": present}


def main() -> int:
    failures = Failures()
    report: dict[str, Any] = {}

    report["labeler"] = test_labeler(failures)
    report["tag"] = test_tag(failures)
    report["build_talosctl"] = test_build_talosctl(failures)
    report["image_pull"] = test_image_pull(failures)
    report["pr_concurrency"] = test_all_pr_workflows_have_concurrency(failures)

    # Parseability of every changed workflow (consumer can load)
    for path in (LABELER, TAG, BUILD, IMAGE_PULL):
        try:
            load_workflow(path)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{path.name} failed to parse: {exc}")

    report["failures"] = list(failures)
    report["ok"] = not failures
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if failures:
        print(f"FAILED ({len(failures)}):", file=sys.stderr)
        for msg in failures:
            print(f"  - {msg}", file=sys.stderr)
        return 1
    print("OK: workflow hardening contracts held", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
