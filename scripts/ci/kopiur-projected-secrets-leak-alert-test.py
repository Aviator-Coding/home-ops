#!/usr/bin/env python3
"""Behavioral regression for KopiurProjectedCredentialsLeaking.

Pins two false-fire incidents on `kopiur_projected_secrets_live`:

  2026-08-31: the gauge is a leader-only periodic census written once per
  KOPIUR_WORK_SPEC_SWEEP_INTERVAL_SECS (default 6h). A single List() that
  catches legitimate in-flight projected Secrets freezes a nonzero reading
  until the next sweep, even after every Secret is reaped. The old rule
  (`kopiur_projected_secrets_live > 0` / `for: 1h`) therefore paged for hours
  on ordinary backup concurrency. Fixed by requiring the population to stay
  positive across at least two sweep passes: `min_over_time(...[13h]) > 0`.

  2026-09-26: that per-series fix is itself leader-label-sensitive. The gauge
  is exported only by the current Lease holder, so its `pod`/`instance`
  labels change on every leader change - a fresh series with no history. A
  new leader's OWN `min_over_time` sees only its own samples, so if its
  startup sweep alone catches a benign in-flight population, the alert fires
  immediately: the new series has never stored a 0. Fixed by aggregating
  away `pod`/`instance` with `max without (...)` before `min_over_time`, via
  a subquery, so the series is continuous straight through a leader change.

This test does NOT grep source text as evidence. It:

  1. Loads the real PrometheusRule Flux would apply.
  2. Feeds it to Prometheus' own rule unit-test engine (`promtool test rules`)
     with synthetic series that model:
       - the 2026-08-31 6h plateau incident (benign mid-flight census, single
         leader series)
       - a genuine one-shot permanent leak on a single leader series
       - a healthy always-zero fleet
       - the 2026-09-26 leader-change incident: a fresh leader series that
         starts at a benign nonzero census and drops to zero at its own next
         sweep
       - a genuine leak that survives a leader change mid-leak (must still
         fire)
  3. Asserts observable alert fire/silence and PromQL sample sets for the
     fixed expression against both older, narrower expressions (proving each
     regression shape the fix closes).

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
# The 2026-08-31 fix: correct multi-pass semantics, but per-series - this is
# what regressed on 2026-09-26 across a leader (pod label) change.
PER_SERIES_MULTIPASS_EXPR = "min_over_time(kopiur_projected_secrets_live[13h])"


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
    compact = "".join(expr.split())

    require(
        "min_over_time" in expr and "kopiur_projected_secrets_live" in expr,
        f"{ALERT_NAME}: expr must use min_over_time over kopiur_projected_secrets_live; "
        f"got {expr!r}",
    )
    require(
        f"[{LOOKBACK_H}h:" in compact,
        f"{ALERT_NAME}: lookback must be a [{LOOKBACK_H}h:...] subquery "
        f"(>2x the {SWEEP_INTERVAL_H}h default sweep) so it survives a "
        f"leader (pod label) change; got {expr!r}",
    )
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

    # --- Single-series scenarios (no leader change) -----------------------
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

    # A single sweep catching exactly one real in-flight credential copy - the
    # smallest possible nonzero census, not just the 2026-08-31 height-4
    # plateau. Measured live: the 23:26:52Z sweep published 1
    # (ai/hermes-r2-...-creds-0, a real backup mid-run), the reaper removed
    # it 3 minutes later, and the NEXT sweep (05:26:52Z, 6h later) read 0
    # again - the gauge holds the frozen 1 the whole 6h between, regardless
    # of the reaper's action minutes in. Same 0 -> N -> 0 shape as the
    # plateau above, at the minimum possible N, on a single (non-leader-
    # change) series.
    single_copy_start = 10
    single_copy_vals = _plateau_values(
        HOURS, start_h=single_copy_start, duration_h=SWEEP_INTERVAL_H, height=1
    )
    single_copy_mid_h = single_copy_start + SWEEP_INTERVAL_H - 1
    single_copy_post_h = single_copy_start + SWEEP_INTERVAL_H + 2

    # Eval points (1h series interval):
    mid_plateau_h = plateau_start + SWEEP_INTERVAL_H - 1  # last hour of plateau
    post_plateau_h = plateau_start + SWEEP_INTERVAL_H + 2  # after plateau cleared
    leak_min_true_h = leak_start + LOOKBACK_H - 1
    leak_fire_h = leak_min_true_h + 1

    # --- Leader-change scenarios --------------------------------------
    # (A) 2026-09-26 shape: the old leader (pod="a") ran healthy - zero for a
    # long stretch - then the Lease changed hands. The new leader (pod="b")
    # has NO prior history; its own startup sweep catches a benign in-flight
    # population (8, matching the live incident), holds it for one sweep
    # interval, then its own next sweep correctly reads 0 and stays there.
    # The fixed rule must never fire anywhere in this timeline.
    old_leader_zero_h = 20  # long enough to anchor a 13h lookback pre-switch
    switch_h = old_leader_zero_h
    new_leader_plateau_h = SWEEP_INTERVAL_H
    tail_h = HOURS - switch_h - new_leader_plateau_h
    leader_switch_old_vals = [0] * old_leader_zero_h
    leader_switch_new_vals = (
        ["_"] * switch_h
        + [8] * new_leader_plateau_h
        + [0] * tail_h
    )

    # (B) A genuine leak starts under the old leader (pod="a"), is still live
    # when the Lease changes hands mid-leak, and the new leader (pod="b")
    # continues to observe it as positive on every subsequent sweep of its
    # own. The fixed rule must still fire once the aggregated series has
    # gone LOOKBACK_H without ever reading zero, same as the single-series
    # case - the leader change must not reset the persistence requirement.
    leak_switch_h = leak_start + SWEEP_INTERVAL_H - 1  # switch mid-leak, still positive
    leak_switch_old_vals = [0] * leak_start + [1] * (leak_switch_h - leak_start + 1)
    leak_switch_new_vals = ["_"] * (leak_switch_h + 1) + [1] * (
        HOURS - leak_switch_h - 1
    )
    leak_switch_min_true_h = leak_start + LOOKBACK_H - 1
    leak_switch_fire_h = leak_switch_min_true_h + 1

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
                    # Smallest possible nonzero census (N=1) from a single
                    # real in-flight credential copy, reaped minutes later -
                    # the exact 23:26:52Z -> 05:26:52Z live shape. Must not
                    # fire: no leak, just one sweep catching one real,
                    # short-lived, legitimate copy.
                    "name": "single_sweep_inflight_copy_between_zeros_stays_silent",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": "kopiur_projected_secrets_live",
                            "values": _series(single_copy_vals),
                        }
                    ],
                    "promql_expr_test": [
                        {
                            "expr": fixed_expr,
                            "eval_time": f"{single_copy_mid_h}h",
                            "exp_samples": [],
                        },
                        {
                            "expr": fixed_expr,
                            "eval_time": f"{single_copy_post_h}h",
                            "exp_samples": [],
                        },
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": f"{single_copy_mid_h}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": f"{single_copy_post_h}h",
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
                {
                    # 2026-09-26 regression: a fresh leader series (new pod,
                    # no history) starts at a benign nonzero census and drops
                    # to zero at its own next sweep. Must never fire.
                    "name": "fresh_leader_series_benign_census_then_zero_stays_silent",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": 'kopiur_projected_secrets_live{pod="leader-a",instance="10.0.0.1:8081"}',
                            "values": _series(leader_switch_old_vals),
                        },
                        {
                            "series": 'kopiur_projected_secrets_live{pod="leader-b",instance="10.0.0.2:8081"}',
                            "values": _series(leader_switch_new_vals),
                        },
                    ],
                    "promql_expr_test": [
                        # The pre-fix per-series form fires: pod="leader-b"'s
                        # OWN samples never touch zero at this eval time - it
                        # has no history before its own first (nonzero)
                        # sample. This is the exact regression shape.
                        {
                            "expr": PER_SERIES_MULTIPASS_EXPR + " > 0",
                            "eval_time": f"{switch_h + new_leader_plateau_h - 1}h",
                            "exp_samples": [
                                {
                                    "labels": '{pod="leader-b",instance="10.0.0.2:8081"}',
                                    "value": 8,
                                }
                            ],
                        },
                        # The fixed, aggregated form stays empty at every
                        # point in the timeline - the outgoing leader's zero
                        # remains inside the 13h window straight through the
                        # handover, and the new leader's own next sweep
                        # confirms zero besides.
                        {
                            "expr": fixed_expr,
                            "eval_time": f"{switch_h}h",
                            "exp_samples": [],
                        },
                        {
                            "expr": fixed_expr,
                            "eval_time": f"{switch_h + new_leader_plateau_h - 1}h",
                            "exp_samples": [],
                        },
                        {
                            "expr": fixed_expr,
                            "eval_time": f"{HOURS - 1}h",
                            "exp_samples": [],
                        },
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": f"{switch_h}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": f"{switch_h + new_leader_plateau_h - 1}h",
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
                {
                    # A genuine leak that survives a leader change mid-leak
                    # must still fire - the aggregated series was never zero
                    # across the transition, so the multi-pass requirement is
                    # unaffected by which pod happens to hold the Lease.
                    "name": "leader_change_mid_leak_still_fires",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": 'kopiur_projected_secrets_live{pod="leader-a",instance="10.0.0.1:8081"}',
                            "values": _series(leak_switch_old_vals),
                        },
                        {
                            "series": 'kopiur_projected_secrets_live{pod="leader-b",instance="10.0.0.2:8081"}',
                            "values": _series(leak_switch_new_vals),
                        },
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": f"{leak_switch_min_true_h}h",
                            "alertname": ALERT_NAME,
                            # Multi-pass expr just turned true - pending under
                            # for:5m, not yet firing.
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": f"{leak_switch_fire_h}h",
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
            ],
        }

        _quote_values(unit)
        test_path = work / "kopiur_creds_leak_test.yml"
        test_path.write_text(yaml.dump(unit, sort_keys=False, width=1000))
        test_out = _run_promtool(["test", "rules", test_path.name], work)

        # Regression proof: the PRE-2026-08-31-FIX bare-level rule with
        # for:1h DOES fire on the identical benign plateau series. Same
        # input, opposite alert outcome - the reason the expression first
        # changed.
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

        # Second regression proof: the PRE-2026-09-26-FIX per-series
        # multi-pass rule (the 2026-08-31 fix, verbatim, with no pod/instance
        # aggregation) DOES fire on a fresh leader series whose own history
        # starts at a benign nonzero census - the exact 2026-09-26 incident.
        per_series_rule = {
            "groups": [
                {
                    "name": "kopiur-absent.rules-per-series",
                    "rules": [
                        {
                            "alert": ALERT_NAME,
                            "expr": PER_SERIES_MULTIPASS_EXPR + " > 0\n",
                            "for": "5m",
                            "labels": {"severity": "critical"},
                            "annotations": {"summary": "per-series, no leader aggregation"},
                        }
                    ],
                }
            ]
        }
        (work / "kopiur_rules_per_series.yml").write_text(
            yaml.safe_dump(per_series_rule, sort_keys=False)
        )
        per_series_fire_h = switch_h + 1  # for:5m satisfied one eval after switch
        per_series_unit = {
            "rule_files": ["kopiur_rules_per_series.yml"],
            "evaluation_interval": "1h",
            "tests": [
                {
                    "name": "per_series_fires_on_fresh_leader_benign_census",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": 'kopiur_projected_secrets_live{pod="leader-a",instance="10.0.0.1:8081"}',
                            "values": _series(leader_switch_old_vals),
                        },
                        {
                            "series": 'kopiur_projected_secrets_live{pod="leader-b",instance="10.0.0.2:8081"}',
                            "values": _series(leader_switch_new_vals),
                        },
                    ],
                    "alert_rule_test": [
                        {
                            "eval_time": f"{per_series_fire_h}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": ALERT_NAME,
                                        "severity": "critical",
                                        "pod": "leader-b",
                                        "instance": "10.0.0.2:8081",
                                    },
                                    "exp_annotations": {
                                        "summary": "per-series, no leader aggregation",
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        _quote_values(per_series_unit)
        per_series_path = work / "kopiur_creds_leak_per_series_test.yml"
        per_series_path.write_text(yaml.dump(per_series_unit, sort_keys=False, width=1000))
        per_series_out = _run_promtool(["test", "rules", per_series_path.name], work)

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
            "leader_switch_h": switch_h,
            "leader_switch_new_plateau_h": new_leader_plateau_h,
            "per_series_fire_h": per_series_fire_h,
            "leak_switch_h": leak_switch_h,
            "leak_switch_fire_h": leak_switch_fire_h,
            "fixed_expr": fixed_expr,
            "old_level_expr": OLD_LEVEL_EXPR,
            "per_series_expr": PER_SERIES_MULTIPASS_EXPR,
        }

    return {
        "check_rules": check_out.splitlines()[-1] if check_out else "SUCCESS",
        "test_rules": "PASS",
        "test_out_tail": test_out[-300:],
        "old_regression_out_tail": old_out[-200:],
        "per_series_regression_out_tail": per_series_out[-200:],
        "evidence": evidence,
        "scenarios": [
            "benign_six_hour_census_plateau_stays_silent",
            "permanent_leak_fires_after_two_sweep_passes",
            "single_sweep_inflight_copy_between_zeros_stays_silent",
            "healthy_zero_census_stays_silent",
            "fresh_leader_series_benign_census_then_zero_stays_silent",
            "leader_change_mid_leak_still_fires",
            "old_bare_level_fires_on_benign_plateau",
            "per_series_fires_on_fresh_leader_benign_census",
        ],
    }


def main() -> int:
    print("==> load KopiurProjectedCredentialsLeaking from live PrometheusRule")
    rule = prometheus_rule(KOPIUR_RULE)
    alerts = alerts_by_name(rule)
    require(ALERT_NAME in alerts, f"missing alert {ALERT_NAME}")
    alert = alerts[ALERT_NAME]
    print(f"    found {ALERT_NAME}")

    print("==> multi-pass lookback / leader-aggregation / severity contract")
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
    print(
        f"    leader switch at {ev['leader_switch_h']}h: fixed rule stays "
        f"silent through the {ev['leader_switch_new_plateau_h']}h new-leader "
        f"plateau; per-series (pre-2026-09-26-fix) form fires at "
        f"{ev['per_series_fire_h']}h"
    )
    print(
        f"    leak spanning a leader switch at {ev['leak_switch_h']}h still "
        f"fires at {ev['leak_switch_fire_h']}h"
    )

    print("PASS: KopiurProjectedCredentialsLeaking multi-pass semantics hold")
    print("covered:")
    print("  - benign 6h frozen census plateau does NOT fire under fixed rule")
    print("  - identical plateau DOES fire under pre-fix bare level + for:1h")
    print("  - permanent leak fires once min_over_time[13h] stays > 0 across sweeps")
    print("  - a single sweep catching one real in-flight credential copy (N=1),")
    print("    reaped minutes later, does NOT fire (23:26:52Z -> 05:26:52Z live shape)")
    print("  - healthy zero census stays silent")
    print("  - a fresh leader series (new pod/instance, no history) that starts at a")
    print("    benign census and drops to zero at its own next sweep stays silent")
    print("    under the fixed (leader-aggregated) rule, but DOES fire under the")
    print("    2026-08-31 per-series form - the 2026-09-26 regression")
    print("  - a genuine leak that survives a leader change mid-leak still fires")
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
