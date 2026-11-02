#!/usr/bin/env python3
# ci: gate
"""Behavioral regression test for backup silent-failure alerting.

Prevents: empty or stale kopiur backups never raising an alert, and a VolSync rule
outliving the operator (its `absent()` alert would fire forever).

Pins the 2026-08-31 monitoring blind-spot close:

  1. VolSync's stall rules (VolSyncSyncStalledCeph/Minio/R2, 2026-08-31) and the
     rest of its PrometheusRule were removed with the operator (issue #2202 PR-6).
     assert_volsync_rules_removed() keeps them out: VolSyncComponentAbsent is an
     absent(up{job="volsync-metrics"}) rule, so it would page forever once the
     operator is gone. Git history holds the stall-window promtool matrix.
  2. Kopiur SecurityContextCompatible is not exported to Prometheus; the chart's
     own Failed-phase alerts already cover uid-mismatch fallout. The remaining
     gap is a Succeeded snapshot that moved zero files (autobrr Stage 1 shape),
     covered by KopiurBackupEmpty on kopiur_policy_last_backup_files == 0.
  3. KopiurBackupStaleCeph / KopiurBackupStaleR2 split the chart's single 48h
     warning (12 missed ceph runs) the way the old VolSync stall rules split
     theirs: 6h critical for policy=~".+-ceph$", 48h warning for
     policy=~".+-r2$". The live metric has no `repository` label.

This test does NOT grep source text as evidence. It:

  1. Loads the real PrometheusRule manifests Flux would apply.
  2. Feeds them to Prometheus' own rule unit-test engine (`promtool test rules`)
     with synthetic series that model an empty kopiur policy and stale and
     fresh ceph/r2 policies.
  3. Asserts observable alert fire/silence and PromQL sample sets.

Live cluster confirmation was already completed against the real
kube-prometheus-stack Prometheus (port-forwarded) and is recorded in the
PrometheusRule comments next to each alert. Fresh worktrees never carry
kubeconfig (AGENTS.md); this CI gate therefore pins the same fire/silence matrix
with promtool rather than re-querying the cluster. Recorded live results:

  KopiurBackupEmpty
  - Silent fleet-wide (every completed policy reports >0 files).
  - Historical empty-volume Snapshot downloads/autobrr-ceph-stage1-verify has
    status.stats = {"filesNew":0,"sizeBytes":0} (explicit zero, not omitted).
  - kopiur_snapshotpolicy_last_backup_success and
    kopiur_policy_last_backup_files share the exact same (namespace,policy)
    set (27==27, zero set difference).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
# Removed with the operator (issue #2202 PR-6); must stay absent.
VOLSYNC_RULE = (
    ROOT / "kubernetes/apps/base/system/volsync/app/prometheusrule.yaml"
)
RULE_ROOT = ROOT / "kubernetes"
KOPIUR_RULE = (
    ROOT / "kubernetes/apps/base/system/kopiur/app/prometheusrule.yaml"
)
PVC_WRITABLE_RULE = (
    ROOT
    / "kubernetes/apps/base/system/pvc-writable-check/app/prometheusrule.yaml"
)

PROMTOOL_IMAGE = os.environ.get(
    "PROMTOOL_IMAGE", "quay.io/prometheus/prometheus:v3.2.1"
)

# Kopiur dest-split staleness. Seconds in the expr, cadence for the comment.
# Selector is the policy suffix: the live metric has no `repository` label.
KOPIUR_STALE = {
    "KopiurBackupStaleCeph": {
        "seconds": "21600",
        "suffix": "ceph",
        "severity": "critical",
    },
    "KopiurBackupStaleR2": {
        "seconds": "172800",
        "suffix": "r2",
        "severity": "warning",
    },
}


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_docs(path: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    with path.open() as f:
        for doc in yaml.safe_load_all(f):
            if isinstance(doc, dict):
                docs.append(doc)
    return docs


def prometheus_rule(path: Path) -> dict[str, Any]:
    docs = load_docs(path)
    rules = [d for d in docs if d.get("kind") == "PrometheusRule"]
    require(len(rules) == 1, f"{path}: expected 1 PrometheusRule, got {len(rules)}")
    return rules[0]


def alerts_by_name(rule: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for group in rule.get("spec", {}).get("groups", []) or []:
        for r in group.get("rules", []) or []:
            if "alert" in r:
                out[r["alert"]] = r
    return out


def _resolve_promtool() -> str | None:
    """Return a runnable promtool binary path, or None.

    CI puts the aqua/mise install on PATH via mise-action. Local sandboxes often
    only have the bare installer under ~/.local/share/mise/installs and a mise
    shim that is not on PATH (or is an untrusted shim). Prefer a real binary
    over podman: `podman --version` can succeed while `podman run` cannot talk
    to a machine, which is a common local failure mode.
    """
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
        # Skip bare mise shims that re-exec mise (need trusted config / PATH).
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
    """True only if podman can actually run a container, not just --version."""
    if shutil.which("podman") is None:
        return False
    probe = subprocess.run(
        ["podman", "info", "--format", "{{.Host.RemoteSocket.Exists}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return False
    return True


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
            f"promtool {' '.join(args)} failed (exit {proc.returncode}):\n"
            f"{out.strip()}"
        )
    return out.strip() or "SUCCESS"


def _const_values(value: int | float | str, n: int) -> str:
    """Space-separated sample list; never use NxM form (YAML 0x30 == hex)."""
    return " ".join([str(value)] * n)


def _step_every(period_h: int, hours: int, start: int = 1) -> str:
    vals: list[str] = []
    cur = start
    for h in range(hours):
        vals.append(str(cur))
        if (h + 1) % period_h == 0:
            cur += 1
    return " ".join(vals)


def _write_rule_file(path: Path, rule: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(
            {"groups": rule["spec"]["groups"]},
            sort_keys=False,
        )
    )


def assert_routing_parity() -> dict[str, Any]:
    """Alerts must carry the same severity-label routing shape as existing backup rules."""
    kopiur = alerts_by_name(prometheus_rule(KOPIUR_RULE))
    pvc = alerts_by_name(prometheus_rule(PVC_WRITABLE_RULE))

    # Existing backup / volume alerts route on severity only (no custom route key).
    for name, alert in {
        **{k: kopiur[k] for k in (
            "KopiurComponentAbsent",
            "KopiurBackupEmpty",
            "KopiurBackupStaleCeph",
            "KopiurBackupStaleR2",
        )},
        **pvc,
    }.items():
        labels = alert.get("labels") or {}
        require(
            "severity" in labels,
            f"{name}: missing severity label (required for existing Alertmanager route)",
        )
        require(
            set(labels.keys()) <= {"severity"},
            f"{name}: unexpected alert labels {labels} - keep parity with "
            "pvc-writable-check / existing kopiur rules (severity only)",
        )

    require(
        kopiur["KopiurBackupEmpty"]["labels"]["severity"] == "warning",
        "empty kopiur backup is warning (succeeded but contentless)",
    )
    require(
        kopiur["KopiurBackupStaleCeph"]["labels"]["severity"] == "critical",
        "stale kopiur ceph backups are critical (sole engine, 4h cadence)",
    )
    require(
        kopiur["KopiurBackupStaleR2"]["labels"]["severity"] == "warning",
        "stale kopiur r2 backups stay warning (daily cadence, 48h = 2 missed)",
    )

    # Summary must not insert a space inside namespace/name from folded YAML.
    empty_summary = (kopiur["KopiurBackupEmpty"].get("annotations") or {}).get(
        "summary", ""
    )
    require(
        "{{ $labels.namespace }}/{{ $labels.policy }}" in empty_summary,
        "KopiurBackupEmpty summary must keep namespace/policy adjacent "
        f"(no folded-scalar space); got {empty_summary!r}",
    )
    require(
        "/ {{ $labels.policy }}" not in empty_summary,
        "KopiurBackupEmpty summary has a space after '/' from YAML folding",
    )

    return {
        "kopiur_empty": "KopiurBackupEmpty",
        "severity_shape": "severity-only",
    }


def assert_volsync_rules_removed() -> None:
    """No PrometheusRule outlives the VolSync operator (issue #2202 PR-6).

    VolSyncComponentAbsent was `absent(up{job="volsync-metrics"})`: with the
    operator gone it would fire forever, and the stall and out-of-sync rules
    would read metrics nothing exports. Every rendered rule file in the repo is
    scanned, not just the old path, so a copy under another app also fails.
    """
    require(not VOLSYNC_RULE.exists(), f"{VOLSYNC_RULE.relative_to(ROOT)} is back")
    offenders: list[str] = []
    for path in sorted(RULE_ROOT.rglob("*.yaml")):
        text = path.read_text()
        if "PrometheusRule" not in text:
            continue
        for doc in load_docs(path):
            if doc.get("kind") != "PrometheusRule":
                continue
            for name, alert in alerts_by_name(doc).items():
                expr = str(alert.get("expr") or "")
                if name.startswith("VolSync") or "volsync" in expr.lower():
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
    require(not offenders, f"VolSync alert rules outlive the operator: {offenders}")


def assert_kopiur_stale_contract(kopiur_alerts: dict[str, dict[str, Any]]) -> None:
    """Ceph 6h critical / r2 48h warning, selected by policy suffix."""
    for alert_name, spec in KOPIUR_STALE.items():
        require(alert_name in kopiur_alerts, f"missing alert {alert_name}")
        expr = kopiur_alerts[alert_name].get("expr") or ""
        require(
            f"> {spec['seconds']}" in expr,
            f"{alert_name}: expected threshold > {spec['seconds']} in expr, got {expr!r}",
        )
        require(
            f'policy=~".+-{spec["suffix"]}$"' in expr,
            f"{alert_name}: expected policy suffix filter for -{spec['suffix']}",
        )
        require(
            "kopiur_policy_last_backup_success_timestamp_seconds" in expr,
            f"{alert_name}: must read last-success timestamp",
        )
        require(
            'repository="' not in expr,
            f"{alert_name}: live metric has no repository label; "
            "select on policy suffix, not repository=",
        )
        require(
            kopiur_alerts[alert_name].get("for") == "5m",
            f"{alert_name}: for: must be 5m to match sibling backup alerts",
        )
        require(
            kopiur_alerts[alert_name]["labels"]["severity"] == spec["severity"],
            f"{alert_name}: severity must be {spec['severity']}",
        )
        summary = (kopiur_alerts[alert_name].get("annotations") or {}).get(
            "summary", ""
        )
        require(
            "{{ $labels.namespace }}/{{ $labels.policy }}" in summary,
            f"{alert_name} summary must keep namespace/policy adjacent "
            f"(no folded-scalar space); got {summary!r}",
        )


def assert_promtool_semantics() -> dict[str, Any]:
    kopiur_rule = prometheus_rule(KOPIUR_RULE)
    kopiur_alerts = alerts_by_name(kopiur_rule)

    require(
        "KopiurBackupEmpty" in kopiur_alerts, "missing alert KopiurBackupEmpty"
    )
    for name in KOPIUR_STALE:
        require(name in kopiur_alerts, f"missing alert {name}")

    assert_kopiur_stale_contract(kopiur_alerts)

    empty_expr = kopiur_alerts["KopiurBackupEmpty"]["expr"]
    empty_summary = (kopiur_alerts["KopiurBackupEmpty"].get("annotations") or {}).get(
        "summary", ""
    )
    stale_ceph_expr = kopiur_alerts["KopiurBackupStaleCeph"]["expr"]
    stale_r2_expr = kopiur_alerts["KopiurBackupStaleR2"]["expr"]
    stale_ceph_summary = (
        kopiur_alerts["KopiurBackupStaleCeph"].get("annotations") or {}
    ).get("summary", "")
    stale_r2_summary = (
        kopiur_alerts["KopiurBackupStaleR2"].get("annotations") or {}
    ).get("summary", "")

    # Expanded annotation text promtool will produce for the empty-backup case.
    expanded_empty_summary = empty_summary.replace(
        "{{ $labels.namespace }}", "downloads"
    ).replace("{{ $labels.policy }}", "autobrr-ceph")
    expanded_stale_ceph_summary = stale_ceph_summary.replace(
        "{{ $labels.namespace }}", "downloads"
    ).replace("{{ $labels.policy }}", "sabnzbd-ceph")
    expanded_stale_r2_summary = stale_r2_summary.replace(
        "{{ $labels.namespace }}", "downloads"
    ).replace("{{ $labels.policy }}", "sabnzbd-r2")

    hours = 48

    # Force quoted values so YAML never treats 0x... as hex integers.
    class _Q(str):
        pass

    def _represent_q(dumper: yaml.Dumper, data: str) -> Any:
        return dumper.represent_scalar(
            "tag:yaml.org,2002:str", data, style='"'
        )

    yaml.add_representer(_Q, _represent_q)

    def _quote_values(doc: dict[str, Any]) -> None:
        for t in doc["tests"]:
            for s in t["input_series"]:
                s["values"] = _Q(s["values"])

    with tempfile.TemporaryDirectory(prefix="backup-alert-promtool-") as tmp:
        work = Path(tmp)
        _write_rule_file(work / "kopiur_rules.yml", kopiur_rule)

        check_kp = _run_promtool(["check", "rules", "kopiur_rules.yml"], work)

        # KopiurBackupEmpty's for:15m needs 1m evaluation.
        kopiur_test = {
            "rule_files": ["kopiur_rules.yml"],
            "evaluation_interval": "1m",
            "tests": [
                {
                    "name": "kopiur_empty_backup_fire_and_silence",
                    "interval": "1m",
                    "input_series": [
                        {
                            "series": (
                                'kopiur_policy_last_backup_files{namespace="downloads",'
                                'policy="autobrr-ceph"}'
                            ),
                            "values": _const_values(0, 40),
                        },
                        {
                            "series": (
                                'kopiur_policy_last_backup_files{namespace="downloads",'
                                'policy="sabnzbd-ceph"}'
                            ),
                            "values": _const_values(2062, 40),
                        },
                        {
                            "series": (
                                'kopiur_policy_last_backup_files{namespace="media",'
                                'policy="plex"}'
                            ),
                            "values": _const_values(100, 40),
                        },
                    ],
                    "promql_expr_test": [
                        {
                            "expr": empty_expr,
                            "eval_time": "20m",
                            "exp_samples": [
                                {
                                    "labels": (
                                        '{__name__="kopiur_policy_last_backup_files", '
                                        'namespace="downloads", policy="autobrr-ceph"}'
                                    ),
                                    "value": 0,
                                }
                            ],
                        }
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": "10m",
                            "alertname": "KopiurBackupEmpty",
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": "16m",
                            "alertname": "KopiurBackupEmpty",
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": "KopiurBackupEmpty",
                                        "severity": "warning",
                                        "namespace": "downloads",
                                        "policy": "autobrr-ceph",
                                    },
                                    "exp_annotations": {
                                        "summary": expanded_empty_summary,
                                    },
                                }
                            ],
                        },
                    ],
                }
            ],
        }

        stale_hours = 60
        stale_test = {
            "rule_files": ["kopiur_rules.yml"],
            "evaluation_interval": "1h",
            "tests": [
                {
                    "name": "kopiur_stale_ceph_and_r2_split",
                    "interval": "1h",
                    "input_series": [
                        # Stale ceph: timestamp frozen at 0. Age == eval time.
                        {
                            "series": (
                                'kopiur_policy_last_backup_success_timestamp_seconds{'
                                'namespace="downloads",policy="sabnzbd-ceph"}'
                            ),
                            "values": _const_values(0, stale_hours),
                        },
                        # Fresh ceph: timestamp tracks wall time, age ~0.
                        {
                            "series": (
                                'kopiur_policy_last_backup_success_timestamp_seconds{'
                                'namespace="media",policy="plex-ceph"}'
                            ),
                            "values": " ".join(
                                str(i * 3600) for i in range(stale_hours)
                            ),
                        },
                        # Stale r2: same frozen timestamp. Must NOT fire at
                        # 8h (ceph window) and MUST fire after 48h.
                        {
                            "series": (
                                'kopiur_policy_last_backup_success_timestamp_seconds{'
                                'namespace="downloads",policy="sabnzbd-r2"}'
                            ),
                            "values": _const_values(0, stale_hours),
                        },
                        # Fresh r2: must stay silent past 48h.
                        {
                            "series": (
                                'kopiur_policy_last_backup_success_timestamp_seconds{'
                                'namespace="media",policy="plex-r2"}'
                            ),
                            "values": " ".join(
                                str(i * 3600) for i in range(stale_hours)
                            ),
                        },
                    ],
                    "promql_expr_test": [
                        {
                            "expr": stale_ceph_expr,
                            "eval_time": "8h",
                            "exp_samples": [
                                {
                                    "labels": (
                                        '{namespace="downloads", policy="sabnzbd-ceph"}'
                                    ),
                                    "value": 28800,
                                }
                            ],
                        },
                        {
                            "expr": stale_r2_expr,
                            "eval_time": "8h",
                            "exp_samples": [],
                        },
                        {
                            "expr": stale_r2_expr,
                            "eval_time": "50h",
                            "exp_samples": [
                                {
                                    "labels": (
                                        '{namespace="downloads", policy="sabnzbd-r2"}'
                                    ),
                                    "value": 180000,
                                }
                            ],
                        },
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": "5h",
                            "alertname": "KopiurBackupStaleCeph",
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": "8h",
                            "alertname": "KopiurBackupStaleCeph",
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": "KopiurBackupStaleCeph",
                                        "severity": "critical",
                                        "namespace": "downloads",
                                        "policy": "sabnzbd-ceph",
                                    },
                                    "exp_annotations": {
                                        "summary": expanded_stale_ceph_summary,
                                    },
                                }
                            ],
                        },
                        {
                            "eval_time": "8h",
                            "alertname": "KopiurBackupStaleR2",
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": "40h",
                            "alertname": "KopiurBackupStaleR2",
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": "50h",
                            "alertname": "KopiurBackupStaleR2",
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": "KopiurBackupStaleR2",
                                        "severity": "warning",
                                        "namespace": "downloads",
                                        "policy": "sabnzbd-r2",
                                    },
                                    "exp_annotations": {
                                        "summary": expanded_stale_r2_summary,
                                    },
                                }
                            ],
                        },
                    ],
                }
            ],
        }

        _quote_values(kopiur_test)
        _quote_values(stale_test)

        kp_path = work / "kopiur_alerts_test.yml"
        stale_path = work / "kopiur_stale_test.yml"
        kp_path.write_text(yaml.dump(kopiur_test, sort_keys=False, width=1000))
        stale_path.write_text(yaml.dump(stale_test, sort_keys=False, width=1000))
        kp_out = _run_promtool(["test", "rules", kp_path.name], work)
        stale_out = _run_promtool(["test", "rules", stale_path.name], work)

    return {
        "check_kopiur": check_kp.splitlines()[-1] if check_kp else "SUCCESS",
        "test_rules": "PASS",
        "kopiur_test_out_tail": kp_out[-200:],
        "stale_test_out_tail": stale_out[-200:],
    }


def main() -> int:
    print("==> routing parity with existing backup alerts")
    routing = assert_routing_parity()
    print(f"    OK severity_shape={routing['severity_shape']}")

    print("==> no VolSync alert rule outlives the operator")
    assert_volsync_rules_removed()
    print("    OK")

    print("==> promtool check + unit-test rule semantics")
    semantics = assert_promtool_semantics()
    print(
        f"    OK check_kopiur={semantics['check_kopiur']!r} "
        f"test_rules={semantics['test_rules']}"
    )

    print("PASS: backup silent-failure alerting contracts hold")
    print("covered:")
    print("  - no VolSync alert rule outlives the operator (issue #2202 PR-6)")
    print("  - KopiurBackupEmpty fires only for zero-file policy after for:15m")
    print("  - KopiurBackupStaleCeph fires at 6h critical; r2 stays silent")
    print("  - KopiurBackupStaleR2 fires at 48h warning; fresh policies silent")
    print("  - severity-only labels match pvc-writable-check / existing rules")
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
