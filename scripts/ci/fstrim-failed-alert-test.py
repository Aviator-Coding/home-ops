#!/usr/bin/env python3
"""Behavioral regression for the FstrimJobFailed alert.

Background: on 2026-09-28 Job fstrim-29842800 (system/fstrim CronJob) failed
and nothing alerted for 2 days (follow-up to PR 1892). The alert reads
kube_cronjob_status_last_schedule_time vs kube_cronjob_status_last_successful_time
(names and labels verified live) instead of kube_job_status_failed, because the
failed Job object stays in history and would latch the alert.

Loads the shipped PrometheusRule and runs it through `promtool test rules`:
  - latest run failed (schedule ahead of success)   -> fires after for: 3h
  - latest run succeeded (success ahead of schedule) -> silent
  - run in flight for 1h, then succeeds              -> silent (for: absorbs it)
  - no success for over 8 days, no failed run        -> fires
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RULE = ROOT / "kubernetes/apps/base/system/fstrim/app/prometheusrule.yaml"
KUSTOMIZATION = ROOT / "kubernetes/apps/base/system/fstrim/app/kustomization.yaml"
ALERT = "FstrimJobFailed"
LABELS = 'namespace="system", cronjob="fstrim"'
EXP: dict = {}


class Failure(Exception):
    pass


def promtool() -> str:
    found = shutil.which("promtool")
    if found and subprocess.run([found, "--version"], capture_output=True).returncode == 0:
        return found
    mise = shutil.which("mise")
    if mise:
        proc = subprocess.run([mise, "which", "promtool"], capture_output=True, text=True)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    raise Failure("promtool not found (mise aqua:prometheus/prometheus)")


def series(metric: str, values: str) -> dict:
    return {"series": f"{metric}{{{LABELS}}}", "values": values}


def case(name: str, schedule: str, success: str, at: list[tuple[str, bool]]) -> dict:
    return {
        "name": name,
        "interval": "1h",
        "input_series": [
            series("kube_cronjob_status_last_schedule_time", schedule),
            series("kube_cronjob_status_last_successful_time", success),
        ],
        "alert_rule_test": [
            {
                "eval_time": t,
                "alertname": ALERT,
                "exp_alerts": [EXP] if fires else [],
            }
            for t, fires in at
        ],
    }


def main() -> int:
    doc = yaml.safe_load(RULE.read_text())
    alerts = {
        r["alert"]: r
        for g in doc["spec"]["groups"]
        for r in g["rules"]
        if "alert" in r
    }
    if ALERT not in alerts or alerts[ALERT]["labels"].get("severity") != "warning":
        raise Failure(f"{ALERT} missing or not severity warning")
    EXP["exp_labels"] = {"severity": "warning", "namespace": "system", "cronjob": "fstrim"}
    EXP["exp_annotations"] = alerts[ALERT]["annotations"]
    kust = yaml.safe_load(KUSTOMIZATION.read_text())
    if "./prometheusrule.yaml" not in kust["resources"]:
        raise Failure("prometheusrule.yaml not wired into the fstrim app kustomization")

    tests = [
        # last run failed: schedule 1 day in, success long before
        case("latest_run_failed_fires", "86400x20", "100x20", [("2h", False), ("4h", True), ("19h", True)]),
        # latest run succeeded: success after schedule, like the old failed Job
        # being superseded by a good run
        case("latest_run_succeeded_silent", "86400x20", "86460x20", [("4h", False), ("19h", False)]),
        # in flight for 1h (schedule ahead), then success lands
        case("in_flight_run_silent", "86400x20", "100 100 86460x18", [("2h", False), ("4h", False), ("19h", False)]),
        # no failed run, but no success for more than 8 days
        case("stale_success_fires", "100x240", "100x240", [("7d", False), ("8d", False), ("9d", True)]),
    ]
    with tempfile.TemporaryDirectory(prefix="fstrim-alert-") as tmp:
        work = Path(tmp)
        (work / "rules.yml").write_text(yaml.safe_dump({"groups": doc["spec"]["groups"]}, sort_keys=False))
        (work / "test.yml").write_text(
            yaml.safe_dump({"rule_files": ["rules.yml"], "evaluation_interval": "1h", "tests": tests}, sort_keys=False)
        )
        for args in (["check", "rules", "rules.yml"], ["test", "rules", "test.yml"]):
            proc = subprocess.run([promtool(), *args], cwd=work, capture_output=True, text=True)
            if proc.returncode != 0:
                raise Failure(f"promtool {' '.join(args)}:\n{proc.stdout}{proc.stderr}")
    print(f"[PASS] {ALERT}: failed-run fires, succeeded/in-flight silent, stale success fires")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)
