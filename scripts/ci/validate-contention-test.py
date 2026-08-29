#!/usr/bin/env python3
"""Behavioral contract for validate.yaml runner-pool contention hardening.

Pins the captain decision measured on PR #1481 run 33233099018 (2026-08-29):
under full fan-out on gha-runner-scale-set-aviator-coding-home-ops, mise-action
Setup Tools can take longer than the old 5–10m job timeouts, cancelling a
healthy job before it reaches its validator. The fix is larger timeouts on the
mise-heavy jobs plus ordering python-tests after the lighter ones so the heavy
litellm[proxy] pip install does not overlap their Setup Tools.

This is a machine-consumed GitHub Actions workflow contract. Assertions run on
a parsed YAML job graph and a small scheduler that mirrors Actions' needs/if
semantics (including always()/cancelled()/needs.*.result), not on source greps.

What this catches
  - mise-heavy job timeouts falling back below the measured contended floor
  - python-tests losing the ordered needs edge on lighter jobs
  - python-tests skipping when a path-filtered sibling is skipped (missing always())
  - python-tests running when filter itself failed or the run was cancelled
  - dropping .github/workflows/validate.yaml from per-job path filters
  - raising home-ops scale-set minRunners/maxRunners
  - removing or renaming the validator entrypoints the jobs invoke

What this does not catch
  - live cluster CPU contention (only CI can exercise that end-to-end)
  - whether 25m is enough on a future worse day (25m is a measured-bound floor
    with headroom, not a guarantee against unbounded starvation)
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
VALIDATE_WF = ROOT / ".github" / "workflows" / "validate.yaml"
HOME_OPS_HR = (
    ROOT
    / "kubernetes"
    / "apps"
    / "base"
    / "actions-runner-system"
    / "gha-runner-scale-set"
    / "app"
    / "aviator-coding"
    / "home-ops"
    / "helmrelease.yaml"
)

ARC_RUNNER = "gha-runner-scale-set-aviator-coding-home-ops"
FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"
WORKFLOW_SELF = ".github/workflows/validate.yaml"

# Measured 2026-08-29 (PR #1481). Contended lower bound for mise Setup Tools is
# the 602s cancel; uncontended jobs finish in ~17–31s. python-tests contended
# success was 469s. Timeouts must clear these floors with headroom.
MEASURED = {
    "talos_setup_tools_cancel_s": 602,
    "talos_alone_total_s": 17,
    "python_tests_contended_success_s": 469,
    "python_tests_pip_contended_s": 416,
    "mise_uncontended_setup_s_max": 23,
}

# Floor = measured kill (or success) with the committed headroom factor.
MISE_TIMEOUT_FLOOR_MIN = 25  # 2.5x the 10m kill; Setup Tools never finished
PYTHON_TIMEOUT_FLOOR_MIN = 20  # ~2.5x the 469s contended success
FILTER_TIMEOUT_MAX_MIN = 5
RENOVATE_TIMEOUT_MAX_MIN = 10

MISE_HEAVY_JOBS = ("talos", "versions", "bootstrap", "terraform")
LIGHT_PREDECESSORS = (
    "filter",
    "talos",
    "versions",
    "bootstrap",
    "renovate-config",
    "terraform",
)

# Validator entrypoints each job must still invoke (coverage must not shrink).
JOB_RUN_MARKERS = {
    "talos": "./scripts/ci/talos-validate.sh",
    "versions": "./scripts/ci/version-consistency.sh",
    "bootstrap": "kubeconform",
    "terraform": "./scripts/ci/tofu-validate.sh",
    "python-tests": "scripts/ci/*-test.py",
    "renovate-config": "renovate-config-validator",
}


class Failure(Exception):
    pass


@dataclass
class Job:
    id: str
    name: str | None
    runs_on: Any
    timeout_minutes: int | None
    needs: list[str]
    if_expr: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    outputs: dict[str, Any] = field(default_factory=dict)

    @property
    def run_scripts(self) -> list[str]:
        out: list[str] = []
        for step in self.steps:
            run = step.get("run")
            if isinstance(run, str):
                out.append(run)
        return out

    def uses_prefix(self, prefix: str) -> bool:
        for step in self.steps:
            uses = step.get("uses") or ""
            if uses.startswith(prefix):
                return True
        return False


@dataclass
class WorkflowModel:
    jobs: dict[str, Job]
    concurrency: dict[str, Any]
    path_filters: dict[str, list[str]]  # job_id filter step -> patterns


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise Failure(f"needs must be str or list, got {type(value)}")


def load_validate() -> WorkflowModel:
    text = VALIDATE_WF.read_text()
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise Failure(f"validate.yaml root must be mapping, got {type(data)}")

    raw_jobs = data.get("jobs") or {}
    jobs: dict[str, Job] = {}
    for jid, raw in raw_jobs.items():
        if not isinstance(raw, dict):
            raise Failure(f"job {jid} must be mapping")
        jobs[jid] = Job(
            id=jid,
            name=raw.get("name"),
            runs_on=raw.get("runs-on"),
            timeout_minutes=raw.get("timeout-minutes"),
            needs=_as_list(raw.get("needs")),
            if_expr=str(raw.get("if") or ""),
            steps=list(raw.get("steps") or []),
            outputs=dict(raw.get("outputs") or {}),
        )

    path_filters: dict[str, list[str]] = {}
    filter_job = jobs.get("filter")
    if filter_job is None:
        raise Failure("validate.yaml missing filter job")
    for step in filter_job.steps:
        sid = step.get("id")
        if not sid:
            continue
        patterns = (step.get("with") or {}).get("patterns") or ""
        path_filters[str(sid)] = [
            line.strip()
            for line in str(patterns).splitlines()
            if line.strip()
        ]

    return WorkflowModel(
        jobs=jobs,
        concurrency=dict(data.get("concurrency") or {}),
        path_filters=path_filters,
    )


def load_home_ops_scale() -> tuple[int, int]:
    data = yaml.safe_load(HOME_OPS_HR.read_text())
    values = (
        data.get("spec", {})
        .get("values", {})
    )
    # chart values may nest under runnerScaleSet / top-level depending on release
    min_r = values.get("minRunners")
    max_r = values.get("maxRunners")
    if min_r is None or max_r is None:
        # try nested common layout
        for key in ("runnerScaleSet", "githubConfigUrl", "template"):
            pass
        # walk shallow dicts
        def find(obj: Any, key: str) -> Any:
            if isinstance(obj, dict):
                if key in obj:
                    return obj[key]
                for v in obj.values():
                    got = find(v, key)
                    if got is not None:
                        return got
            return None

        min_r = find(data, "minRunners")
        max_r = find(data, "maxRunners")
    if min_r is None or max_r is None:
        raise Failure("home-ops HelmRelease missing minRunners/maxRunners")
    return int(min_r), int(max_r)


# ---------------------------------------------------------------------------
# Minimal GitHub Actions needs/if evaluator for the expressions we author.
# Only the operators used by validate.yaml are implemented.
# ---------------------------------------------------------------------------

_FUNC_ALWAYS = re.compile(r"\balways\s*\(\s*\)")
_FUNC_CANCELLED = re.compile(r"\bcancelled\s*\(\s*\)")
_NEEDS_RESULT = re.compile(
    r"needs\.([A-Za-z0-9_-]+)\.result\s*==\s*'([^']+)'"
)
_NEEDS_OUTPUT_NE = re.compile(
    r"needs\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*!=\s*'([^']*)'"
)
_NEEDS_OUTPUT_EQ = re.compile(
    r"needs\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*==\s*'([^']*)'"
)
_CONTEXT_EQ = re.compile(
    r"github\.event\.pull_request\.head\.repo\.full_name\s*==\s*github\.repository"
)


@dataclass
class JobState:
    result: str  # success | failure | skipped | cancelled
    outputs: dict[str, str] = field(default_factory=dict)


def _strip_expr(expr: str) -> str:
    expr = expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        return expr[3:-2].strip()
    return expr


def eval_if(
    expr: str,
    *,
    needs: dict[str, JobState],
    is_fork: bool,
    run_cancelled: bool,
) -> bool:
    """Evaluate the subset of Actions if-expressions used in validate.yaml."""
    if not expr:
        # Default: skip if any needed job did not succeed
        return True

    body = _strip_expr(expr)
    # Tokenize on && / || at top level (no nested parens beyond function calls).
    # All validate.yaml expressions are &&-joined only.
    parts = [p.strip() for p in re.split(r"&&", body)]
    for part in parts:
        negated = False
        token = part
        if token.startswith("!") and not token.startswith("!="):
            # !cancelled()
            negated = True
            token = token[1:].strip()

        if _FUNC_ALWAYS.fullmatch(token):
            val = True
        elif _FUNC_CANCELLED.fullmatch(token):
            val = run_cancelled
        elif _CONTEXT_EQ.fullmatch(token):
            val = not is_fork
        elif m := _NEEDS_RESULT.fullmatch(token):
            jid, want = m.group(1), m.group(2)
            st = needs.get(jid)
            val = st is not None and st.result == want
        elif m := _NEEDS_OUTPUT_NE.fullmatch(token):
            jid, out, want = m.group(1), m.group(2), m.group(3)
            st = needs.get(jid)
            got = (st.outputs.get(out) if st else None) or ""
            val = got != want
        elif m := _NEEDS_OUTPUT_EQ.fullmatch(token):
            jid, out, want = m.group(1), m.group(2), m.group(3)
            st = needs.get(jid)
            got = (st.outputs.get(out) if st else None) or ""
            val = got == want
        else:
            raise Failure(f"unsupported if-expression fragment: {part!r}")

        if negated:
            val = not val
        if not val:
            return False
    return True


def default_needs_gate(job: Job, needs: dict[str, JobState], if_pass: bool) -> bool:
    """Mirror Actions: without always(), a non-success dependency skips the job."""
    if not if_pass:
        return False
    has_always = bool(_FUNC_ALWAYS.search(_strip_expr(job.if_expr))) if job.if_expr else False
    if has_always:
        return True
    for dep in job.needs:
        st = needs.get(dep)
        if st is None or st.result != "success":
            return False
    return True


def schedule(
    model: WorkflowModel,
    *,
    changed_outputs: dict[str, str],
    is_fork: bool = False,
    run_cancelled: bool = False,
    force_results: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return job_id -> result for a synthetic validate.yaml run.

    changed_outputs maps filter output names (talos, versions, ...) to the
    changed-files JSON string the filter job would emit ('[]' or a non-empty
    sentinel). force_results can pin a predecessor to failure/cancelled after
    it is selected to run.
    """
    force_results = force_results or {}
    results: dict[str, JobState] = {}
    # Topological-ish: keep scanning until every job is decided.
    pending = set(model.jobs)
    guard = 0
    while pending:
        guard += 1
        if guard > len(model.jobs) * 4:
            raise Failure(f"scheduler stuck; pending={pending} results={results}")
        progress = False
        for jid in list(pending):
            job = model.jobs[jid]
            if any(dep not in results for dep in job.needs):
                continue
            # Build needs view
            needs_view = {d: results[d] for d in job.needs}
            # filter job synthesizes outputs when it runs
            if jid == "filter":
                if_pass = eval_if(
                    job.if_expr,
                    needs=needs_view,
                    is_fork=is_fork,
                    run_cancelled=run_cancelled,
                )
                if not default_needs_gate(job, needs_view, if_pass):
                    results[jid] = JobState(result="skipped")
                elif run_cancelled:
                    results[jid] = JobState(result="cancelled")
                else:
                    results[jid] = JobState(
                        result=force_results.get(jid, "success"),
                        outputs=dict(changed_outputs),
                    )
            else:
                if_pass = eval_if(
                    job.if_expr,
                    needs=needs_view,
                    is_fork=is_fork,
                    run_cancelled=run_cancelled,
                )
                if not default_needs_gate(job, needs_view, if_pass):
                    results[jid] = JobState(result="skipped")
                elif run_cancelled:
                    results[jid] = JobState(result="cancelled")
                else:
                    results[jid] = JobState(
                        result=force_results.get(jid, "success")
                    )
            pending.remove(jid)
            progress = True
        if not progress and pending:
            raise Failure(f"cyclic or unknown needs; pending={pending}")
    return {jid: st.result for jid, st in results.items()}


def _check(cond: bool, msg: str, failures: list[str]) -> None:
    if not cond:
        failures.append(msg)


def test_timeouts_meet_measured_floors(model: WorkflowModel) -> dict[str, Any]:
    failures: list[str] = []
    evidence: dict[str, Any] = {"measured": MEASURED, "timeouts": {}}
    for jid in MISE_HEAVY_JOBS:
        job = model.jobs[jid]
        evidence["timeouts"][jid] = job.timeout_minutes
        _check(
            job.timeout_minutes is not None
            and job.timeout_minutes >= MISE_TIMEOUT_FLOOR_MIN,
            f"{jid} timeout-minutes must be >= {MISE_TIMEOUT_FLOOR_MIN} "
            f"(measured Setup Tools cancel at {MEASURED['talos_setup_tools_cancel_s']}s); "
            f"got {job.timeout_minutes}",
            failures,
        )
        # Must clear the raw measured cancel in minutes.
        _check(
            job.timeout_minutes is not None
            and job.timeout_minutes * 60 > MEASURED["talos_setup_tools_cancel_s"],
            f"{jid} timeout must exceed measured {MEASURED['talos_setup_tools_cancel_s']}s cancel",
            failures,
        )

    pt = model.jobs["python-tests"]
    evidence["timeouts"]["python-tests"] = pt.timeout_minutes
    _check(
        pt.timeout_minutes is not None
        and pt.timeout_minutes >= PYTHON_TIMEOUT_FLOOR_MIN,
        f"python-tests timeout-minutes must be >= {PYTHON_TIMEOUT_FLOOR_MIN} "
        f"(contended success {MEASURED['python_tests_contended_success_s']}s); "
        f"got {pt.timeout_minutes}",
        failures,
    )
    _check(
        pt.timeout_minutes is not None
        and pt.timeout_minutes * 60
        > MEASURED["python_tests_contended_success_s"],
        "python-tests timeout must exceed measured contended success",
        failures,
    )

    filt = model.jobs["filter"]
    evidence["timeouts"]["filter"] = filt.timeout_minutes
    _check(
        filt.timeout_minutes == FILTER_TIMEOUT_MAX_MIN,
        f"filter timeout should stay {FILTER_TIMEOUT_MAX_MIN}m (not mise-heavy); "
        f"got {filt.timeout_minutes}",
        failures,
    )
    reno = model.jobs["renovate-config"]
    evidence["timeouts"]["renovate-config"] = reno.timeout_minutes
    _check(
        reno.timeout_minutes == RENOVATE_TIMEOUT_MAX_MIN,
        f"renovate-config timeout should stay {RENOVATE_TIMEOUT_MAX_MIN}m; "
        f"got {reno.timeout_minutes}",
        failures,
    )

    # Regression: the pre-fix ceilings must NOT still be in force.
    _check(
        model.jobs["versions"].timeout_minutes != 5,
        "versions must not remain at the pre-fix 5m ceiling (below 10m kill)",
        failures,
    )
    for jid in ("talos", "bootstrap", "terraform"):
        _check(
            model.jobs[jid].timeout_minutes != 10,
            f"{jid} must not remain at the pre-fix 10m ceiling",
            failures,
        )

    if failures:
        raise Failure("; ".join(failures))
    return evidence


def test_python_tests_ordered_after_light_jobs(model: WorkflowModel) -> dict[str, Any]:
    pt = model.jobs["python-tests"]
    needs = set(pt.needs)
    missing = [j for j in LIGHT_PREDECESSORS if j not in needs]
    if missing:
        raise Failure(
            f"python-tests must need lighter jobs {list(LIGHT_PREDECESSORS)}; "
            f"missing {missing}; got {pt.needs}"
        )
    # Must not introduce a self-cycle or depend only on filter.
    if pt.needs == ["filter"]:
        raise Failure("python-tests must not depend only on filter (fan-out overlap)")
    return {"python-tests.needs": pt.needs}


def test_python_tests_if_preserves_coverage(model: WorkflowModel) -> dict[str, Any]:
    pt = model.jobs["python-tests"]
    body = _strip_expr(pt.if_expr)
    if "always()" not in body:
        raise Failure(
            "python-tests if: must call always() so skipped predecessors do not "
            f"drop coverage; got {pt.if_expr!r}"
        )
    if "!cancelled()" not in body and "!(cancelled())" not in body:
        raise Failure(
            f"python-tests if: must exclude cancelled runs; got {pt.if_expr!r}"
        )
    if "needs.filter.result == 'success'" not in body:
        raise Failure(
            "python-tests if: must require needs.filter.result == 'success'; "
            f"got {pt.if_expr!r}"
        )
    if FORK_GUARD not in body:
        raise Failure(f"python-tests must keep fork guard; got {pt.if_expr!r}")
    if "needs.filter.outputs.pythontests != '[]'" not in body:
        raise Failure(
            "python-tests must still gate on pythontests path filter; "
            f"got {pt.if_expr!r}"
        )
    return {"python-tests.if": pt.if_expr}


def test_scheduler_scenarios(model: WorkflowModel) -> dict[str, Any]:
    """End-user-shaped scenarios: which jobs actually run under path filters."""
    full = {k: '["x"]' for k in (
        "talos", "versions", "bootstrap", "renovate", "terraform", "pythontests"
    )}
    scenarios: dict[str, dict[str, str]] = {}

    # 1. Full workflow-file fan-out: every gate selected.
    scenarios["full_fanout"] = schedule(model, changed_outputs=full)
    for jid in model.jobs:
        if scenarios["full_fanout"][jid] != "success":
            raise Failure(
                f"full fan-out: expected {jid}=success, got {scenarios['full_fanout']}"
            )

    # 2. scripts/ci-only PR: only pythontests output non-empty. Lighter jobs
    # skip immediately; python-tests must still run (always() + skipped deps).
    scripts_only = {
        "talos": "[]",
        "versions": "[]",
        "bootstrap": "[]",
        "renovate": "[]",
        "terraform": "[]",
        "pythontests": '["scripts/ci/x"]',
    }
    scenarios["scripts_ci_only"] = schedule(model, changed_outputs=scripts_only)
    exp_skip = {"talos", "versions", "bootstrap", "renovate-config", "terraform"}
    for jid in exp_skip:
        if scenarios["scripts_ci_only"][jid] != "skipped":
            raise Failure(
                f"scripts/ci-only: {jid} should skip, got {scenarios['scripts_ci_only']}"
            )
    if scenarios["scripts_ci_only"]["python-tests"] != "success":
        raise Failure(
            "scripts/ci-only: python-tests must still run when siblings skip "
            f"(got {scenarios['scripts_ci_only']})"
        )
    if scenarios["scripts_ci_only"]["filter"] != "success":
        raise Failure("scripts/ci-only: filter must succeed")

    # 3. Sibling failure must not hide python-tests (always() + filter success).
    scenarios["sibling_failure"] = schedule(
        model,
        changed_outputs=full,
        force_results={"talos": "failure"},
    )
    if scenarios["sibling_failure"]["talos"] != "failure":
        raise Failure("sibling_failure setup broken")
    if scenarios["sibling_failure"]["python-tests"] != "success":
        raise Failure(
            "failed talos must not skip python-tests "
            f"(got {scenarios['sibling_failure']})"
        )

    # 4. filter failure must skip python-tests (no coverage without filter).
    scenarios["filter_failure"] = schedule(
        model,
        changed_outputs=full,
        force_results={"filter": "failure"},
    )
    if scenarios["filter_failure"]["python-tests"] != "skipped":
        raise Failure(
            "filter failure must skip python-tests "
            f"(got {scenarios['filter_failure']})"
        )

    # 5. Cancelled run must not schedule python-tests work.
    scenarios["cancelled"] = schedule(
        model, changed_outputs=full, run_cancelled=True
    )
    if scenarios["cancelled"]["python-tests"] not in {"skipped", "cancelled"}:
        raise Failure(
            f"cancelled run must not run python-tests: {scenarios['cancelled']}"
        )

    # 6. Fork PR: filter skips, everything skips.
    scenarios["fork"] = schedule(model, changed_outputs=full, is_fork=True)
    if scenarios["fork"]["filter"] != "skipped":
        raise Failure(f"fork must skip filter: {scenarios['fork']}")
    if scenarios["fork"]["python-tests"] != "skipped":
        raise Failure(f"fork must skip python-tests: {scenarios['fork']}")

    # 7. Without always()-style gate, the OLD python-tests if would drop coverage
    # on scripts/ci-only. Prove the new expression is what saves it by evaluating
    # the pre-fix expression shape against the same needs view.
    old_if = (
        "${{ github.event.pull_request.head.repo.full_name == github.repository "
        "&& needs.filter.outputs.pythontests != '[]' }}"
    )
    # Simulate post-filter states for scripts/ci-only under OLD needs: [filter] only
    # vs NEW needs with skipped siblings + old if (no always) → skipped by default gate.
    old_job = Job(
        id="python-tests-old",
        name="python-tests",
        runs_on=ARC_RUNNER,
        timeout_minutes=20,
        needs=["filter"],  # pre-fix needs
        if_expr=old_if,
    )
    # Under NEW needs list but OLD if (no always): default gate skips on skipped dep.
    old_if_new_needs = Job(
        id="python-tests-old-if",
        name="python-tests",
        runs_on=ARC_RUNNER,
        timeout_minutes=20,
        needs=list(LIGHT_PREDECESSORS),
        if_expr=old_if,
    )
    needs_view = {
        "filter": JobState(result="success", outputs=scripts_only),
        "talos": JobState(result="skipped"),
        "versions": JobState(result="skipped"),
        "bootstrap": JobState(result="skipped"),
        "renovate-config": JobState(result="skipped"),
        "terraform": JobState(result="skipped"),
    }
    old_if_pass = eval_if(
        old_if_new_needs.if_expr,
        needs=needs_view,
        is_fork=False,
        run_cancelled=False,
    )
    old_runs = default_needs_gate(old_if_new_needs, needs_view, old_if_pass)
    if old_runs:
        raise Failure(
            "regression probe: old if + new needs should NOT run python-tests "
            "when siblings are skipped (always() is load-bearing)"
        )
    new_if_pass = eval_if(
        model.jobs["python-tests"].if_expr,
        needs=needs_view,
        is_fork=False,
        run_cancelled=False,
    )
    new_runs = default_needs_gate(
        model.jobs["python-tests"], needs_view, new_if_pass
    )
    if not new_runs:
        raise Failure(
            "regression probe: new if + new needs must run python-tests when "
            "siblings are skipped"
        )
    # Pre-fix needs:[filter] + old if still runs on scripts/ci-only (no ordering).
    old_pre_needs_view = {"filter": JobState(result="success", outputs=scripts_only)}
    old_pre_if = eval_if(
        old_job.if_expr,
        needs=old_pre_needs_view,
        is_fork=False,
        run_cancelled=False,
    )
    if not default_needs_gate(old_job, old_pre_needs_view, old_pre_if):
        raise Failure("sanity: pre-fix python-tests should run on scripts/ci-only")

    scenarios["regression_old_if_with_new_needs_runs"] = {
        "old": str(old_runs),
        "new": str(new_runs),
    }
    return scenarios


def test_path_filters_keep_workflow_self_test(model: WorkflowModel) -> dict[str, Any]:
    failures: list[str] = []
    # Every per-job filter step must still include the workflow file so an edit
    # self-tests rather than skipping gates.
    expected_steps = (
        "talos",
        "versions",
        "bootstrap",
        "renovate",
        "terraform",
        "pythontests",
    )
    for sid in expected_steps:
        patterns = model.path_filters.get(sid) or []
        _check(
            WORKFLOW_SELF in patterns,
            f"filter step {sid!r} must still match {WORKFLOW_SELF} for self-test; "
            f"got {patterns}",
            failures,
        )
    if failures:
        raise Failure("; ".join(failures))
    return {"path_filters": model.path_filters}


def test_runner_capacity_unchanged() -> dict[str, Any]:
    min_r, max_r = load_home_ops_scale()
    if min_r != 1 or max_r != 15:
        raise Failure(
            f"home-ops scale set capacity must stay minRunners=1 maxRunners=15; "
            f"got min={min_r} max={max_r}"
        )
    return {"minRunners": min_r, "maxRunners": max_r, "name": ARC_RUNNER}


def test_jobs_still_on_home_ops_runner(model: WorkflowModel) -> dict[str, Any]:
    evidence: dict[str, str] = {}
    for jid, job in model.jobs.items():
        evidence[jid] = str(job.runs_on)
        if job.runs_on != ARC_RUNNER:
            raise Failure(
                f"{jid} must run on {ARC_RUNNER}; got {job.runs_on!r}"
            )
    return evidence


def test_validation_entrypoints_preserved(model: WorkflowModel) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for jid, marker in JOB_RUN_MARKERS.items():
        job = model.jobs[jid]
        blob = "\n".join(job.run_scripts)
        evidence[jid] = job.run_scripts
        if marker not in blob:
            raise Failure(
                f"{jid} must still invoke {marker!r}; runs={job.run_scripts!r}"
            )
        # mise-heavy jobs (except renovate) still use mise-action
        if jid in MISE_HEAVY_JOBS or jid == "python-tests":
            if not job.uses_prefix("jdx/mise-action@"):
                raise Failure(f"{jid} must still use jdx/mise-action")
    return evidence


def test_no_workflow_level_job_serialization(model: WorkflowModel) -> dict[str, Any]:
    """Lighter mise jobs must still be siblings (not a linear chain).

    Serializing the whole set would count queue time toward timeout-minutes.
    """
    for jid in MISE_HEAVY_JOBS:
        needs = model.jobs[jid].needs
        if needs != ["filter"]:
            raise Failure(
                f"{jid} must need only filter (parallel fan-out); got {needs}. "
                "Do not job-concurrency-serialize the mise set."
            )
    reno = model.jobs["renovate-config"].needs
    if reno != ["filter"]:
        raise Failure(f"renovate-config must need only filter; got {reno}")
    # workflow concurrency is PR-scoped cancel-in-progress, not a job mutex
    conc = model.concurrency
    if conc.get("cancel-in-progress") is not True:
        raise Failure(
            f"workflow concurrency cancel-in-progress should stay true; got {conc}"
        )
    return {"mise_needs": {j: model.jobs[j].needs for j in MISE_HEAVY_JOBS}, "concurrency": conc}


def main() -> int:
    report: dict[str, Any] = {"ok": False, "tests": {}}
    failures: list[str] = []

    try:
        model = load_validate()
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"parse: {exc}"}, indent=2))
        print(f"FAILED: parse validate.yaml: {exc}", file=sys.stderr)
        return 1

    tests = [
        ("timeouts_meet_measured_floors", lambda: test_timeouts_meet_measured_floors(model)),
        ("python_tests_ordered_after_light_jobs", lambda: test_python_tests_ordered_after_light_jobs(model)),
        ("python_tests_if_preserves_coverage", lambda: test_python_tests_if_preserves_coverage(model)),
        ("scheduler_scenarios", lambda: test_scheduler_scenarios(model)),
        ("path_filters_keep_workflow_self_test", lambda: test_path_filters_keep_workflow_self_test(model)),
        ("runner_capacity_unchanged", test_runner_capacity_unchanged),
        ("jobs_still_on_home_ops_runner", lambda: test_jobs_still_on_home_ops_runner(model)),
        ("validation_entrypoints_preserved", lambda: test_validation_entrypoints_preserved(model)),
        ("no_workflow_level_job_serialization", lambda: test_no_workflow_level_job_serialization(model)),
    ]

    for name, fn in tests:
        try:
            evidence = fn()
            report["tests"][name] = {"status": "PASS", "evidence": evidence}
            print(f"[PASS] {name}", file=sys.stderr)
        except Failure as exc:
            report["tests"][name] = {"status": "FAIL", "error": str(exc)}
            failures.append(f"{name}: {exc}")
            print(f"[FAIL] {name}: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            report["tests"][name] = {"status": "ERROR", "error": str(exc)}
            failures.append(f"{name}: {exc}")
            print(f"[ERROR] {name}: {exc}", file=sys.stderr)

    report["ok"] = not failures
    report["failures"] = failures
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    if failures:
        print(f"FAILED ({len(failures)})", file=sys.stderr)
        return 1
    print("OK: validate.yaml contention contracts held", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
