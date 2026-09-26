#!/usr/bin/env python3
"""Promtool and manifest contract for the backup-age, kopiur-policy, and tdarr alerts.

Live Prometheus on 2026-09-26, before these rules existed:

  Postgres backup timestamp was a real unix time only on the primary
  (age ~8.9h). Both standbys were exactly 0. A 14d max on timestamp > 0
  was ~23.9h, under the 36h threshold. No series was timestamp > 0 and
  in recovery at the same time. The WAL archiver series existed only on
  the primary; lag was ~84s and the 7d peak was ~5 minutes.

  count(kopiur_policy_last_backup_success_timestamp_seconds) was 62 now,
  at offset 24h, and for 7d (min = max = 62). The vanished-policy
  expression returned no series. Dropping one live policy from the
  right-hand set returned that one series.

  Tdarr :8265 /api/v2/status was {"status":"good",...}. :8266 answered
  HTTP 302 to :8265. get-nodes reported the in-cluster node with a
  process lastUpdateTime about 1s old while idle. The same command
  exited 1 for a 1ms ceiling and for an unknown nodeName. The node
  process does not listen.

This test loads the manifests Flux would apply and:

  1. Feeds the PrometheusRules to promtool. Empty live expressions are
     the healthy case; the unit tests are what prove the rules fire.
  2. Pins the Gatus endpoints that give Echo, Kromgo, and Tdarr :8266
     a series for the existing GatusEndpointDown / GatusServiceDown rules.
  3. Pins the tdarr-node probes: liveness is the local process only, so
     a server API blip cannot restart the GPU worker.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CNPG_RULE = (
    ROOT
    / "kubernetes/apps/base/database/cloudnative-pg/cluster-17/prometheusrule.yaml"
)
KOPIUR_RULE = ROOT / "kubernetes/apps/base/system/kopiur/app/prometheusrule.yaml"
GATUS_RULE = ROOT / "kubernetes/apps/base/monitoring/gatus/app/prometheusrule.yaml"
GATUS_CONFIG = (
    ROOT / "kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml"
)
TDARR_HR = ROOT / "kubernetes/apps/base/media/tdarr/app/helmrelease.yaml"

PROMTOOL_IMAGE = os.environ.get(
    "PROMTOOL_IMAGE", "quay.io/prometheus/prometheus:v3.2.1"
)

BACKUP_STALE = "PostgresBackupStale"
BACKUP_ABSENT = "PostgresBackupTimestampAbsent"
WAL_STALE = "PostgresWALArchiveStale"
WAL_ABSENT = "PostgresWALArchiveTimestampAbsent"
POLICY_GONE = "KopiurPolicyVanished"

# 40h at a 4m sample step stays inside Prometheus' 5m staleness.
CNPG_STEP_S = 4 * 60
CNPG_SAMPLES = (40 * 60 * 60) // CNPG_STEP_S + 10

# Kopiur offset is 24h. 1m samples so a 30m `for` resolves, and the
# series that disappears at 24h has been gone longer than `for` by 25h.
KOPIUR_MINUTES = 26 * 60


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_docs(path: Path) -> list[dict[str, Any]]:
    text = path.read_text()
    docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
    require(docs, f"{path} parsed to no documents")
    return docs


def prometheus_rule(path: Path) -> dict[str, Any]:
    rules = [d for d in load_docs(path) if d.get("kind") == "PrometheusRule"]
    require(len(rules) == 1, f"{path} should contain one PrometheusRule")
    return rules[0]


def alerts_by_name(rule: dict[str, Any]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for group in rule["spec"]["groups"]:
        for alert in group.get("rules") or []:
            name = alert.get("alert")
            if name:
                found[name] = alert
    return found


def _resolve_promtool() -> str | None:
    on_path = shutil.which("promtool")
    candidates: list[Path] = []
    if on_path:
        candidates.append(Path(on_path))
    mise_root = Path.home() / ".local/share/mise/installs/aqua-prometheus-prometheus"
    if mise_root.is_dir():
        candidates.extend(sorted(mise_root.glob("*/prometheus-*/promtool"), reverse=True))
    for cand in candidates:
        if not cand.is_file():
            continue
        try:
            if cand.is_symlink() and "mise" in os.path.basename(os.readlink(cand)):
                continue
        except OSError:
            pass
        probe = subprocess.run(
            [str(cand), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0 and "promtool" in (probe.stdout + probe.stderr).lower():
            return str(cand)
    return None


def _podman_runnable() -> bool:
    if shutil.which("podman") is None:
        return False
    probe = subprocess.run(
        ["podman", "info", "--format", "{{.Host.RemoteSocket.Exists}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def _run_promtool(args: list[str], cwd: Path) -> str:
    promtool = _resolve_promtool()
    if promtool is not None:
        cmd = [promtool, *args]
        run_cwd: str | None = str(cwd)
    elif _podman_runnable():
        cmd = [
            "podman",
            "run",
            "--rm",
            "--entrypoint",
            "promtool",
            "-v",
            f"{cwd}:/work:Z",
            "-w",
            "/work",
            PROMTOOL_IMAGE,
            *args,
        ]
        run_cwd = None
    else:
        raise Failure(
            "promtool is required to evaluate PrometheusRule expr "
            "(install native promtool via mise aqua:prometheus/prometheus, "
            "or provide a working podman for image fallback)"
        )
    proc = subprocess.run(
        cmd, check=False, capture_output=True, text=True, cwd=run_cwd
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise Failure(
            f"promtool {' '.join(args)} failed (exit {proc.returncode}):\n{out.strip()}"
        )
    return out.strip() or "SUCCESS"


def _repeat(value: int, n: int) -> str:
    return " ".join([str(value)] * n)


def _gap_then(gap: int, value: int, n_value: int) -> str:
    """Leading missing samples, then a constant. `_` is a promtool gap."""
    return " ".join(["_"] * gap + [str(value)] * n_value)


def _track(n: int, step_s: int, lag_s: int) -> str:
    """Timestamp samples that stay `lag_s` behind each sample's own time."""
    vals: list[str] = []
    for i in range(n):
        t = i * step_s
        vals.append(str(t - lag_s if t > lag_s else 1))
    return " ".join(vals)


def _write_rule_file(path: Path, rule: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump({"groups": rule["spec"]["groups"]}, sort_keys=False))


def _expand(text: str, labels: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        require(key in labels, f"annotation uses $labels.{key}, labels are {labels}")
        return labels[key]

    expanded = re.sub(r"\{\{\s*\$labels\.([A-Za-z0-9_]+)\s*\}\}", repl, text)
    require("{{" not in expanded, f"unexpanded annotation: {expanded!r}")
    return expanded


def _expect(alert: dict[str, Any], labels: dict[str, str]) -> dict[str, Any]:
    annotations = alert.get("annotations") or {}
    return {
        "exp_labels": {"alertname": alert["alert"], "severity": alert["labels"]["severity"], **labels},
        "exp_annotations": {
            key: _expand(str(value), labels) for key, value in annotations.items()
        },
    }


def _series(metric: str, labels: dict[str, str], values: str) -> dict[str, str]:
    inner = ",".join(f'{k}="{v}"' for k, v in labels.items())
    return {"series": f"{metric}{{{inner}}}", "values": values}


def assert_label_contract(alerts: dict[str, dict[str, Any]], names: list[str]) -> None:
    for name in names:
        require(name in alerts, f"missing alert {name}")
        labels = alerts[name].get("labels") or {}
        require(labels.get("severity") == "warning", f"{name} must be warning, got {labels}")
        require(
            set(labels) == {"severity"},
            f"{name}: alert labels must be severity only, got {labels}",
        )


def assert_cnpg_contract(alerts: dict[str, dict[str, Any]]) -> None:
    assert_label_contract(alerts, [BACKUP_STALE, BACKUP_ABSENT, WAL_STALE, WAL_ABSENT])
    backup = alerts[BACKUP_STALE]["expr"]
    require("129600" in backup, "backup stale threshold must be 36h in seconds")
    require("> 0" in backup, "backup stale must ignore the standby timestamp of 0")
    require("cnpg_pg_replication_in_recovery == 0" in backup, "backup stale must select the primary")
    require(alerts[BACKUP_STALE].get("for") == "15m", "backup stale for: 15m")
    wal = alerts[WAL_STALE]["expr"]
    require("> 3600" in wal, "WAL stale threshold must be 1h")
    require("cnpg_pg_stat_archiver_last_archived_time > 0" in wal, "WAL stale must ignore a 0 timestamp")
    require("cnpg_pg_replication_in_recovery == 0" in wal, "WAL stale must select the primary")
    require(alerts[WAL_STALE].get("for") == "10m", "WAL stale for: 10m")
    for name in (BACKUP_ABSENT, WAL_ABSENT):
        expr = alerts[name]["expr"]
        require("unless on(pod)" in expr, f"{name} must be the primary unless a positive timestamp")
        require(alerts[name].get("for") == "15m", f"{name} for: 15m")


def assert_kopiur_contract(alerts: dict[str, dict[str, Any]]) -> None:
    assert_label_contract(alerts, [POLICY_GONE])
    expr = alerts[POLICY_GONE]["expr"]
    require("offset 24h" in expr, "policy-vanished must look back 24h, not a hardcoded count")
    require("unless" in expr, "policy-vanished must be a set difference, not a scalar count")
    require('count by (namespace, policy)' in expr, "policy-vanished must keep namespace and policy")
    require(
        'up{job="kopiur-controller-metrics"} == 1' in expr,
        "policy-vanished must stay quiet when the exporter is down",
    )
    require("< 62" not in expr and "<62" not in expr, "policy-vanished must not pin the fleet count")
    require(alerts[POLICY_GONE].get("for") == "30m", "policy-vanished for: 30m")
    summary = (alerts[POLICY_GONE].get("annotations") or {}).get("summary", "")
    require(
        "{{ $labels.namespace }}/{{ $labels.policy }}" in summary,
        f"summary must keep namespace/policy adjacent, got {summary!r}",
    )


def assert_cnpg_promtool(rule: dict[str, Any], alerts: dict[str, dict[str, Any]]) -> None:
    n = CNPG_SAMPLES
    step = CNPG_STEP_S
    fresh_backup = _repeat(111600, n)  # age ~9h at t=40h, under 36h for the whole window
    stale_backup = _repeat(10000, n)
    fresh_wal = _track(n, step, 84)
    stale_wal = _repeat(10000, n)
    primary = _repeat(0, n)
    standby = _repeat(1, n)
    zero_ts = _repeat(0, n)

    def pod(name: str) -> dict[str, str]:
        return {"namespace": "database", "pod": name}

    series = [
        _series("cnpg_collector_last_available_backup_timestamp", pod("pg-fresh"), fresh_backup),
        _series("cnpg_pg_stat_archiver_last_archived_time", pod("pg-fresh"), fresh_wal),
        _series("cnpg_pg_replication_in_recovery", pod("pg-fresh"), primary),
        _series("cnpg_collector_last_available_backup_timestamp", pod("pg-stale-backup"), stale_backup),
        _series("cnpg_pg_stat_archiver_last_archived_time", pod("pg-stale-backup"), fresh_wal),
        _series("cnpg_pg_replication_in_recovery", pod("pg-stale-backup"), primary),
        _series("cnpg_collector_last_available_backup_timestamp", pod("pg-stale-wal"), fresh_backup),
        _series("cnpg_pg_stat_archiver_last_archived_time", pod("pg-stale-wal"), stale_wal),
        _series("cnpg_pg_replication_in_recovery", pod("pg-stale-wal"), primary),
        _series("cnpg_collector_last_available_backup_timestamp", pod("pg-replica"), zero_ts),
        _series("cnpg_pg_replication_in_recovery", pod("pg-replica"), standby),
        _series("cnpg_collector_last_available_backup_timestamp", pod("pg-demoted"), stale_backup),
        _series("cnpg_pg_stat_archiver_last_archived_time", pod("pg-demoted"), stale_wal),
        _series("cnpg_pg_replication_in_recovery", pod("pg-demoted"), standby),
        _series("cnpg_pg_replication_in_recovery", pod("pg-unmeasured"), primary),
    ]

    def exp(name: str, pod_name: str) -> dict[str, Any]:
        return _expect(alerts[name], pod(pod_name))

    absent = [exp(BACKUP_ABSENT, "pg-unmeasured")]
    wal_absent = [exp(WAL_ABSENT, "pg-unmeasured")]
    none: list[dict[str, Any]] = []

    test_doc = {
        "rule_files": ["cnpg_rules.yml"],
        "evaluation_interval": "1m",
        "tests": [
            {
                "name": "primary_standby_and_demoted",
                "interval": f"{step}s",
                "input_series": series,
                "alert_rule_test": [
                    {"eval_time": "14m", "alertname": BACKUP_ABSENT, "exp_alerts": none},
                    {"eval_time": "14m", "alertname": WAL_ABSENT, "exp_alerts": none},
                    {"eval_time": "15m", "alertname": BACKUP_ABSENT, "exp_alerts": absent},
                    {"eval_time": "15m", "alertname": WAL_ABSENT, "exp_alerts": wal_absent},
                    {"eval_time": "15m", "alertname": BACKUP_STALE, "exp_alerts": none},
                    {"eval_time": "15m", "alertname": WAL_STALE, "exp_alerts": none},
                    {"eval_time": "4h", "alertname": WAL_STALE, "exp_alerts": [exp(WAL_STALE, "pg-stale-wal")]},
                    {"eval_time": "4h", "alertname": BACKUP_STALE, "exp_alerts": none},
                    {"eval_time": "38h", "alertname": BACKUP_STALE, "exp_alerts": none},
                    {"eval_time": "40h", "alertname": BACKUP_STALE, "exp_alerts": [exp(BACKUP_STALE, "pg-stale-backup")]},
                    {"eval_time": "40h", "alertname": WAL_STALE, "exp_alerts": [exp(WAL_STALE, "pg-stale-wal")]},
                    {"eval_time": "40h", "alertname": BACKUP_ABSENT, "exp_alerts": absent},
                    {"eval_time": "40h", "alertname": WAL_ABSENT, "exp_alerts": wal_absent},
                ],
            }
        ],
    }
    with tempfile.TemporaryDirectory(prefix="cnpg-backup-alert-") as tmp:
        work = Path(tmp)
        _write_rule_file(work / "cnpg_rules.yml", rule)
        _run_promtool(["check", "rules", "cnpg_rules.yml"], work)
        path = work / "cnpg_test.yml"
        path.write_text(yaml.safe_dump(test_doc, sort_keys=False, width=100000))
        _run_promtool(["test", "rules", path.name], work)


def assert_kopiur_promtool(rule: dict[str, Any], alerts: dict[str, dict[str, Any]]) -> None:
    present = _repeat(1, KOPIUR_MINUTES)
    dropped = _repeat(1, 24 * 60)
    arrived = _gap_then(24 * 60, 1, 2 * 60)
    up_ok = _repeat(1, KOPIUR_MINUTES)
    up_down = _repeat(0, KOPIUR_MINUTES)

    def policy(namespace: str, name: str) -> dict[str, str]:
        return {"namespace": namespace, "policy": name}

    def ts(labels: dict[str, str], values: str) -> dict[str, str]:
        return _series("kopiur_policy_last_backup_success_timestamp_seconds", labels, values)

    def up(values: str) -> dict[str, str]:
        return _series(
            "up",
            {"job": "kopiur-controller-metrics", "namespace": "system", "pod": "kopiur-1"},
            values,
        )

    gone = _expect(alerts[POLICY_GONE], policy("downloads", "gone-ceph"))

    def case(name: str, series: list[dict[str, str]], checks: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "name": name,
            "interval": "1m",
            "input_series": series,
            "alert_rule_test": checks,
        }

    test_doc = {
        "rule_files": ["kopiur_rules.yml"],
        "evaluation_interval": "1m",
        "tests": [
            case(
                "stable_inventory_stays_silent",
                [ts(policy("media", "stay-ceph"), present), up(up_ok)],
                [{"eval_time": "25h", "alertname": POLICY_GONE, "exp_alerts": []}],
            ),
            case(
                "one_policy_removed",
                [
                    ts(policy("downloads", "gone-ceph"), dropped),
                    ts(policy("media", "stay-ceph"), present),
                    up(up_ok),
                ],
                [
                    {"eval_time": "24h10m", "alertname": POLICY_GONE, "exp_alerts": []},
                    {"eval_time": "25h", "alertname": POLICY_GONE, "exp_alerts": [gone]},
                ],
            ),
            case(
                "exporter_down_does_not_page_every_policy",
                [ts(policy("downloads", "gone-ceph"), dropped), up(up_down)],
                [{"eval_time": "25h", "alertname": POLICY_GONE, "exp_alerts": []}],
            ),
            case(
                "add_and_remove_still_names_the_removal",
                [
                    ts(policy("downloads", "gone-ceph"), dropped),
                    ts(policy("ai", "new-ceph"), arrived),
                    up(up_ok),
                ],
                [{"eval_time": "25h", "alertname": POLICY_GONE, "exp_alerts": [gone]}],
            ),
        ],
    }
    with tempfile.TemporaryDirectory(prefix="kopiur-policy-alert-") as tmp:
        work = Path(tmp)
        _write_rule_file(work / "kopiur_rules.yml", rule)
        _run_promtool(["check", "rules", "kopiur_rules.yml"], work)
        path = work / "kopiur_test.yml"
        path.write_text(yaml.safe_dump(test_doc, sort_keys=False, width=100000))
        _run_promtool(["test", "rules", path.name], work)


def assert_gatus_promtool(rule: dict[str, Any], alerts: dict[str, dict[str, Any]]) -> None:
    require("GatusEndpointDown" in alerts, "GatusEndpointDown missing")
    require("GatusServiceDown" in alerts, "GatusServiceDown missing")
    down = _repeat(0, 30)
    up = _repeat(1, 30)

    def ep(group: str, name: str, values: str) -> dict[str, str]:
        return _series(
            "gatus_results_endpoint_success",
            {"group": group, "name": name, "job": "gatus"},
            values,
        )

    echo_down = _expect(alerts["GatusEndpointDown"], {"group": "external", "name": "Echo", "job": "gatus"})
    tdarr_down = _expect(
        alerts["GatusServiceDown"], {"group": "media", "name": "Tdarr Server", "job": "gatus"}
    )
    test_doc = {
        "rule_files": ["gatus_rules.yml"],
        "evaluation_interval": "1m",
        "tests": [
        {
            "name": "incluster_echo_down_is_critical",
            "interval": "1m",
            "input_series": [ep("external", "Echo", down)],
            "alert_rule_test": [
                {"eval_time": "4m", "alertname": "GatusEndpointDown", "exp_alerts": []},
                {"eval_time": "5m", "alertname": "GatusEndpointDown", "exp_alerts": [echo_down]},
                {"eval_time": "20m", "alertname": "GatusServiceDown", "exp_alerts": []},
            ],
        },
        {
            "name": "incluster_echo_up_stays_silent",
            "interval": "1m",
            "input_series": [ep("external", "Echo", up)],
            "alert_rule_test": [
                {"eval_time": "20m", "alertname": "GatusEndpointDown", "exp_alerts": []},
            ],
        },
        {
            "name": "tdarr_server_port_down_is_warning",
            "interval": "1m",
            "input_series": [ep("media", "Tdarr Server", down)],
            "alert_rule_test": [
                {"eval_time": "9m", "alertname": "GatusServiceDown", "exp_alerts": []},
                {"eval_time": "10m", "alertname": "GatusServiceDown", "exp_alerts": [tdarr_down]},
                {"eval_time": "20m", "alertname": "GatusEndpointDown", "exp_alerts": []},
            ],
        },
        {
            "name": "connectivity_icmp_is_not_an_app_alert",
            "interval": "1m",
            "input_series": [ep("connectivity", "Cloudflare", down)],
            "alert_rule_test": [
                {"eval_time": "20m", "alertname": "GatusEndpointDown", "exp_alerts": []},
                {"eval_time": "20m", "alertname": "GatusServiceDown", "exp_alerts": []},
            ],
        },
        ],
    }
    with tempfile.TemporaryDirectory(prefix="gatus-coverage-alert-") as tmp:
        work = Path(tmp)
        _write_rule_file(work / "gatus_rules.yml", rule)
        _run_promtool(["check", "rules", "gatus_rules.yml"], work)
        path = work / "gatus_test.yml"
        path.write_text(yaml.safe_dump(test_doc, sort_keys=False, width=100000))
        _run_promtool(["test", "rules", path.name], work)


def assert_gatus_endpoints() -> None:
    cfg = yaml.safe_load(GATUS_CONFIG.read_text())
    endpoints = {item["name"]: item for item in cfg["endpoints"]}
    echo = endpoints["Echo"]
    require(echo["group"] == "external", "Echo must be group external so GatusEndpointDown covers it")
    require(echo["url"] == "http://echo.network.svc.cluster.local/healthz", echo["url"])
    require("[STATUS] == 200" in echo["conditions"], "Echo must require HTTP 200")
    require("[BODY].path == /healthz" in echo["conditions"], "Echo must match its own JSON path")
    kromgo = endpoints["Kromgo"]
    require(kromgo["group"] == "external", "Kromgo must be group external")
    require(kromgo["url"] == "http://kromgo.monitoring.svc.cluster.local:8080/readyz", kromgo["url"])
    require("[BODY] == OK" in kromgo["conditions"], "Kromgo must match the plain OK body")
    tdarr = endpoints["Tdarr Server"]
    require(tdarr["group"] == "media", "Tdarr Server must not join the critical external group")
    require(tdarr["group"] not in {"external", "connectivity"}, tdarr["group"])
    require(tdarr["url"] == "tcp://tdarr.media.svc.cluster.local:8266", tdarr["url"])
    require("[CONNECTED] == true" in tdarr["conditions"], "Tdarr Server is a tcp connect check")


def _probe_script(probe: dict[str, Any]) -> str:
    command = probe["spec"]["exec"]["command"]
    require(command[0] == "/bin/sh" and command[1] == "-c", f"probe command {command[:2]}")
    return command[2]


def assert_tdarr_probes() -> None:
    hr = load_docs(TDARR_HR)[0]
    node = hr["spec"]["values"]["controllers"]["tdarr-node"]["containers"]["app"]
    probes = node["probes"]
    liveness = probes["liveness"]["spec"]["exec"]["command"]
    require(liveness == ["/usr/bin/pgrep", "-x", "Tdarr_Node"], f"liveness {liveness}")
    ready = _probe_script(probes["readiness"])
    start = _probe_script(probes["startup"])
    require(ready == start, "startup and readiness must use the same heartbeat command")
    require("api/v2/get-nodes" in ready, "heartbeat must read the server node list")
    require("180000" in ready, "heartbeat ceiling must be 180000ms")
    require("process.env.nodeName" in ready, "heartbeat must use the pod's nodeName env")
    require("get-nodes" not in " ".join(liveness), "liveness must not call the server API")
    require(probes["readiness"]["spec"]["failureThreshold"] == 3, "readiness failureThreshold")
    require(probes["startup"]["spec"]["failureThreshold"] == 30, "startup failureThreshold")


def main() -> int:
    print("==> CNPG backup age and WAL archive")
    cnpg = prometheus_rule(CNPG_RULE)
    cnpg_alerts = alerts_by_name(cnpg)
    assert_cnpg_contract(cnpg_alerts)
    assert_cnpg_promtool(cnpg, cnpg_alerts)
    print("    OK promtool: primary age/lag fire, standbys and a demoted primary stay silent")

    print("==> kopiur policy vanished")
    kopiur = prometheus_rule(KOPIUR_RULE)
    kopiur_alerts = alerts_by_name(kopiur)
    assert_kopiur_contract(kopiur_alerts)
    assert_kopiur_promtool(kopiur, kopiur_alerts)
    print("    OK promtool: a removed policy fires, a stable count and a down exporter do not")

    print("==> gatus Echo, Kromgo, Tdarr Server")
    gatus = prometheus_rule(GATUS_RULE)
    assert_gatus_endpoints()
    assert_gatus_promtool(gatus, alerts_by_name(gatus))
    print("    OK promtool: Echo down is critical, Tdarr :8266 down is warning")

    print("==> tdarr-node probes")
    assert_tdarr_probes()
    print("    OK liveness is local, readiness is the server heartbeat")

    print("PASS: alert coverage gaps")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
