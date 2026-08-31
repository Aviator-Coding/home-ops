#!/usr/bin/env python3
"""Behavioral regression test for backup silent-failure alerting.

Pins the 2026-08-31 monitoring blind-spot close:

  1. VolSync ReplicationSources stuck Synchronizing=True/SyncInProgress are
     indistinguishable from a healthy mid-sync. Alert on "no completed sync in
     1.5x the destination schedule" via increase(volsync_sync_duration_seconds
     _count) == 0, joined to a live cache PVC so deleted-RS metric leaks
     (media/jellyfin-ceph) do not page forever.
  2. Kopiur SecurityContextCompatible is not exported to Prometheus; the chart's
     own Failed-phase alerts already cover uid-mismatch fallout. The remaining
     gap is a Succeeded snapshot that moved zero files (autobrr Stage 1 shape),
     covered by KopiurBackupEmpty on kopiur_policy_last_backup_files == 0.

This test does NOT grep source text as evidence. It:

  1. Loads the real PrometheusRule manifests Flux would apply.
  2. Feeds them to Prometheus' own rule unit-test engine (`promtool test rules`)
     with synthetic series that model the live stuck opencode sources, healthy
     fleet completions, the jellyfin leaked-series case, the recyclarr negative
     case, and an empty kopiur policy.
  3. Asserts observable alert fire/silence and PromQL sample sets.

Live cluster confirmation (port-forwarded Prometheus query against the real
opencode series) remains a post-merge / operator gate; this pins the rule
semantics so a refactor cannot silently re-open the blind spots.
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
VOLSYNC_RULE = (
    ROOT / "kubernetes/apps/base/system/volsync/app/prometheusrule.yaml"
)
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

# Destination schedule (nominal) -> stall window (1.5x), documented in the rule.
STALL_WINDOWS = {
    "VolSyncSyncStalledCeph": ("6h", "ceph", "4h"),
    "VolSyncSyncStalledMinio": ("9h", "minio", "6h"),
    "VolSyncSyncStalledR2": ("36h", "r2", "24h"),
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
    volsync = alerts_by_name(prometheus_rule(VOLSYNC_RULE))
    kopiur = alerts_by_name(prometheus_rule(KOPIUR_RULE))
    pvc = alerts_by_name(prometheus_rule(PVC_WRITABLE_RULE))

    # Existing backup / volume alerts route on severity only (no custom route key).
    for name, alert in {
        **{k: volsync[k] for k in (
            "VolSyncComponentAbsent",
            "VolSyncVolumeOutOfSync",
            "VolSyncSyncStalledCeph",
            "VolSyncSyncStalledMinio",
            "VolSyncSyncStalledR2",
        )},
        **{k: kopiur[k] for k in (
            "KopiurComponentAbsent",
            "KopiurBackupEmpty",
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
            "pvc-writable-check / existing volsync+kopiur rules (severity only)",
        )

    require(
        volsync["VolSyncSyncStalledCeph"]["labels"]["severity"] == "critical",
        "stalled VolSync syncs are critical (backup not running)",
    )
    require(
        kopiur["KopiurBackupEmpty"]["labels"]["severity"] == "warning",
        "empty kopiur backup is warning (succeeded but contentless)",
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
        "volsync_stalled": [
            "VolSyncSyncStalledCeph",
            "VolSyncSyncStalledMinio",
            "VolSyncSyncStalledR2",
        ],
        "kopiur_empty": "KopiurBackupEmpty",
        "severity_shape": "severity-only",
    }


def assert_window_contract(volsync_alerts: dict[str, dict[str, Any]]) -> None:
    """Windows are 1.5x destination schedules, selected by obj_name suffix."""
    for alert_name, (window, suffix, _nominal) in STALL_WINDOWS.items():
        expr = volsync_alerts[alert_name].get("expr") or ""
        require(
            f"[{window}]" in expr,
            f"{alert_name}: expected range window [{window}] in expr, got {expr!r}",
        )
        require(
            f".+-{suffix}$" in expr or f".+-{suffix}" in expr,
            f"{alert_name}: expected obj_name suffix filter for -{suffix}",
        )
        require(
            'role="source"' in expr,
            f"{alert_name}: must filter role=source",
        )
        require(
            "kube_persistentvolumeclaim_info" in expr,
            f"{alert_name}: must join live cache PVC to drop leaked series",
        )
        require(
            "volsync-src-$1-cache" in expr or "volsync-src-" in expr,
            f"{alert_name}: cache PVC join key must be volsync-src-<obj_name>-cache",
        )
        require(
            volsync_alerts[alert_name].get("for") == "5m",
            f"{alert_name}: for: must be 5m to match sibling VolSync alerts",
        )


def assert_promtool_semantics() -> dict[str, Any]:
    volsync_rule = prometheus_rule(VOLSYNC_RULE)
    kopiur_rule = prometheus_rule(KOPIUR_RULE)
    volsync_alerts = alerts_by_name(volsync_rule)
    kopiur_alerts = alerts_by_name(kopiur_rule)

    for name in STALL_WINDOWS:
        require(name in volsync_alerts, f"missing alert {name}")
    require(
        "KopiurBackupEmpty" in kopiur_alerts, "missing alert KopiurBackupEmpty"
    )

    assert_window_contract(volsync_alerts)

    ceph_expr = volsync_alerts["VolSyncSyncStalledCeph"]["expr"]
    minio_expr = volsync_alerts["VolSyncSyncStalledMinio"]["expr"]
    r2_expr = volsync_alerts["VolSyncSyncStalledR2"]["expr"]
    empty_expr = kopiur_alerts["KopiurBackupEmpty"]["expr"]
    empty_summary = (kopiur_alerts["KopiurBackupEmpty"].get("annotations") or {}).get(
        "summary", ""
    )

    # Expanded annotation text promtool will produce for the empty-backup case.
    expanded_empty_summary = empty_summary.replace(
        "{{ $labels.namespace }}", "downloads"
    ).replace("{{ $labels.policy }}", "autobrr-ceph")

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
        _write_rule_file(work / "volsync_rules.yml", volsync_rule)
        _write_rule_file(work / "kopiur_rules.yml", kopiur_rule)

        check_vs = _run_promtool(["check", "rules", "volsync_rules.yml"], work)
        check_kp = _run_promtool(["check", "rules", "kopiur_rules.yml"], work)

        # Separate unit-test files: VolSync needs multi-hour range windows with a
        # 1h series interval; KopiurBackupEmpty's for:15m needs 1m evaluation.
        volsync_test = {
            "rule_files": ["volsync_rules.yml"],
            "evaluation_interval": "1h",
            "tests": [
                {
                    "name": "volsync_stalled_fire_and_silence",
                    "interval": "1h",
                    "input_series": [
                        # Blind spot 1 subject: ai/opencode-ceph stuck, cache PVC live
                        {
                            "series": (
                                'volsync_sync_duration_seconds_count{role="source",'
                                'obj_name="opencode-ceph",obj_namespace="ai",'
                                'job="volsync-metrics"}'
                            ),
                            "values": _const_values(7, hours),
                        },
                        {
                            "series": (
                                'kube_persistentvolumeclaim_info{namespace="ai",'
                                'persistentvolumeclaim="volsync-src-opencode-ceph-cache"}'
                            ),
                            "values": _const_values(1, hours),
                        },
                        # ai/opencode-minio stuck
                        {
                            "series": (
                                'volsync_sync_duration_seconds_count{role="source",'
                                'obj_name="opencode-minio",obj_namespace="ai",'
                                'job="volsync-metrics"}'
                            ),
                            "values": _const_values(3, hours),
                        },
                        {
                            "series": (
                                'kube_persistentvolumeclaim_info{namespace="ai",'
                                'persistentvolumeclaim="volsync-src-opencode-minio-cache"}'
                            ),
                            "values": _const_values(1, hours),
                        },
                        # ai/opencode-r2 still completing on daily cadence
                        {
                            "series": (
                                'volsync_sync_duration_seconds_count{role="source",'
                                'obj_name="opencode-r2",obj_namespace="ai",'
                                'job="volsync-metrics"}'
                            ),
                            "values": _step_every(24, hours, start=1),
                        },
                        {
                            "series": (
                                'kube_persistentvolumeclaim_info{namespace="ai",'
                                'persistentvolumeclaim="volsync-src-opencode-r2-cache"}'
                            ),
                            "values": _const_values(1, hours),
                        },
                        # Healthy fleet samples (must stay silent)
                        {
                            "series": (
                                'volsync_sync_duration_seconds_count{role="source",'
                                'obj_name="sabnzbd-ceph",obj_namespace="downloads",'
                                'job="volsync-metrics"}'
                            ),
                            "values": _step_every(4, hours, start=10),
                        },
                        {
                            "series": (
                                'kube_persistentvolumeclaim_info{namespace="downloads",'
                                'persistentvolumeclaim="volsync-src-sabnzbd-ceph-cache"}'
                            ),
                            "values": _const_values(1, hours),
                        },
                        {
                            "series": (
                                'volsync_sync_duration_seconds_count{role="source",'
                                'obj_name="sabnzbd-minio",obj_namespace="downloads",'
                                'job="volsync-metrics"}'
                            ),
                            "values": _step_every(6, hours, start=5),
                        },
                        {
                            "series": (
                                'kube_persistentvolumeclaim_info{namespace="downloads",'
                                'persistentvolumeclaim="volsync-src-sabnzbd-minio-cache"}'
                            ),
                            "values": _const_values(1, hours),
                        },
                        {
                            "series": (
                                'volsync_sync_duration_seconds_count{role="source",'
                                'obj_name="hermes-ceph",obj_namespace="ai",'
                                'job="volsync-metrics"}'
                            ),
                            "values": _step_every(4, hours, start=20),
                        },
                        {
                            "series": (
                                'kube_persistentvolumeclaim_info{namespace="ai",'
                                'persistentvolumeclaim="volsync-src-hermes-ceph-cache"}'
                            ),
                            "values": _const_values(1, hours),
                        },
                        # Explicit negative: downloads/recyclarr (healthy daily)
                        {
                            "series": (
                                'volsync_sync_duration_seconds_count{role="source",'
                                'obj_name="recyclarr-r2",obj_namespace="downloads",'
                                'job="volsync-metrics"}'
                            ),
                            "values": _step_every(24, hours, start=1),
                        },
                        {
                            "series": (
                                'kube_persistentvolumeclaim_info{namespace="downloads",'
                                'persistentvolumeclaim="volsync-src-recyclarr-r2-cache"}'
                            ),
                            "values": _const_values(1, hours),
                        },
                        # Leaked series for deleted media/jellyfin-ceph (no cache PVC)
                        {
                            "series": (
                                'volsync_sync_duration_seconds_count{role="source",'
                                'obj_name="jellyfin-ceph",obj_namespace="media",'
                                'job="volsync-metrics"}'
                            ),
                            "values": _const_values(9, hours),
                        },
                    ],
                    "promql_expr_test": [
                        {
                            "expr": ceph_expr,
                            "eval_time": "40h",
                            "exp_samples": [
                                {
                                    "labels": (
                                        '{cachepvc="volsync-src-opencode-ceph-cache", '
                                        'job="volsync-metrics", '
                                        'obj_name="opencode-ceph", '
                                        'obj_namespace="ai", role="source"}'
                                    ),
                                    "value": 0,
                                }
                            ],
                        },
                        {
                            "expr": minio_expr,
                            "eval_time": "40h",
                            "exp_samples": [
                                {
                                    "labels": (
                                        '{cachepvc="volsync-src-opencode-minio-cache", '
                                        'job="volsync-metrics", '
                                        'obj_name="opencode-minio", '
                                        'obj_namespace="ai", role="source"}'
                                    ),
                                    "value": 0,
                                }
                            ],
                        },
                        {
                            "expr": r2_expr,
                            "eval_time": "40h",
                            "exp_samples": [],
                        },
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": "40h",
                            "alertname": "VolSyncSyncStalledCeph",
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": "VolSyncSyncStalledCeph",
                                        "severity": "critical",
                                        "cachepvc": "volsync-src-opencode-ceph-cache",
                                        "job": "volsync-metrics",
                                        "obj_name": "opencode-ceph",
                                        "obj_namespace": "ai",
                                        "role": "source",
                                    },
                                    "exp_annotations": {
                                        "summary": (
                                            "ai/opencode-ceph has had no completed VolSync "
                                            "sync in over 6h (1.5x its 4h ceph schedule). "
                                            "Its ReplicationSource may still read "
                                            "Synchronizing=True the whole time, which looks "
                                            "identical to a healthy mid-sync. Check "
                                            "`kubectl -n ai describe replicationsource "
                                            "opencode-ceph` for a wedged mover pod."
                                        ),
                                    },
                                }
                            ],
                        },
                        {
                            "eval_time": "40h",
                            "alertname": "VolSyncSyncStalledMinio",
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": "VolSyncSyncStalledMinio",
                                        "severity": "critical",
                                        "cachepvc": "volsync-src-opencode-minio-cache",
                                        "job": "volsync-metrics",
                                        "obj_name": "opencode-minio",
                                        "obj_namespace": "ai",
                                        "role": "source",
                                    },
                                    "exp_annotations": {
                                        "summary": (
                                            "ai/opencode-minio has had no completed VolSync "
                                            "sync in over 9h (1.5x its 6h minio schedule). "
                                            "Check `kubectl -n ai describe replicationsource "
                                            "opencode-minio` for a wedged mover pod."
                                        ),
                                    },
                                }
                            ],
                        },
                        {
                            "eval_time": "40h",
                            "alertname": "VolSyncSyncStalledR2",
                            "exp_alerts": [],
                        },
                    ],
                }
            ],
        }

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

        _quote_values(volsync_test)
        _quote_values(kopiur_test)

        vs_path = work / "volsync_alerts_test.yml"
        kp_path = work / "kopiur_alerts_test.yml"
        vs_path.write_text(yaml.dump(volsync_test, sort_keys=False, width=1000))
        kp_path.write_text(yaml.dump(kopiur_test, sort_keys=False, width=1000))
        vs_out = _run_promtool(["test", "rules", vs_path.name], work)
        kp_out = _run_promtool(["test", "rules", kp_path.name], work)

    return {
        "check_volsync": check_vs.splitlines()[-1] if check_vs else "SUCCESS",
        "check_kopiur": check_kp.splitlines()[-1] if check_kp else "SUCCESS",
        "test_rules": "PASS",
        "volsync_test_out_tail": vs_out[-200:],
        "kopiur_test_out_tail": kp_out[-200:],
    }


def main() -> int:
    print("==> routing parity with existing backup alerts")
    routing = assert_routing_parity()
    print(f"    OK severity_shape={routing['severity_shape']}")

    print("==> promtool check + unit-test rule semantics")
    semantics = assert_promtool_semantics()
    print(
        f"    OK check_volsync={semantics['check_volsync']!r} "
        f"check_kopiur={semantics['check_kopiur']!r} "
        f"test_rules={semantics['test_rules']}"
    )

    print("PASS: backup silent-failure alerting contracts hold")
    print("covered:")
    print("  - VolSyncSyncStalledCeph fires only for stuck ai/opencode-ceph")
    print("  - VolSyncSyncStalledMinio fires only for stuck ai/opencode-minio")
    print("  - VolSyncSyncStalledR2 silent for healthy daily completions")
    print("  - leaked media/jellyfin-ceph series excluded by cache-PVC join")
    print("  - downloads/recyclarr healthy series does not fire")
    print("  - KopiurBackupEmpty fires only for zero-file policy after for:15m")
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
