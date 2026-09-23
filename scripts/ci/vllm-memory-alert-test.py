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
       - VLLMMemoryExceedsRequest (added 2026-09-20 with the 39Gi -> 12Gi
         request cut) fires once a sustained excursion above the REQUEST has
         held continuously for the full for:30m window, resolves immediately
         (not gated by for:) once the ratio drops back under 1, stays quiet
         through a 20-minute transient that never reaches the 30m for:, and
         never fires for the sibling vllm-embed controller despite it
         sitting at 2x its own request throughout - exercised the same way
         as the other two alerts, not just via the structural string checks
         below.

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
VLLM_HR = ROOT / "kubernetes/apps/base/ai/vllm/app/helmrelease.yaml"

# Inputs to VLLMMemoryRetainedAboveBound's retained ceiling (see the rule's
# comment). BASELINE_MIB is the measured post-load working set (2026-09-19).
# CHECKPOINT_MIB is one context checkpoint of this model's recurrent state, seen
# live as exact 62.8 MiB mappings (30 linear-attention layers x (2 MiB S + 96 KiB
# conv)). DEFAULT_CTX_CHECKPOINTS is llama.cpp b10820's n_ctx_checkpoints default
# (common/common.h), used when the args do not set --ctx-checkpoints.
BASELINE_MIB = 1232
CHECKPOINT_MIB = 62.8
DEFAULT_CTX_CHECKPOINTS = 32
GIB = 1024**3

PROMTOOL_IMAGE = os.environ.get(
    "PROMTOOL_IMAGE", "quay.io/prometheus/prometheus:v3.2.1"
)

EMBED_POD = "vllm-vllm-embed-abc1234"
MAIN_POD = "vllm-7cfbc95d5-9cqm7"
CLIMB_POD = "vllm-climb"
NOISY_POD = "vllm-noisy"
RAMP_POD = "vllm-ramp"
REQUEST_POD = "vllm-request"
RETAINED_OK_POD = "vllm-retained-ok"
RETAINED_LEAK_POD = "vllm-retained-leak"


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
    require("VLLMMemoryExceedsRequest" in alerts, "missing alert VLLMMemoryExceedsRequest")
    require("VLLMMemoryRetainedAboveBound" in alerts, "missing alert VLLMMemoryRetainedAboveBound")

    warn = alerts["VLLMMemoryApproachingLimit"]
    crit = alerts["VLLMMemoryCriticalLimit"]
    over = alerts["VLLMMemoryExceedsRequest"]
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
        # The invariant this pins is "resolve the denominator from a LIVE
        # kube-state-metrics series", not one specific metric name. It was
        # written as a literal `kube_pod_container_resource_limits` check when
        # every rule here was limit-based, and that froze the file against the
        # first legitimate request-based rule (VLLMMemoryExceedsRequest, added
        # 2026-09-20 with the 39Gi -> 12Gi request cut). Widened to the
        # relationship; the dead-metric half below is unchanged and is the half
        # that actually carries the value. See AGENTS.md on CI gates that freeze
        # a past PR's scope boundary. Narrowed again 2026-09-22 to RATIO rules
        # only: VLLMMemoryRetainedAboveBound compares an absolute floor against
        # a threshold derived from the HelmRelease's own --cache-ram (checked
        # below), so it has no denominator to resolve.
        if "/" in expr:
            require(
                any(
                    f"kube_pod_container_resource_{kind}" in expr
                    for kind in ("limits", "requests")
                ),
                f"{name}: a ratio rule must join against "
                "kube_pod_container_resource_limits or "
                "kube_pod_container_resource_requests, not the dead "
                "container_spec_* metric family",
            )
        require(
            "container_spec_" not in expr,
            f"{name}: must not use the dead container_spec_* metric family",
        )

    # VLLMMemoryExceedsRequest guards the REQUEST, which is where eviction
    # ranking is decided and which no other rule in this file can see. Pinned
    # here so it cannot quietly decay into a second limit rule.
    over_expr = over["expr"]
    require(
        "kube_pod_container_resource_requests" in over_expr,
        "VLLMMemoryExceedsRequest must compare against the REQUEST - comparing "
        "against the limit duplicates VLLMMemoryCriticalLimit and leaves the "
        "eviction exceeds-set unwatched",
    )
    require(
        "predict_linear(" not in over_expr,
        "VLLMMemoryExceedsRequest must stay a plain current-ratio rule - the "
        "shape it guards against is a prefill spike, which arrives with prompt "
        "volume rather than with time and which a linear projection misses",
    )
    require(
        over_expr.strip().endswith("> 1"),
        "VLLMMemoryExceedsRequest threshold must remain 1 (100% of request) - "
        "that is the exact point the pod re-enters the eviction exceeds-set",
    )
    require(
        over.get("for") == "30m",
        "VLLMMemoryExceedsRequest for: must be 30m - long enough that one "
        "transient cold-fill of the 4096 Mi prompt cache cannot page",
    )

    require(warn["labels"]["severity"] == "warning", "VLLMMemoryApproachingLimit must be severity=warning")
    require(crit["labels"]["severity"] == "critical", "VLLMMemoryCriticalLimit must be severity=critical")
    require(over["labels"]["severity"] == "warning", "VLLMMemoryExceedsRequest must be severity=warning")

    assert_retained_rule_contract(alerts["VLLMMemoryRetainedAboveBound"])


def _vllm_container() -> dict[str, Any]:
    for doc in load_docs(VLLM_HR):
        if doc.get("kind") == "HelmRelease" and (doc.get("metadata") or {}).get("name") == "vllm":
            return doc["spec"]["values"]["controllers"]["vllm"]["containers"]["app"]
    raise Failure(f"{VLLM_HR}: could not find the vllm HelmRelease")


def _arg_int(args: list[str], names: tuple[str, ...], default: int | None) -> int | None:
    for name in names:
        if name in args:
            idx = args.index(name)
            require(idx + 1 < len(args), f"{name} is present but has no value")
            raw = args[idx + 1]
            require(re.fullmatch(r"-?\d+", raw) is not None, f"{name} value {raw!r} is not an integer")
            return int(raw)
    return default


def _mib(quantity: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(Mi|Gi|Ti)", str(quantity).strip())
    require(match is not None, f"unsupported memory quantity {quantity!r}")
    assert match is not None
    return float(match.group(1)) * {"Mi": 1, "Gi": 1024, "Ti": 1024**2}[match.group(2)]


def retained_threshold_bytes(expr: str) -> float:
    """Parse the `> N * 1024^3` (or plain `> <bytes>`) tail of the floor rule."""
    tail = _strip_tail(expr)
    match = re.search(r">\s*(\d+(?:\.\d+)?)\s*\*\s*1024\s*\^\s*3$", tail)
    if match:
        return float(match.group(1)) * GIB
    match = re.search(r">\s*(\d+(?:\.\d+)?)$", tail)
    require(match is not None, "VLLMMemoryRetainedAboveBound must end in '> <GiB> * 1024^3' or '> <bytes>'")
    assert match is not None
    return float(match.group(1))


def assert_retained_rule_contract(rule: dict[str, Any]) -> None:
    """VLLMMemoryRetainedAboveBound: the 3h floor vs the config's retained ceiling.

    The threshold is a RELATIONSHIP to the HelmRelease, not a free constant: it
    must sit at or above everything llama.cpp is allowed to retain (baseline +
    --cache-ram + one slot's full context-checkpoint budget), or it pages on a
    healthy server, and below limits.memory, or it can never fire before the
    OOMKill. Raising --cache-ram without revisiting the threshold fails here.
    """
    expr = rule["expr"]
    require(
        re.search(r"min_over_time\(\s*container_memory_working_set_bytes\{[^}]*\}\[3h\]\s*\)", expr) is not None,
        "VLLMMemoryRetainedAboveBound must key on min_over_time(container_memory_working_set_bytes[3h]) - "
        "the RETAINED floor. The instantaneous working set includes prefill spikes and prompt-cache churn, "
        "which are bounded and must not page; a 3h minimum only moves when memory is kept",
    )
    require(
        "predict_linear(" not in expr,
        "VLLMMemoryRetainedAboveBound must stay a floor-vs-bound rule, not a projection",
    )
    require(rule.get("for") == "1h", "VLLMMemoryRetainedAboveBound for: must be 1h")
    require(
        rule["labels"]["severity"] == "warning",
        "VLLMMemoryRetainedAboveBound must be severity=warning",
    )

    container = _vllm_container()
    args = [str(a) for a in container.get("args", [])]
    cache_ram = _arg_int(args, ("--cache-ram", "-cram"), None)
    require(
        cache_ram is not None and cache_ram > 0,
        "the vllm HelmRelease must set a positive --cache-ram (vllm-prompt-cache-test.py owns why)",
    )
    assert cache_ram is not None
    checkpoints = _arg_int(
        args, ("--ctx-checkpoints", "-ctxcp", "--swa-checkpoints"), DEFAULT_CTX_CHECKPOINTS
    )
    assert checkpoints is not None
    ceiling_mib = BASELINE_MIB + cache_ram + max(checkpoints, 0) * CHECKPOINT_MIB
    threshold = retained_threshold_bytes(expr)
    require(
        threshold >= ceiling_mib * 1024**2,
        f"VLLMMemoryRetainedAboveBound threshold {threshold / GIB:.2f} GiB is below the retained ceiling "
        f"{ceiling_mib / 1024:.2f} GiB (baseline {BASELINE_MIB} Mi + --cache-ram {cache_ram} Mi + "
        f"{checkpoints} x {CHECKPOINT_MIB} Mi checkpoints). A healthy server can legitimately hold that much "
        "for hours, so this would page on normal operation - raise the threshold with the cache",
    )
    limit = ((container.get("resources") or {}).get("limits") or {}).get("memory")
    require(limit is not None, "ai/vllm must declare limits.memory")
    require(
        threshold < _mib(limit) * 1024**2,
        f"VLLMMemoryRetainedAboveBound threshold {threshold / GIB:.2f} GiB is not below limits.memory "
        f"({limit}) - it could never fire before the OOMKill",
    )


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


def _exceeds_request_series() -> list[float]:
    """Ratio-of-request shape for VLLMMemoryExceedsRequest, at 5m steps:

      t=0-25m   quiet baseline (0.5)
      t=30-45m  a 20-minute transient excursion (1.5) - shorter than the
                30m for:, so this must NEVER fire
      t=50-75m  back to quiet baseline
      t=80-125m a 45-minute sustained excursion (1.5) - long enough to
                clear for:30m, so this must fire starting at t=110m
      t=130-145m back to quiet baseline - must resolve immediately, not
                gated by for:

    0.5 and 1.5 are both exactly representable in binary float, so
    humanizePercentage's rendered "150%" in the fired annotation is not at
    the mercy of floating-point rounding.
    """
    return [0.5] * 6 + [1.5] * 4 + [0.5] * 6 + [1.5] * 10 + [0.5] * 4


def _cache_churn(i: int, low_gib: float, high_gib: float) -> float:
    """Hourly prompt-cache sawtooth at 5m steps: low at each hour start, rising
    to high by the hour's last sample (entries saved, then evicted/restored)."""
    return low_gib + (high_gib - low_gib) * (i % 12) / 11


def _retained_ok_series() -> list[float]:
    """A HEALTHY server's working set in BYTES, 12h at 5m steps - every shape
    the bounded consumers can legitimately produce, including the worst one:

      t=0-1h55m     post-load, cache still cold (3 GiB)
      t=2h-5h55m    prompt-cache churn between 5 and 7.3 GiB
      t=6h-7h40m    a 105-minute prefill/transient spike to 11.5 GiB - above
                    this rule's threshold AND above the 12Gi request
      t=7h45m-8h55m churn again
      t=9h-11h55m   held flat at the full retained ceiling (7.3 GiB: baseline
                    + a full 4096 MiB cache + one slot's full checkpoint
                    budget) for the whole 3h window

    The 3h floor never exceeds 7.3 GiB, so this must never fire.
    """
    out: list[float] = []
    for i in range(144):
        if i < 24:
            gib = 3.0
        elif 72 <= i < 93:
            gib = 11.5
        elif i >= 108:
            gib = 7.3
        else:
            gib = _cache_churn(i, 5.0, 7.3)
        out.append(gib * GIB)
    return out


def _retained_leak_series() -> list[float]:
    """The 2026-09-22 leak's shape in BYTES, 30h at 5m steps: a retained floor
    climbing 0.15 GiB/h (~3.6 GiB/day, the measured rate) from 5 GiB, with
    2 GiB of hourly cache churn on top. The floor reaches 8.0 GiB at exactly
    t=20h (not above it) and 8.15 GiB at t=21h. The first 3h window whose
    minimum clears 8 GiB is (20h, 23h] - range selectors are left-open, so
    the 20h00 sample drops out at t=23h - and for: 1h moves the firing to
    t=24h."""
    return [
        (5.0 + 0.15 * (i / 12) + _cache_churn(i, 0.0, 2.0)) * GIB
        for i in range(360)
    ]


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

        exceeds = _exceeds_request_series()
        n_exceeds = len(exceeds)
        embed_exceeds = [2.0] * n_exceeds
        request_one = [1.0] * n_exceeds

        retained_ok = _retained_ok_series()
        retained_leak = _retained_leak_series()
        # vllm-embed pinned at 20 GiB throughout: its 3h floor is far above the
        # threshold, so any leak through the pod!~ exclusion shows up at once.
        embed_retained_ok = [20.0 * GIB] * len(retained_ok)
        embed_retained_leak = [20.0 * GIB] * len(retained_leak)
        retained_rule = "VLLMMemoryRetainedAboveBound"

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
                {
                    "name": "exceeds_request_fires_holds_resolves_and_embed_excluded",
                    "interval": "5m",
                    "input_series": [
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{REQUEST_POD}", container="app"}}',
                            "values": _series(exceeds),
                        },
                        {
                            "series": f'kube_pod_container_resource_requests{{namespace="ai", pod="{REQUEST_POD}", container="app", resource="memory"}}',
                            "values": _series(request_one),
                        },
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{EMBED_POD}", container="app"}}',
                            "values": _series(embed_exceeds),
                        },
                        {
                            "series": f'kube_pod_container_resource_requests{{namespace="ai", pod="{EMBED_POD}", container="app", resource="memory"}}',
                            "values": _series(request_one),
                        },
                    ],
                    "alert_rule_test": [
                        # t=25m: quiet baseline (0.5) - quiet.
                        {"eval_time": "25m", "alertname": "VLLMMemoryExceedsRequest", "exp_alerts": []},
                        # t=45m: end of a 20-minute transient (1.5, t=30-45m)
                        # - shorter than for:30m, must NEVER fire.
                        {"eval_time": "45m", "alertname": "VLLMMemoryExceedsRequest", "exp_alerts": []},
                        # t=75m: back to quiet baseline after the transient.
                        {"eval_time": "75m", "alertname": "VLLMMemoryExceedsRequest", "exp_alerts": []},
                        # t=105m: 25m into the sustained excursion (started
                        # t=80m) - not yet 30m, must still be quiet (proves
                        # this isn't just "never fires", it's for:-gated).
                        {"eval_time": "105m", "alertname": "VLLMMemoryExceedsRequest", "exp_alerts": []},
                        # t=110m: exactly 30m into the sustained excursion -
                        # fires, and only for the request pod - vllm-embed
                        # (pinned at 2x its OWN request the entire time) must
                        # never appear.
                        {
                            "eval_time": "110m",
                            "alertname": "VLLMMemoryExceedsRequest",
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": "VLLMMemoryExceedsRequest",
                                        "severity": "warning",
                                        "namespace": "ai",
                                        "pod": REQUEST_POD,
                                        "container": "app",
                                    },
                                    "exp_annotations": {
                                        "summary": "vllm is using more memory than it reserves",
                                        "description": (
                                            f"vllm pod {REQUEST_POD} is at 150% of its memory "
                                            "REQUEST. It has re-entered the kubelet's eviction "
                                            "exceeds-set and will now be evicted before pods "
                                            "that stay within their requests. The 12Gi request "
                                            "was sized on 2026-09-20 against a measured 5750 Mi "
                                            "peak. If this is firing, either that sizing is "
                                            "wrong or host memory is leaking. If "
                                            "VLLMMemoryRetainedAboveBound is also firing, it is "
                                            "a leak, and raising the request only buys time - "
                                            "see docs/ai/vllm-onednn-sdpa-leak.md."
                                        ),
                                    },
                                }
                            ],
                        },
                        # t=125m: still within the sustained excursion (ends
                        # t=125m) - still firing.
                        {
                            "eval_time": "125m",
                            "alertname": "VLLMMemoryExceedsRequest",
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": "VLLMMemoryExceedsRequest",
                                        "severity": "warning",
                                        "namespace": "ai",
                                        "pod": REQUEST_POD,
                                        "container": "app",
                                    },
                                    "exp_annotations": {
                                        "summary": "vllm is using more memory than it reserves",
                                        "description": (
                                            f"vllm pod {REQUEST_POD} is at 150% of its memory "
                                            "REQUEST. It has re-entered the kubelet's eviction "
                                            "exceeds-set and will now be evicted before pods "
                                            "that stay within their requests. The 12Gi request "
                                            "was sized on 2026-09-20 against a measured 5750 Mi "
                                            "peak. If this is firing, either that sizing is "
                                            "wrong or host memory is leaking. If "
                                            "VLLMMemoryRetainedAboveBound is also firing, it is "
                                            "a leak, and raising the request only buys time - "
                                            "see docs/ai/vllm-onednn-sdpa-leak.md."
                                        ),
                                    },
                                }
                            ],
                        },
                        # t=130m: dropped back to 0.5 - resolves IMMEDIATELY
                        # (resolution is not gated by for:, only the onset
                        # is).
                        {"eval_time": "130m", "alertname": "VLLMMemoryExceedsRequest", "exp_alerts": []},
                    ],
                },
                {
                    "name": "retained_quiet_at_bounded_ceiling_and_through_spike",
                    "interval": "5m",
                    "input_series": [
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{RETAINED_OK_POD}", container="app"}}',
                            "values": _series(retained_ok),
                        },
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{EMBED_POD}", container="app"}}',
                            "values": _series(embed_retained_ok),
                        },
                    ],
                    "alert_rule_test": [
                        # 4h: churn only. 7h30m: 90m into the 11.5 GiB
                        # spike. 8h: just after it. 11h55m: the whole 3h
                        # window held at the full 7.3 GiB retained ceiling.
                        # None of them may fire, and vllm-embed (20 GiB
                        # floor throughout) must never appear.
                        {"eval_time": t, "alertname": retained_rule, "exp_alerts": []}
                        for t in ["4h", "7h30m", "8h", "10h", "11h55m"]
                    ],
                },
                {
                    "name": "retained_fires_on_leak_shaped_floor_climb",
                    "interval": "5m",
                    "input_series": [
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{RETAINED_LEAK_POD}", container="app"}}',
                            "values": _series(retained_leak),
                        },
                        {
                            "series": f'container_memory_working_set_bytes{{namespace="ai", pod="{EMBED_POD}", container="app"}}',
                            "values": _series(embed_retained_leak),
                        },
                    ],
                    "alert_rule_test": [
                        # 20h: floor exactly at 8.0 GiB, not above - quiet.
                        {"eval_time": "20h", "alertname": retained_rule, "exp_alerts": []},
                        # 23h50m: condition true since 23h, but only 50m of
                        # the 1h for: - still pending, must not fire yet.
                        {"eval_time": "23h50m", "alertname": retained_rule, "exp_alerts": []},
                        # 24h10m: held past for: 1h - fires, for the leaking
                        # pod only. The 3h floor is the 22h00 cache-empty
                        # sample, 5 + 0.15 * 22 = 8.3 GiB.
                        {
                            "eval_time": "24h10m",
                            "alertname": retained_rule,
                            "exp_alerts": [
                                {
                                    "exp_labels": {
                                        "alertname": retained_rule,
                                        "severity": "warning",
                                        "namespace": "ai",
                                        "pod": RETAINED_LEAK_POD,
                                        "container": "app",
                                    },
                                    "exp_annotations": {
                                        "summary": "vllm is retaining more host memory than its bounded caches can hold",
                                        "description": (
                                            f"vllm pod {RETAINED_LEAK_POD} has not dropped below "
                                            "8.3GiB of working set in the last 3h. Everything "
                                            "this server is meant to keep is bounded under "
                                            "~7.2GiB (baseline + the 4096 MiB prompt cache + one "
                                            "slot's context checkpoints), so something is growing "
                                            "without bound. First suspect: the oneDNN SDPA leak "
                                            "is back. Check that the running image still honours "
                                            "GGML_SYCL_FA_ONEDNN=0 - "
                                            "docs/ai/vllm-onednn-sdpa-leak.md."
                                        ),
                                    },
                                }
                            ],
                        },
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
    print("  - VLLMMemoryExceedsRequest fires once an excursion above the request holds for for:30m,")
    print("    resolves immediately on drop, stays quiet through a 20m transient, and excludes vllm-embed")
    print("  - the sibling vllm-embed controller never contaminates any of the three alerts")
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
