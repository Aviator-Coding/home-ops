#!/usr/bin/env python3
"""Behavioral regression for KopiurProjectedCredentialsLeaking.

Pins the 2026-08-31 false-fire on `kopiur_projected_secrets_live`:

  The gauge is a leader-only periodic census written once per
  KOPIUR_WORK_SPEC_SWEEP_INTERVAL_SECS (default 6h). A single List() that
  catches legitimate in-flight projected Secrets freezes a nonzero reading
  until the next sweep, even after every Secret is reaped. The old rule
  (`kopiur_projected_secrets_live > 0` / `for: 1h`) therefore pages for hours
  on ordinary backup concurrency.

  The fixed rule requires the population to stay positive across at least two
  sweep passes:

      min_over_time(kopiur_projected_secrets_live[13h]) > 0
      for: 5m

This test does NOT grep source text as evidence. It:

  1. Loads the real PrometheusRule Flux would apply.
  2. Feeds it to Prometheus' own rule unit-test engine (`promtool test rules`)
     with synthetic series that model:
       - the live 6h plateau incident (benign mid-flight census of 4)
       - a genuine one-shot permanent leak (stays elevated across 2+ sweeps)
       - a healthy always-zero fleet
  3. Asserts observable alert fire/silence and PromQL sample sets for both the
     fixed expression and the old bare level expression (proving the regression
     shape the fix closes).

promtool is resolved the same way as backup-silent-failure-alerting-test.py
(native aqua install preferred; podman image fallback).
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
KOPIUR_RULE = (
    ROOT / "kubernetes/apps/base/system/kopiur/app/prometheusrule.yaml"
)

PROMTOOL_IMAGE = os.environ.get(
    "PROMTOOL_IMAGE", "quay.io/prometheus/prometheus:v3.2.1"
)

# Operator default sweep interval and the alert's multi-pass lookback.
SWEEP_INTERVAL_H = 6
LOOKBACK_H = 13
# Synthetic timeline long enough for two full sweeps + for: window + slack.
HOURS = 48

ALERT_NAME = "KopiurProjectedCredentialsLeaking"
# Reproduced bare level expr that false-fired on the 2026-08-31 plateau.
OLD_LEVEL_EXPR = "kopiur_projected_secrets_live > 0"


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
    on_path = shutil.which("promtool")
    candidates: list[Path] = []
    if on_path:
        candidates.append(Path(on_path))

    mise_root = Path.home() / ".local/share/mise/installs/aqua-prometheus-prometheus"
    if mise_root.is_dir():
        candidates.extend(
            sorted(mise_root.glob("*/prometheus-*/promtool"), reverse=True)
        )

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


def _write_rule_file(path: Path, rule: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(
            {"groups": rule["spec"]["groups"]},
            sort_keys=False,
        )
    )


def _series(values: list[int | float]) -> str:
    """Space-separated samples; never NxM form (YAML 0x30 == hex)."""
    return " ".join(str(v) for v in values)


def _plateau_values(
    hours: int,
    *,
    start_h: int,
    duration_h: int,
    height: int,
) -> list[int]:
    """Zeros with a single frozen nonzero plateau (one sweep census catch)."""
    out: list[int] = []
    for h in range(hours):
        if start_h <= h < start_h + duration_h:
            out.append(height)
        else:
            out.append(0)
    return out


def _permanent_leak_values(hours: int, *, start_h: int, height: int = 1) -> list[int]:
    """Zeros then a permanent step that never returns to zero."""
    return [height if h >= start_h else 0 for h in range(hours)]


def assert_rule_contract(alert: dict[str, Any]) -> dict[str, Any]:
    """Structural contract the expression/for/severity must keep."""
    expr = (alert.get("expr") or "").strip()
    require(
        "min_over_time" in expr and "kopiur_projected_secrets_live" in expr,
        f"{ALERT_NAME}: expr must use min_over_time over kopiur_projected_secrets_live; "
        f"got {expr!r}",
    )
    require(
        f"[{LOOKBACK_H}h]" in expr,
        f"{ALERT_NAME}: lookback must be [{LOOKBACK_H}h] "
        f"(>2x the {SWEEP_INTERVAL_H}h default sweep); got {expr!r}",
    )
    compact = "".join(expr.split())
    require(
        compact != "kopiur_projected_secrets_live>0",
        f"{ALERT_NAME}: bare level expr must not return; got {expr!r}",
    )
    require(
        compact.startswith("min_over_time("),
        f"{ALERT_NAME}: expr must be a min_over_time(...) persistence check; got {expr!r}",
    )
    require(
        alert.get("for") == "5m",
        f"{ALERT_NAME}: for: must be 5m (pending hold after multi-pass expression is true); "
        f"got {alert.get('for')!r}",
    )
    labels = alert.get("labels") or {}
    require(
        labels.get("severity") == "critical",
        f"{ALERT_NAME}: severity must stay critical (credential leak); got {labels!r}",
    )
    # 13h lookback is the multi-pass gate; it must exceed two default sweeps.
    require(
        LOOKBACK_H > 2 * SWEEP_INTERVAL_H,
        f"lookback {LOOKBACK_H}h must be > 2x sweep {SWEEP_INTERVAL_H}h",
    )
    return {
        "expr": expr,
        "for": alert.get("for"),
        "severity": labels.get("severity"),
        "lookback_h": LOOKBACK_H,
        "sweep_h": SWEEP_INTERVAL_H,
    }


def assert_promtool_semantics(alert: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    fixed_expr = (alert.get("expr") or "").strip()
    summary = (alert.get("annotations") or {}).get("summary", "")

    # Incident shape: 0 -> 4 at one sweep, held 6h, then 4 -> 0 at the next.
    # Matches the live Prometheus series from 2026-08-31 (09:29 -> 15:29).
    plateau_start = 8
    plateau_vals = _plateau_values(
        HOURS, start_h=plateau_start, duration_h=SWEEP_INTERVAL_H, height=4
    )
    # Genuine leak: steps 0 -> 1 at a sweep and never returns.
    leak_start = 6
    leak_vals = _permanent_leak_values(HOURS, start_h=leak_start, height=1)
    healthy_vals = [0] * HOURS

    # Eval points (1h series interval):
    # - mid-plateau: bare level is true; min_over_time[13h] still sees prior zeros.
    mid_plateau_h = plateau_start + SWEEP_INTERVAL_H - 1  # last hour of plateau
    # - after plateau cleared
    post_plateau_h = plateau_start + SWEEP_INTERVAL_H + 2
    # - first hour where 13h lookback is entirely inside the permanent leak
    #   (leak_start + LOOKBACK_H - 1): samples [leak_start, that hour] are all 1.
    leak_min_true_h = leak_start + LOOKBACK_H - 1
    # - one eval later so for:5m is satisfied under a 1h evaluation_interval
    leak_fire_h = leak_min_true_h + 1

    class _Q(str):
        pass

    def _represent_q(dumper: yaml.Dumper, data: str) -> Any:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

    yaml.add_representer(_Q, _represent_q)

    def _quote_values(doc: dict[str, Any]) -> None:
        for t in doc["tests"]:
            for s in t["input_series"]:
                s["values"] = _Q(s["values"])

    with tempfile.TemporaryDirectory(prefix="kopiur-creds-leak-promtool-") as tmp:
        work = Path(tmp)
        _write_rule_file(work / "kopiur_rules.yml", rule)
        check_out = _run_promtool(["check", "rules", "kopiur_rules.yml"], work)

        unit = {
            "rule_files": ["kopiur_rules.yml"],
            "evaluation_interval": "1h",
            "tests": [
                {
                    "name": "benign_six_hour_census_plateau_stays_silent",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": "kopiur_projected_secrets_live",
                            "values": _series(plateau_vals),
                        }
                    ],
                    "promql_expr_test": [
                        # Old bare level IS true at the frozen plateau - the
                        # exact false-positive shape that paged for hours.
                        {
                            "expr": OLD_LEVEL_EXPR,
                            "eval_time": f"{mid_plateau_h}h",
                            "exp_samples": [
                                {
                                    "labels": '{__name__="kopiur_projected_secrets_live"}',
                                    "value": 4,
                                }
                            ],
                        },
                        # Fixed multi-pass expression stays empty: the 13h window
                        # still contains pre-plateau zeros, so min is 0.
                        {
                            "expr": fixed_expr,
                            "eval_time": f"{mid_plateau_h}h",
                            "exp_samples": [],
                        },
                        {
                            "expr": fixed_expr,
                            "eval_time": f"{post_plateau_h}h",
                            "exp_samples": [],
                        },
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": f"{mid_plateau_h}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": f"{post_plateau_h}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [],
                        },
                        # End of timeline still silent - plateau never spanned
                        # two sweep passes without returning to zero.
                        {
                            "eval_time": f"{HOURS - 1}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [],
                        },
                    ],
                },
                {
                    "name": "permanent_leak_fires_after_two_sweep_passes",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": "kopiur_projected_secrets_live",
                            "values": _series(leak_vals),
                        }
                    ],
                    "promql_expr_test": [
                        # Before the lookback is fully inside the leak, min is 0.
                        {
                            "expr": fixed_expr,
                            "eval_time": f"{leak_start + LOOKBACK_H - 2}h",
                            "exp_samples": [],
                        },
                        # Once every sample in the 13h window is the leaked
                        # nonzero census, min_over_time > 0 becomes true.
                        {
                            "expr": fixed_expr,
                            "eval_time": f"{leak_min_true_h}h",
                            "exp_samples": [
                                {
                                    "labels": "{}",
                                    "value": 1,
                                }
                            ],
                        },
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": f"{leak_min_true_h}h",
                            "alertname": ALERT_NAME,
                            # First hour the expr is true: pending under for:5m
                            # with 1h eval interval - not yet firing.
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": f"{leak_fire_h}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": ALERT_NAME,
                                        "severity": "critical",
                                    },
                                    "exp_annotations": {
                                        "summary": summary,
                                    },
                                }
                            ],
                        },
                    ],
                },
                {
                    "name": "healthy_zero_census_stays_silent",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": "kopiur_projected_secrets_live",
                            "values": _series(healthy_vals),
                        }
                    ],
                    "promql_expr_test": [
                        {
                            "expr": fixed_expr,
                            "eval_time": "24h",
                            "exp_samples": [],
                        },
                        {
                            "expr": OLD_LEVEL_EXPR,
                            "eval_time": "24h",
                            "exp_samples": [],
                        },
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": "24h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": f"{HOURS - 1}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [],
                        },
                    ],
                },
            ],
        }

        _quote_values(unit)
        test_path = work / "kopiur_creds_leak_test.yml"
        test_path.write_text(yaml.dump(unit, sort_keys=False, width=1000))
        test_out = _run_promtool(["test", "rules", test_path.name], work)

        # Regression proof: the PRE-FIX bare-level rule with for:1h DOES fire on
        # the identical benign plateau series. Same input, opposite alert outcome
        # - the reason the expression had to change.
        old_rule = {
            "groups": [
                {
                    "name": "kopiur-absent.rules-old",
                    "rules": [
                        {
                            "alert": ALERT_NAME,
                            "expr": OLD_LEVEL_EXPR + "\n",
                            "for": "1h",
                            "labels": {"severity": "critical"},
                            "annotations": {"summary": "old bare level"},
                        }
                    ],
                }
            ]
        }
        (work / "kopiur_rules_old.yml").write_text(
            yaml.safe_dump(old_rule, sort_keys=False)
        )
        # for:1h under a 1h evaluation_interval fires on the second consecutive
        # true hour of the plateau (start+1).
        old_fire_h = plateau_start + 1
        old_unit = {
            "rule_files": ["kopiur_rules_old.yml"],
            "evaluation_interval": "1h",
            "tests": [
                {
                    "name": "old_bare_level_fires_on_benign_plateau",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": "kopiur_projected_secrets_live",
                            "values": _series(plateau_vals),
                        }
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": f"{old_fire_h}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": ALERT_NAME,
                                        "severity": "critical",
                                    },
                                    "exp_annotations": {
                                        "summary": "old bare level",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        _quote_values(old_unit)
        old_path = work / "kopiur_creds_leak_old_test.yml"
        old_path.write_text(yaml.dump(old_unit, sort_keys=False, width=1000))
        old_out = _run_promtool(["test", "rules", old_path.name], work)

        # Evidence dump for the outer test gate: the series shapes + eval points.
        evidence = {
            "plateau_values_head": plateau_vals[
                : plateau_start + SWEEP_INTERVAL_H + 3
            ],
            "mid_plateau_h": mid_plateau_h,
            "post_plateau_h": post_plateau_h,
            "old_fire_h": old_fire_h,
            "leak_start_h": leak_start,
            "leak_min_true_h": leak_min_true_h,
            "leak_fire_h": leak_fire_h,
            "fixed_expr": fixed_expr,
            "old_level_expr": OLD_LEVEL_EXPR,
        }

    return {
        "check_rules": check_out.splitlines()[-1] if check_out else "SUCCESS",
        "test_rules": "PASS",
        "test_out_tail": test_out[-300:],
        "old_regression_out_tail": old_out[-200:],
        "evidence": evidence,
        "scenarios": [
            "benign_six_hour_census_plateau_stays_silent",
            "permanent_leak_fires_after_two_sweep_passes",
            "healthy_zero_census_stays_silent",
            "old_bare_level_fires_on_benign_plateau",
        ],
    }


def main() -> int:
    print("==> load KopiurProjectedCredentialsLeaking from live PrometheusRule")
    rule = prometheus_rule(KOPIUR_RULE)
    alerts = alerts_by_name(rule)
    require(ALERT_NAME in alerts, f"missing alert {ALERT_NAME}")
    alert = alerts[ALERT_NAME]
    print(f"    found {ALERT_NAME}")

    print("==> multi-pass lookback / severity contract")
    contract = assert_rule_contract(alert)
    print(
        f"    OK lookback={contract['lookback_h']}h "
        f"sweep={contract['sweep_h']}h for={contract['for']} "
        f"severity={contract['severity']}"
    )
    print(f"    expr={contract['expr']!r}")

    print("==> promtool check + unit-test fire/silence matrix")
    semantics = assert_promtool_semantics(alert, rule)
    print(
        f"    OK check_rules={semantics['check_rules']!r} "
        f"test_rules={semantics['test_rules']}"
    )
    for name in semantics["scenarios"]:
        print(f"    scenario PASS: {name}")
    ev = semantics["evidence"]
    print(
        f"    plateau mid={ev['mid_plateau_h']}h "
        f"(fixed silent); old bare-level fires at {ev['old_fire_h']}h; "
        f"leak fires at {ev['leak_fire_h']}h "
        f"(min_over_time true from {ev['leak_min_true_h']}h)"
    )

    print("PASS: KopiurProjectedCredentialsLeaking multi-pass semantics hold")
    print("covered:")
    print("  - benign 6h frozen census plateau does NOT fire under fixed rule")
    print("  - identical plateau DOES fire under pre-fix bare level + for:1h")
    print("  - permanent leak fires once min_over_time[13h] stays > 0 across sweeps")
    print("  - healthy zero census stays silent")
    print("  - promtool check rules accepts the PrometheusRule groups")
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
