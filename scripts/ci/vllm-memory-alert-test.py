#!/usr/bin/env python3
"""VLLMMemoryRetainedAboveBound can fire on a host-RAM leak in ai/vllm.

Prevents: a chat outage from a host-RAM leak that CI cannot see. If a new
llama.cpp image renames `GGML_SYCL_FA_ONEDNN`, vllm-prompt-cache-test.py stays
green while the oneDNN partition cache leaks (~1 GiB per million prompt tokens)
toward an OOMKill. This alert is the only detector for that gap.

Checks, with `promtool test rules` on the real rule (annotations stripped):

  (a) a leak-shaped working set whose 3h floor climbs past the threshold fires,
      for that pod, only after `for: 1h`;
  (b) once the pod is gone (stale), the rule resolves at the next evaluation,
      although its samples stay inside the 3h window (the live-series AND);
  (c) the threshold sits at or above the retained ceiling (baseline +
      `--cache-ram` + one slot's context checkpoints) read from the
      HelmRelease, and below `limits.memory`, so it neither pages a healthy
      server nor fires only after the OOMKill.

Why the numbers: skill b70-llm-serving (references/memory.md "Alerts").
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
APP = ROOT / "kubernetes/apps/base/ai/vllm/app"
ALERT = "VLLMMemoryRetainedAboveBound"
GIB = 1024**3
# Measured post-load working set (2026-09-19) and one context checkpoint of
# this model's recurrent state (62.8 MiB); 32 is llama.cpp's default count.
BASELINE_MIB = 1232
CHECKPOINT_MIB = 62.8
DEFAULT_CTX_CHECKPOINTS = 32

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


def load(name: str, kind: str) -> dict:
    for doc in yaml.safe_load_all((APP / name).read_text()):
        if isinstance(doc, dict) and doc.get("kind") == kind:
            return doc
    raise SystemExit(f"FAIL: no {kind} in {name}")


def mib(quantity: str) -> float:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(Mi|Gi|Ti)", str(quantity).strip())
    if match is None:
        raise SystemExit(f"FAIL: unsupported memory quantity {quantity!r}")
    return float(match.group(1)) * {"Mi": 1, "Gi": 1024, "Ti": 1024**2}[match.group(2)]


def arg_int(args: list[str], names: tuple[str, ...], default: int | None) -> int | None:
    for name in names:
        if name in args and args.index(name) + 1 < len(args):
            raw = args[args.index(name) + 1]
            return int(raw) if re.fullmatch(r"-?\d+", raw) else None
    return default


def promtool() -> str | None:
    found = shutil.which("promtool")
    if found:
        return found
    root = Path.home() / ".local/share/mise/installs/aqua-prometheus-prometheus"
    candidates = sorted(root.glob("*/prometheus-*/promtool"), reverse=True)
    return str(candidates[0]) if candidates else None


def check_threshold(rule: dict) -> None:
    """(c) ceiling <= threshold < limit."""
    floor_clause = rule["expr"].partition("and on (namespace, pod, container)")[0]
    match = re.search(r">\s*(\d+(?:\.\d+)?)\s*\*\s*1024\s*\^\s*3", floor_clause)
    check(match is not None, f"{ALERT} floor compares against '<N> * 1024^3'")
    if match is None:
        return
    threshold_mib = float(match.group(1)) * 1024
    hr = load("helmrelease.yaml", "HelmRelease")
    container = hr["spec"]["values"]["controllers"]["vllm"]["containers"]["app"]
    args = [str(a) for a in container.get("args") or []]
    cache = arg_int(args, ("--cache-ram", "-cram"), None)
    checkpoints = arg_int(args, ("--ctx-checkpoints", "-ctxcp", "--swa-checkpoints"), DEFAULT_CTX_CHECKPOINTS)
    check(cache is not None and cache > 0 and checkpoints is not None,
          f"vllm sets a positive --cache-ram ({cache}) and a readable checkpoint count")
    if cache is None or checkpoints is None:
        return
    ceiling = BASELINE_MIB + cache + max(checkpoints, 0) * CHECKPOINT_MIB
    limit = mib(container["resources"]["limits"]["memory"])
    check(ceiling <= threshold_mib < limit,
          f"retained ceiling {ceiling:.0f} MiB <= threshold {threshold_mib:.0f} MiB < limit {limit:.0f} MiB "
          "(raise the threshold with --cache-ram)")


def series(pod: str, values: str) -> dict:
    return {"series": f'container_memory_working_set_bytes{{namespace="ai", pod="{pod}", container="app"}}',
            "values": values}


def firing(pod: str) -> list[dict]:
    return [{"exp_labels": {"alertname": ALERT, "severity": "warning",
                            "namespace": "ai", "pod": pod, "container": "app"}}]


def check_promtool(rule: dict, binary: str) -> None:
    """(a) fires on a leak; (b) quiet once the pod is gone."""
    stripped = {k: v for k, v in rule.items() if k != "annotations"}
    # Leak: floor 5 GiB + 0.15 GiB/h with 2 GiB hourly cache churn on top, 30h at 5m.
    # The 3h floor first clears 8 GiB at 23h; for: 1h moves firing past 24h.
    leak = " ".join(f"{(5.0 + 0.15 * i / 12 + 2.0 * (i % 12) / 11) * GIB:.0f}" for i in range(360))
    # Gone: 12 GiB until 4h55m, stale at 5h; its samples stay in the 3h window to 7h55m.
    gone = " ".join([f"{12 * GIB}"] * 60) + " stale"
    # vllm-embed at 20 GiB throughout must never match.
    embed = f"{20 * GIB}+0x360"
    tests = [
        {"name": "fires_on_leak", "interval": "5m",
         "input_series": [series("vllm-leak", leak), series("vllm-vllm-embed-x", embed)],
         "alert_rule_test": [
             {"eval_time": "20h", "alertname": ALERT, "exp_alerts": []},
             {"eval_time": "23h50m", "alertname": ALERT, "exp_alerts": []},
             {"eval_time": "24h10m", "alertname": ALERT, "exp_alerts": firing("vllm-leak")},
         ]},
        {"name": "quiet_once_pod_gone", "interval": "5m",
         "input_series": [series("vllm-gone", gone)],
         "alert_rule_test": [
             {"eval_time": "4h", "alertname": ALERT, "exp_alerts": firing("vllm-gone")},
             *({"eval_time": t, "alertname": ALERT, "exp_alerts": []} for t in ("5h05m", "6h", "7h30m")),
         ]},
    ]
    with tempfile.TemporaryDirectory(prefix="vllm-alert-") as tmp:
        work = Path(tmp)
        (work / "rules.yml").write_text(yaml.safe_dump(
            {"groups": [{"name": "vllm", "rules": [stripped]}]}, sort_keys=False))
        (work / "test.yml").write_text(yaml.safe_dump(
            {"rule_files": ["rules.yml"], "evaluation_interval": "5m", "tests": tests},
            sort_keys=False, width=100000))
        proc = subprocess.run([binary, "test", "rules", "test.yml"], cwd=work,
                              capture_output=True, text=True, check=False)
    check(proc.returncode == 0,
          "promtool: fires on a leak after for: 1h, never for vllm-embed, quiet once the pod is gone"
          + ("" if proc.returncode == 0 else f"\n{proc.stdout}{proc.stderr}"))


def main() -> int:
    rules = load("prometheusrule.yaml", "PrometheusRule")
    found = [r for g in rules["spec"]["groups"] for r in g.get("rules", []) if r.get("alert") == ALERT]
    check(len(found) == 1, f"{ALERT} exists once")
    if len(found) != 1:
        return 1
    check_threshold(found[0])
    binary = promtool()
    check(binary is not None, "promtool is installed (mise aqua:prometheus/prometheus)")
    if binary is not None:
        check_promtool(found[0], binary)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
