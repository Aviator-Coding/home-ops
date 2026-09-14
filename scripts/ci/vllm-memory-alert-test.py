#!/usr/bin/env python3
"""Behavioral regression for the ai/vllm memory-approach PrometheusRule.

Pins the 2026-09-14 fix that replaced a static-threshold
`VLLMMemoryApproachingLimit` warning rule with a growth-aware one, and the
same-day follow-up that stopped that growth-aware rule from false-positiving
on every pod restart:

  Measured live over the pod's full ~159h uptime, the working-set/limit
  ratio sat in a persistent 65.31-78.99% band for the last 48h and had not
  dipped below ~65% in two days. Any static threshold at or below ~79% is
  therefore either already permanently true (an alert the operator learns to
  ignore) or gives no advance warning, and the 70-78% band where the pod
  spends most of its time crossed back and forth 25-39 times in 48h, so a
  static threshold anywhere in the observed band would flap. The fixed rule
  instead keys on trajectory via `predict_linear` (window 6h, horizon 24h,
  for: 1h) - see kubernetes/apps/base/ai/vllm/app/prometheusrule.yaml for the
  full derivation.

  `predict_linear` alone was then found to false-positive on a fresh pod
  restart: verified live, a 2026-09-14 restart projected a 1.19x crossing
  from its steep post-boot ramp while the pod sat at a harmless 0.20 of its
  limit. The rule now requires BOTH the predicted crossing AND an
  already-elevated current ratio (`and on (namespace, pod, container)` a
  `> 0.6` floor) - above the measured post-restart ramp, below the
  pre-restart 48h steady-state noise band.

  `VLLMMemoryCriticalLimit` (static ratio > 0.85, for: 15m) is unchanged and
  still a legitimate fast-spike backstop.

This test does NOT grep source text as evidence. It:

  1. Loads the real PrometheusRule Flux would apply and asserts its
     structural contract (predict_linear window/horizon, for: durations,
     thresholds, the vllm-embed exclusion, severity labels).
  2. Feeds it to Prometheus' own rule unit-test engine (`promtool test
     rules`) with synthetic series and asserts observable alert
     firing/silence:
       - A 48h noisy-but-non-climbing series shaped like the documented
         65-78% band never fires either alert.
       - A fresh-restart-shaped ramp (steep early climb from a low base,
         modeled on the real 2026-09-14 false positive) never fires the
         warning alert, even though its raw predict_linear projection
         crosses the limit almost immediately - the current-ratio floor
         holds it quiet because the ratio never reaches 0.6.
       - A series that climbs steadily toward the limit from an
         already-elevated base fires the warning alert once predict_linear's
         projection has held above the limit for the full for:1h window -
         and not a moment before.
       - The unchanged critical backstop still fires after 15m sustained
         above 0.85 and resolves immediately (not gated by for:) once the
         ratio drops back below 0.85.
       - Adversarial: the sibling vllm-embed controller (own 16Gi limit,
         shares the "vllm-" pod-name prefix and "app" container name) never
         appears in any exp_alerts despite being pinned at a critically high
         ratio of its own limit throughout every scenario.

promtool is resolved the same way as backup-silent-failure-alerting-test.py
(native aqua install preferred; podman image fallback).
"""

from __future__ import annotations

import math
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
VLLM_RULE = ROOT / "kubernetes/apps/base/ai/vllm/app/prometheusrule.yaml"

PROMTOOL_IMAGE = os.environ.get(
    "PROMTOOL_IMAGE", "quay.io/prometheus/prometheus:v3.2.1"
)

EMBED_POD = "vllm-vllm-embed-abc1234"
MAIN_POD = "vllm-7cfbc95d5-9cqm7"
CLIMB_POD = "vllm-climb"
NOISY_POD = "vllm-noisy"
RAMP_POD = "vllm-ramp"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def _strip_tail(s: str) -> str:
    """Strip trailing whitespace and closing parens, so a substring check on
    a PromQL clause is not sensitive to how many `)` follow it."""
    return re.sub(r"[\s)]+$", "", s)


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
            f"promtool {' '.join(args)} failed (exit {proc.returncode}):\n"
            f"{out.strip()}"
        )
    return out.strip() or "SUCCESS"


def _write_rule_file(path: Path, rule: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump({"groups": rule["spec"]["groups"]}, sort_keys=False)
    )


class _Q(str):
    pass


def _represent_q(dumper: yaml.Dumper, data: str) -> Any:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


yaml.add_representer(_Q, _represent_q)


def _series(values: list[float]) -> _Q:
    return _Q(" ".join(f"{v:.6f}" for v in values))


def assert_structural_contract(alerts: dict[str, dict[str, Any]]) -> None:
    require("VLLMMemoryApproachingLimit" in alerts, "missing alert VLLMMemoryApproachingLimit")
    require("VLLMMemoryCriticalLimit" in alerts, "missing alert VLLMMemoryCriticalLimit")

    warn = alerts["VLLMMemoryApproachingLimit"]
    crit = alerts["VLLMMemoryCriticalLimit"]
    warn_expr = warn["expr"]
    crit_expr = crit["expr"]

    require(
        "predict_linear(" in warn_expr,
        "VLLMMemoryApproachingLimit must be growth-aware (predict_linear), "
        "not a static threshold - a static threshold at or below the "
        "observed ~79% ceiling is permanently true on this pod's real "
        "48h history",
    )
    require(
        "[6h]" in warn_expr,
        "VLLMMemoryApproachingLimit predict_linear window must be 6h",
    )
    require(
        ", 86400)" in warn_expr,
        "VLLMMemoryApproachingLimit predict_linear horizon must be 86400s (24h)",
    )
    require(
        warn.get("for") == "1h",
        "VLLMMemoryApproachingLimit for: must be 1h",
    )
    require(
        warn_expr.count("predict_linear(") == 1,
        "VLLMMemoryApproachingLimit's current-ratio floor clause must use the "
        "raw container_memory_working_set_bytes ratio, not another "
        "predict_linear projection",
    )
    require(
        "and on (namespace, pod, container)" in warn_expr,
        "VLLMMemoryApproachingLimit must AND the predicted-crossing clause "
        "with an already-elevated current-ratio floor - predict_linear alone "
        "fires on a fresh pod restart's steep early ramp before there is any "
        "real danger (measured live 2026-09-14: fires at ratio=0.20, 6h "
        "post-restart, projecting 1.19x)",
    )
    proj_clause, _, floor_clause = warn_expr.partition(
        "and on (namespace, pod, container)"
    )
    require(
        _strip_tail(proj_clause).endswith("> 1"),
        "VLLMMemoryApproachingLimit's predict_linear clause must compare the "
        "projected ratio against 1 (100% of limit)",
    )
    require(
        _strip_tail(floor_clause).endswith("> 0.6"),
        "VLLMMemoryApproachingLimit's current-ratio floor must be 0.6 - above "
        "the measured post-restart ramp (0.20 at 6h post-restart) and below "
        "the pre-restart 48h steady-state noise band (0.6764-0.7761)",
    )

    require(
        "predict_linear(" not in crit_expr,
        "VLLMMemoryCriticalLimit must remain a static fast-spike backstop, not predictive",
    )
    require(
        crit_expr.strip().endswith("> 0.85"),
        "VLLMMemoryCriticalLimit threshold must remain 0.85",
    )
    require(
        crit.get("for") == "15m",
        "VLLMMemoryCriticalLimit for: must remain 15m",
    )

    for name, alert in alerts.items():
        expr = alert["expr"]
        require(
            'pod!~"vllm-vllm-embed-.*"' in expr,
            f"{name}: must exclude the sibling vllm-embed controller",
        )
        require(
            "kube_pod_container_resource_limits" in expr,
            f"{name}: must join against kube_pod_container_resource_limits, "
            "not the dead container_spec_* metric family",
        )
        require(
            "container_spec_" not in expr,
            f"{name}: must not use the dead container_spec_* metric family",
        )

    require(warn["labels"]["severity"] == "warning", "VLLMMemoryApproachingLimit must be severity=warning")
    require(crit["labels"]["severity"] == "critical", "VLLMMemoryCriticalLimit must be severity=critical")


def _noisy_band_series(hours: int = 48, step_min: int = 5) -> list[float]:
    """48h-shaped noise: mean 0.7275, amplitude 0.0625 (65.31-78.99% band),
    period 4h - matches the live-measured shape documented in the rule file,
    and never trends toward the limit."""
    n = hours * 60 // step_min
    period_samples = 4 * 60 // step_min
    mean, amp = 0.7275, 0.0625
    return [mean + amp * math.sin(2 * math.pi * i / period_samples) for i in range(n)]


def _post_restart_ramp_series(hours: int = 10, step_min: int = 5) -> list[float]:
    """A fresh pod restart's steep early ramp: linear from ratio 0.05 at
    boot (t=0) at 0.045/hour, reaching 0.50 by t=10h - modeled on the real
    2026-09-14 false positive (predict_linear projected 1.19x while the
    pod sat at a harmless 0.20 of its limit, 6h post-restart) and on the
    measured 2026-09-07 boot curve's early steepness (+7 GiB in day one).
    Stays strictly below the 0.6 floor for the whole window, so any
    firing here is a regression of the false positive the floor exists to
    prevent."""
    n = hours * 60 // step_min
    rate_per_step = 0.045 / (60 // step_min)
    return [0.05 + rate_per_step * i for i in range(n)]


def _sustained_climb_series(lead_hours: int = 6, climb_hours: int = 30, step_min: int = 5) -> list[float]:
    """6h flat at 0.70, then a sustained climb at 0.0104/hour (~0.5 GiB/h
    against a 48Gi limit, the fastest rate ever observed for this pod)."""
    total = (lead_hours + climb_hours) * 60 // step_min
    rate_per_step = 0.0104 / (60 // step_min)
    lead_steps = lead_hours * 60 // step_min
    out = []
    for i in range(total):
        if i < lead_steps:
            out.append(0.70)
        else:
            out.append(0.70 + rate_per_step * (i - lead_steps))
    return out


def assert_promtool_semantics(rule: dict[str, Any]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="vllm-alert-promtool-") as tmp:
        work = Path(tmp)
        _write_rule_file(work / "vllm_rules.yml", rule)

        check_out = _run_promtool(["check", "rules", "vllm_rules.yml"], work)

        noisy = _noisy_band_series()
        n_noisy = len(noisy)
        embed_noisy = [0.95] * n_noisy
        limit_noisy = [1.0] * n_noisy

        climb = _sustained_climb_series()
        n_climb = len(climb)
        embed_climb = [0.95] * n_climb
        limit_climb = [1.0] * n_climb

        backstop_vals = [0.40] * 5 + [0.76] * 10 + [0.90] * 8 + [0.70] * 4 + [0.55] * 4
        n_backstop = len(backstop_vals)
        embed_backstop = [0.95] * n_backstop
        limit_backstop = [1.0] * n_backstop

        ramp = _post_restart_ramp_series()
        n_ramp = len(ramp)
        embed_ramp = [0.95] * n_ramp
        limit_ramp = [1.0] * n_ramp

        test_doc = {
            "rule_files": ["vllm_rules.yml"],
            "evaluation_interval": "5m",
            "tests": [
                {
                    "name": "quiet_on_noisy_non_climbing_band",
                    "interval": "5m",
                    "input_series": [
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{NOISY_POD}", container="app"}}',
                            "values": _series(noisy),
                        },
                        {
                            "series": f'kube_pod_container_resource_limits{{namespace="ai", pod="{NOISY_POD}", container="app", resource="memory"}}',
                            "values": _series(limit_noisy),
                        },
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{EMBED_POD}", container="app"}}',
                            "values": _series(embed_noisy),
                        },
                        {
                            "series": f'kube_pod_container_resource_limits{{namespace="ai", pod="{EMBED_POD}", container="app", resource="memory"}}',
                            "values": _series(limit_noisy),
                        },
                    ],
                    "alert_rule_test": [
                        {"eval_time": t, "alertname": alertname, "exp_alerts": []}
                        for t in ["12h", "24h", "36h", "48h"]
                        for alertname in ("VLLMMemoryApproachingLimit", "VLLMMemoryCriticalLimit")
                    ],
                },
                {
                    "name": "quiet_during_post_restart_ramp",
                    "interval": "5m",
                    "input_series": [
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{RAMP_POD}", container="app"}}',
                            "values": _series(ramp),
                        },
                        {
                            "series": f'kube_pod_container_resource_limits{{namespace="ai", pod="{RAMP_POD}", container="app", resource="memory"}}',
                            "values": _series(limit_ramp),
                        },
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{EMBED_POD}", container="app"}}',
                            "values": _series(embed_ramp),
                        },
                        {
                            "series": f'kube_pod_container_resource_limits{{namespace="ai", pod="{EMBED_POD}", container="app", resource="memory"}}',
                            "values": _series(limit_ramp),
                        },
                    ],
                    "alert_rule_test": [
                        # This is the regression case for the 2026-09-14
                        # false positive: the raw predict_linear clause
                        # crosses 1 almost immediately (projected ~1.13 at
                        # t=0, climbing from there), so for:1h alone would
                        # already have been satisfied by t=1h under the
                        # pre-fix rule. The current-ratio floor must keep
                        # the alert quiet regardless, because the ratio
                        # never reaches 0.6 anywhere in this series (it
                        # tops out at ~0.496 at t=9h55m).
                        {"eval_time": t, "alertname": alertname, "exp_alerts": []}
                        for t in ["1h", "3h", "6h", "8h", "9h45m"]
                        for alertname in ("VLLMMemoryApproachingLimit", "VLLMMemoryCriticalLimit")
                    ],
                },
                {
                    "name": "warning_fires_on_sustained_climb",
                    "interval": "5m",
                    "input_series": [
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{CLIMB_POD}", container="app"}}',
                            "values": _series(climb),
                        },
                        {
                            "series": f'kube_pod_container_resource_limits{{namespace="ai", pod="{CLIMB_POD}", container="app", resource="memory"}}',
                            "values": _series(limit_climb),
                        },
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{EMBED_POD}", container="app"}}',
                            "values": _series(embed_climb),
                        },
                        {
                            "series": f'kube_pod_container_resource_limits{{namespace="ai", pod="{EMBED_POD}", container="app", resource="memory"}}',
                            "values": _series(limit_climb),
                        },
                    ],
                    "alert_rule_test": [
                        # Raw predict_linear ratio first crosses 1 at t=11h30m
                        # (measured); for:1h means firing must not start
                        # before t=12h30m.
                        {
                            "eval_time": "12h25m",
                            "alertname": "VLLMMemoryApproachingLimit",
                            "exp_alerts": [],
                        },
                        {
                            "eval_time": "12h30m",
                            "alertname": "VLLMMemoryApproachingLimit",
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": "VLLMMemoryApproachingLimit",
                                        "severity": "warning",
                                        "namespace": "ai",
                                        "pod": CLIMB_POD,
                                        "container": "app",
                                    },
                                    "exp_annotations": {
                                        "summary": "vllm is on track to exceed its memory limit",
                                        "description": (
                                            f"vllm pod {CLIMB_POD} is projected to exceed its "
                                            "memory limit within the next ~24h based on its "
                                            "recent growth rate (currently 101.7% of limit "
                                            "projected). This is the captain's LLM - plan "
                                            "capacity or investigate the growth before it "
                                            "reaches the critical threshold."
                                        ),
                                    },
                                }
                            ],
                        },
                    ],
                },
                {
                    "name": "critical_backstop_unchanged_and_embed_excluded",
                    "interval": "5m",
                    "input_series": [
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{MAIN_POD}", container="app"}}',
                            "values": _series(backstop_vals),
                        },
                        {
                            "series": f'kube_pod_container_resource_limits{{namespace="ai", pod="{MAIN_POD}", container="app", resource="memory"}}',
                            "values": _series(limit_backstop),
                        },
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{EMBED_POD}", container="app"}}',
                            "values": _series(embed_backstop),
                        },
                        {
                            "series": f'kube_pod_container_resource_limits{{namespace="ai", pod="{EMBED_POD}", container="app", resource="memory"}}',
                            "values": _series(limit_backstop),
                        },
                    ],
                    "alert_rule_test": [
                        # t=15m: baseline 0.40 - quiet.
                        {"eval_time": "15m", "alertname": "VLLMMemoryCriticalLimit", "exp_alerts": []},
                        # t=70m: 0.76 since t=25m - below 0.85, still quiet.
                        {"eval_time": "70m", "alertname": "VLLMMemoryCriticalLimit", "exp_alerts": []},
                        # t=105m: 0.90 since t=75m (30m >= for:15m) - fires,
                        # and only for the main pod - vllm-embed (pinned at
                        # 0.95 of its OWN limit the entire time) must never
                        # appear despite being critically over its limit too.
                        {
                            "eval_time": "105m",
                            "alertname": "VLLMMemoryCriticalLimit",
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": "VLLMMemoryCriticalLimit",
                                        "severity": "critical",
                                        "namespace": "ai",
                                        "pod": MAIN_POD,
                                        "container": "app",
                                    },
                                    "exp_annotations": {
                                        "summary": "vllm is close to being OOMKilled",
                                        "description": (
                                            f"vllm pod {MAIN_POD} is at 90% of its memory "
                                            "limit and is close to an OOMKill. This is the "
                                            "captain's LLM - act now, not after the crash."
                                        ),
                                    },
                                }
                            ],
                        },
                        # t=115m: dropped to 0.70 at t=115m - resolves
                        # IMMEDIATELY (resolution is not gated by for:, only
                        # the onset is).
                        {"eval_time": "115m", "alertname": "VLLMMemoryCriticalLimit", "exp_alerts": []},
                    ],
                },
            ],
        }

        test_path = work / "vllm_alerts_test.yml"
        test_path.write_text(yaml.dump(test_doc, sort_keys=False, width=1000))
        test_out = _run_promtool(["test", "rules", test_path.name], work)

    return {
        "check": check_out.splitlines()[-1] if check_out else "SUCCESS",
        "test": "PASS",
        "test_out_tail": test_out[-200:],
    }


def main() -> int:
    rule = prometheus_rule(VLLM_RULE)
    alerts = alerts_by_name(rule)

    print("==> structural contract (predict_linear window/horizon, for:, thresholds, vllm-embed exclusion)")
    assert_structural_contract(alerts)
    print("    OK")

    print("==> promtool check + unit-test rule semantics")
    semantics = assert_promtool_semantics(rule)
    print(f"    OK check={semantics['check']!r} test={semantics['test']}")

    print("PASS: ai/vllm memory alerting contracts hold")
    print("covered:")
    print("  - VLLMMemoryApproachingLimit uses predict_linear (6h window, 24h horizon, for:1h)")
    print("    AND on (namespace, pod, container) an already-elevated current-ratio floor (> 0.6)")
    print("  - quiet against a 48h noisy-but-non-climbing series shaped like the live 65-78% band")
    print("  - quiet during a fresh-restart-shaped ramp despite predict_linear projecting a crossing")
    print("  - fires once a sustained climb's projection has held above the limit for for:1h")
    print("  - VLLMMemoryCriticalLimit stays a static 0.85/for:15m backstop, resolves immediately on drop")
    print("  - the sibling vllm-embed controller never contaminates either alert")
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
