#!/usr/bin/env python3
"""Behavioral regression for ContainerRestartingChronically and ExternalSecretNotReady.

Background: in 2026-10 tdarr-node (13 days), kube-apiserver-talos-3 (7 days, a
restart every ~12 minutes) and an ExternalSecret (13 days) failed with no page.
KubePodCrashLooping needs 15m of continuous CrashLoopBackOff, so a slow restart
cycle never trips it, and nothing read externalsecret_status_condition.

Replayed against live Prometheus (14d) before writing the rules: the restart
expression matched tdarr-node, kube-apiserver, hermes, seerr-mcp, radarr and
sabnzbd episodes and nothing from the three node reboots on 2026-10-22. The
ExternalSecret expression matched cloudnative-pg-r2 for 13.6 days and was quiet
on the day of writing. The exporter is the external-secrets pod, so `namespace`
is `security` and the object's namespace is `exported_namespace`.

Runs the shipped PrometheusRule through `promtool test rules`:
  - restart every 12m for hours            -> silent early, fires once past 20 in 24h
  - reboot-sized burst (6 restarts) then flat -> silent
  - loop that stops after 30 restarts        -> fires while looping, silent after
  - ExternalSecret not Ready for 3h          -> fires after for: 30m
  - 10 minute not-Ready blip, ExternalSecret Ready -> silent
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ALERTS = ROOT / "kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts"
RULE = ALERTS / "silent-failure-gaps.yaml"
KUSTOMIZATION = ALERTS / "kustomization.yaml"
RESTARTS = "ContainerRestartingChronically"
ESO = "ExternalSecretNotReady"

POD_LABELS = {"namespace": "media", "pod": "tdarr-node-abc", "container": "app"}
ESO_LABELS = {
    "condition": "Ready",
    "name": "cloudnative-pg-r2",
    "exported_namespace": "database",
    "namespace": "security",
}


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


def selector(labels: dict[str, str]) -> str:
    return ",".join(f'{k}="{v}"' for k, v in labels.items())


def render(template: str, labels: dict[str, str]) -> str:
    text = re.sub(r"\{\{\s*\$labels\.(\w+)\s*\}\}", lambda m: labels[m.group(1)], template)
    return " ".join(text.split())


def expected(alert: dict, labels: dict[str, str]) -> dict:
    return {
        "exp_labels": {**labels, "severity": alert["labels"]["severity"]},
        "exp_annotations": {
            key: render(value, labels) for key, value in alert["annotations"].items()
        },
    }


def alert_case(
    name: str,
    metric: str,
    labels: dict[str, str],
    values: str,
    alert: dict,
    interval: str,
    at: list[tuple[str, bool]],
) -> dict:
    return {
        "name": name,
        "interval": interval,
        "input_series": [{"series": f"{metric}{{{selector(labels)}}}", "values": values}],
        "alert_rule_test": [
            {
                "eval_time": t,
                "alertname": alert["alert"],
                "exp_alerts": [expected(alert, labels)] if fires else [],
            }
            for t, fires in at
        ],
    }


def main() -> int:
    doc = yaml.safe_load(RULE.read_text())
    alerts = {r["alert"]: r for g in doc["spec"]["groups"] for r in g["rules"] if "alert" in r}
    for name in (RESTARTS, ESO):
        if name not in alerts or alerts[name]["labels"].get("severity") != "warning":
            raise Failure(f"{name} missing or not severity warning")
    kust = yaml.safe_load(KUSTOMIZATION.read_text())
    if "./silent-failure-gaps.yaml" not in kust["resources"]:
        raise Failure("silent-failure-gaps.yaml not wired into the alerts kustomization")

    restarts = alerts[RESTARTS]
    eso = alerts[ESO]
    counter = "kube_pod_container_status_restarts_total"
    gauge = "externalsecret_status_condition"
    tests = [
        # 1 restart per 12m step: 24h window reaches 20 after 4h, so silent at 3h.
        alert_case(
            "slow_loop_fires", counter, POD_LABELS, "0+1x400", restarts, "12m",
            [("3h", False), ("6h", True), ("30h", True)],
        ),
        # reboot-sized burst of 6 restarts, then flat for days.
        alert_case(
            "reboot_burst_silent", counter, POD_LABELS, "0 0 1 2 3 4 5 6 6x400", restarts, "12m",
            [("2h", False), ("6h", False), ("30h", False)],
        ),
        # 30 restarts over 6h, then flat: 24h count stays above 20, 1h count is 0.
        alert_case(
            "stopped_loop_clears", counter, POD_LABELS, "0+1x30 30x400", restarts, "12m",
            [("5h", True), ("10h", False), ("20h", False)],
        ),
        alert_case(
            "externalsecret_failing_fires", gauge, {**ESO_LABELS, "status": "False"},
            "1x400", eso, "5m",
            [("20m", False), ("1h", True), ("3h", True)],
        ),
        # 10 minute blip then back to Ready (False series drops to 0).
        alert_case(
            "externalsecret_blip_silent", gauge, {**ESO_LABELS, "status": "False"},
            "0 1 1 0x400", eso, "5m",
            [("20m", False), ("1h", False)],
        ),
    ]
    with tempfile.TemporaryDirectory(prefix="silent-gaps-") as tmp:
        work = Path(tmp)
        (work / "rules.yml").write_text(yaml.safe_dump({"groups": doc["spec"]["groups"]}, sort_keys=False))
        (work / "test.yml").write_text(
            yaml.safe_dump({"rule_files": ["rules.yml"], "evaluation_interval": "1m", "tests": tests}, sort_keys=False)
        )
        for args in (["check", "rules", "rules.yml"], ["test", "rules", "test.yml"]):
            proc = subprocess.run([promtool(), *args], cwd=work, capture_output=True, text=True)
            if proc.returncode != 0:
                raise Failure(f"promtool {' '.join(args)}:\n{proc.stdout}{proc.stderr}")
    print(f"[PASS] {RESTARTS}: slow loop fires, reboot burst and stopped loop silent")
    print(f"[PASS] {ESO}: sustained not-Ready fires, short blip silent")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        sys.exit(1)
