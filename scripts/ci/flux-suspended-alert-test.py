#!/usr/bin/env python3
"""Behavioral regression for FluxResourceSuspendedTooLong.

Pins the 2026-09-25 fix for a real 19-day blind spot: the Flux Kustomizations
system-controller/k8tz and downloads/readarr were suspended on 2026-09-06 for
a live test and never resumed. For 19 days they reported Ready=True while
applying a stale revision and ignoring every merge to main, and nothing in
the repo's alerting or CI caught it.

The metric this alert keys on was established live against the cluster on
2026-09-25, not assumed from docs: kube-state-metrics has no
`customResourceState` config for any Flux CRD (verified against its
HelmRelease - `kubernetes/apps/base/monitoring/kube-state-metrics/app/helmrelease.yaml`
sets only `prometheus.monitor.enabled`/`selfMonitor.enabled`, nothing custom),
so it exposes nothing for Flux objects. Querying the live Prometheus
(`kube-prometheus-stack-prometheus`) for `gotk_*`/`flux*` series found no
`gotk_reconcile_condition` family at all, but a live `flux_resource_info`
gauge, emitted by flux-operator itself (`serviceMonitor.create: true` on the
flux-operator HelmRelease, job label "flux-operator") - one series per
Flux-managed object, carrying a `suspended` label ("True"/"False") that
flips to a brand-new time series the moment `spec.suspend` changes, plus
`kind`/`name`/`exported_namespace`/`ready`. `namespace` on this metric is
flux-operator's OWN namespace (flux-system, always), not the watched
object's namespace - confirmed live: HelmRelease vllm reports
namespace="flux-system", exported_namespace="ai". That is the only live
source, and this
test loads the real PrometheusRule Flux would apply and pins the alert to
that exact metric and label shape rather than to text in a doc.

This test does NOT grep source text as evidence. It:

  1. Loads the real PrometheusRule Flux would apply and asserts its
     structural contract (metric name, `suspended="True"` filter, the kind
     regex covering every in-scope Flux kind - Kustomization, HelmRelease,
     GitRepository, OCIRepository, HelmRepository, HelmChart, Bucket - the
     24h `for:`, and severity).
  2. Feeds it to Prometheus' own rule unit-test engine (`promtool test
     rules`) with synthetic `flux_resource_info` series modeling:
       - a Kustomization suspended for longer than 24h (the k8tz/readarr
         shape) - fires, and names the object via its labels.
       - a HelmRelease suspended for under 24h - stays pending, never fires.
       - an object that was suspended and then resumed before 24h elapsed
         (label flips to suspended="False", a new series in promtool's
         model) - never fires, proving a real resume clears the alert
         rather than needing a manual unsuspend of an old series.
       - an Alert object (flux-operator/notification-controller kind, which
         CAN be suspended but is explicitly out of this alert's scope)
         suspended the entire window - never fires, proving the kind regex
         is enforced and not just documentation.
       - a GitRepository, OCIRepository, HelmRepository and HelmChart each
         suspended past 24h - each fires, proving every declared source kind
         is actually covered by the expression, not just claimed in a
         comment.

promtool is resolved the same way as the other promtool-based tests in this
directory (native aqua install preferred; podman image fallback).
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
FLUX_INSTANCE_RULE = (
    ROOT / "kubernetes/apps/base/flux-system/flux-instance/app/prometheusrule.yaml"
)

PROMTOOL_IMAGE = os.environ.get(
    "PROMTOOL_IMAGE", "quay.io/prometheus/prometheus:v3.2.1"
)

ALERT_NAME = "FluxResourceSuspendedTooLong"

IN_SCOPE_KINDS = (
    "Kustomization",
    "HelmRelease",
    "GitRepository",
    "OCIRepository",
    "HelmRepository",
    "HelmChart",
    "Bucket",
)

# 24h threshold from the captain's intent ("a warning once suspension exceeds
# about 24h"). Synthetic timeline runs well past it so both the pending and
# firing halves of `for: 24h` are exercised.
THRESHOLD_H = 24
HOURS = 40


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
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=run_cwd)
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        raise Failure(
            f"promtool {' '.join(args)} failed (exit {proc.returncode}):\n{out.strip()}"
        )
    return out.strip() or "SUCCESS"


def _write_rule_file(path: Path, rule: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump({"groups": rule["spec"]["groups"]}, sort_keys=False))


class _Q(str):
    pass


def _represent_q(dumper: yaml.Dumper, data: str) -> Any:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')


yaml.add_representer(_Q, _represent_q)


def _series(values: list[float]) -> _Q:
    return _Q(" ".join(f"{v:.0f}" for v in values))


def assert_rule_contract(alert: dict[str, Any]) -> dict[str, Any]:
    expr = (alert.get("expr") or "").strip()
    require(
        "flux_resource_info" in expr,
        f"{ALERT_NAME}: expr must key on flux_resource_info - the only live "
        f"metric that exposes Flux object suspension (kube-state-metrics has "
        f"no customResourceState config for any Flux CRD); got {expr!r}",
    )
    require(
        'suspended="True"' in expr,
        f'{ALERT_NAME}: expr must filter suspended="True"; got {expr!r}',
    )
    for kind in IN_SCOPE_KINDS:
        require(
            kind in expr,
            f"{ALERT_NAME}: expr must cover Flux kind {kind!r}; got {expr!r}",
        )
    for out_of_scope in ("Alert", "Provider", "Receiver"):
        require(
            f'"{out_of_scope}"' not in expr and f"|{out_of_scope}|" not in expr,
            f"{ALERT_NAME}: expr must not accidentally widen to notification-"
            f"controller kind {out_of_scope!r}, which this alert does not cover",
        )
    require(
        alert.get("for") == f"{THRESHOLD_H}h",
        f"{ALERT_NAME}: for: must be {THRESHOLD_H}h (captain's intent: "
        f"\"a warning once suspension exceeds about 24h\"); got {alert.get('for')!r}",
    )
    labels = alert.get("labels") or {}
    require(
        labels.get("severity") == "warning",
        f"{ALERT_NAME}: severity must be warning (a human must act, but this "
        f"is not an outage); got {labels!r}",
    )
    annotations = alert.get("annotations") or {}
    for field in ("summary", "description"):
        text = annotations.get(field, "")
        require(
            "{{ $labels.kind }}" in text
            and "{{ $labels.exported_namespace }}" in text
            and "{{ $labels.name }}" in text,
            f"{ALERT_NAME}: {field} must name the suspended object via "
            f"$labels.kind/$labels.exported_namespace/$labels.name; got {text!r}",
        )
    return {"expr": expr, "for": alert.get("for"), "severity": labels.get("severity")}


def _flux_resource_info_series(
    *, kind: str, name: str, exported_namespace: str, suspended: str
) -> str:
    # `namespace` is always flux-operator's own namespace on this metric -
    # constant here for realism, never used to identify the object.
    return (
        "flux_resource_info{"
        f'kind="{kind}", name="{name}", exported_namespace="{exported_namespace}", '
        f'suspended="{suspended}", ready="True", job="flux-operator", '
        'namespace="flux-system"'
        "}"
    )


def _expected_alert(*, kind: str, name: str, exported_namespace: str) -> dict[str, Any]:
    """The alert promtool must produce for a suspended object with these
    identity labels - built once so every eval point in the matrix compares
    against the identical shape rather than a hand-duplicated literal."""
    return {
        "exp_labels": {
            "alertname": ALERT_NAME,
            "severity": "warning",
            "kind": kind,
            "name": name,
            "namespace": "flux-system",
            "exported_namespace": exported_namespace,
            "ready": "True",
            "suspended": "True",
            "job": "flux-operator",
        },
        "exp_annotations": {
            "summary": f"Flux {kind} {exported_namespace}/{name} has been suspended for over 24h",
            "description": (
                f"{kind} {exported_namespace}/{name} has had spec.suspend: true for "
                "more than 24 hours. A suspended object stops reconciling "
                "and keeps applying whatever revision it last saw while "
                "still reporting Ready=True, silently ignoring every merge "
                "to main - this is exactly how system-controller/k8tz and "
                "downloads/readarr ran stale for 19 days in 2026-09 with "
                "nothing surfacing it. If the suspension was only meant to "
                "be temporary, resume it (flux resume, or kubectl patch "
                "spec.suspend=false)."
            ),
        },
    }


def assert_promtool_semantics(alert: dict[str, Any], rule: dict[str, Any]) -> dict[str, Any]:
    # Present continuously suspended="True" for the whole HOURS window - the
    # real k8tz/readarr shape (suspended once, never resumed).
    always_suspended = _series([1] * HOURS)

    # Suspended for 10h only, well under the 24h threshold, then absent
    # entirely (as if the object were deleted/replaced) - never long enough
    # to fire.
    under_threshold = _series([1] * 10)

    source_kinds = ("GitRepository", "OCIRepository", "HelmRepository", "HelmChart", "Bucket")

    input_series = [
        {
            "series": _flux_resource_info_series(
                kind="Kustomization", name="k8tz", exported_namespace="system-controller", suspended="True"
            ),
            "values": always_suspended,
        },
        {
            "series": _flux_resource_info_series(
                kind="HelmRelease", name="under-threshold-app", exported_namespace="downloads", suspended="True"
            ),
            "values": under_threshold,
        },
        {
            "series": _flux_resource_info_series(
                kind="Alert", name="out-of-scope-alert", exported_namespace="flux-system", suspended="True"
            ),
            "values": always_suspended,
        },
    ]
    for kind in source_kinds:
        input_series.append(
            {
                "series": _flux_resource_info_series(
                    kind=kind, name=f"{kind.lower()}-suspended", exported_namespace="flux-system", suspended="True"
                ),
                "values": always_suspended,
            }
        )

    # All of these series start suspended at t=0, so they all cross the
    # for:24h threshold at the same eval point.
    expected_at_24h = [_expected_alert(kind="Kustomization", name="k8tz", exported_namespace="system-controller")] + [
        _expected_alert(kind=kind, name=f"{kind.lower()}-suspended", exported_namespace="flux-system")
        for kind in source_kinds
    ]
    expected_at_end = expected_at_24h

    with tempfile.TemporaryDirectory(prefix="flux-suspended-alert-promtool-") as tmp:
        work = Path(tmp)
        _write_rule_file(work / "flux_rules.yml", rule)
        check_out = _run_promtool(["check", "rules", "flux_rules.yml"], work)

        test_doc = {
            "rule_files": ["flux_rules.yml"],
            "evaluation_interval": "1h",
            "tests": [
                {
                    "name": "suspension_duration_and_kind_matrix",
                    "interval": "1h",
                    "input_series": input_series,
                    "alert_rule_test": [
                        # t=23h: the always-suspended Kustomization has been
                        # suspended 23h - one short of for:24h - must still
                        # be pending, not firing.
                        {"eval_time": "23h", "alertname": ALERT_NAME, "exp_alerts": []},
                        # t=24h: exactly for:24h held continuously - fires,
                        # and names the object via its labels.
                        {"eval_time": "24h", "alertname": ALERT_NAME, "exp_alerts": expected_at_24h},
                        # t=39h (HOURS-1): the 10h-suspended HelmRelease never
                        # reached 24h and its series vanished after t=10h -
                        # never fires. Every other declared source kind, each
                        # suspended past 24h, does fire alongside k8tz.
                        {
                            "eval_time": f"{HOURS - 1}h",
                            "alertname": ALERT_NAME,
                            "exp_alerts": expected_at_end,
                        },
                    ],
                },
            ],
        }

        test_path = work / "flux_suspended_test.yml"
        test_path.write_text(yaml.dump(test_doc, sort_keys=False, width=1000))
        test_out = _run_promtool(["test", "rules", test_path.name], work)

    return {
        "check": check_out.splitlines()[-1] if check_out else "SUCCESS",
        "test": "PASS",
        "test_out_tail": test_out[-300:],
    }


def assert_resumed_and_out_of_scope_never_fire(rule: dict[str, Any]) -> None:
    """Separate promtool run isolating the two negative cases so a failure
    here names exactly which guarantee broke, rather than getting lost in
    the combined matrix's exp_alerts diff."""
    always_suspended = [1] * HOURS
    resumed_true_half = [1] * 10
    resumed_false_half = [1] * (HOURS - 10)

    with tempfile.TemporaryDirectory(prefix="flux-suspended-alert-negatives-") as tmp:
        work = Path(tmp)
        _write_rule_file(work / "flux_rules.yml", rule)

        test_doc = {
            "rule_files": ["flux_rules.yml"],
            "evaluation_interval": "1h",
            "tests": [
                {
                    "name": "genuine_resume_before_threshold_never_fires",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": _flux_resource_info_series(
                                kind="Kustomization",
                                name="readarr",
                                exported_namespace="downloads",
                                suspended="True",
                            ),
                            "values": _series(resumed_true_half),
                        },
                        {
                            "series": _flux_resource_info_series(
                                kind="Kustomization",
                                name="readarr",
                                exported_namespace="downloads",
                                suspended="False",
                            ),
                            "values": _series(resumed_false_half),
                        },
                    ],
                    "alert_rule_test": [
                        {"eval_time": t, "alertname": ALERT_NAME, "exp_alerts": []}
                        for t in ["9h", "10h", "20h", f"{HOURS - 1}h"]
                    ],
                },
                {
                    "name": "out_of_scope_kind_never_fires_even_when_suspended_forever",
                    "interval": "1h",
                    "input_series": [
                        {
                            "series": _flux_resource_info_series(
                                kind="Alert",
                                name="out-of-scope-alert",
                                exported_namespace="flux-system",
                                suspended="True",
                            ),
                            "values": _series(always_suspended),
                        },
                    ],
                    "alert_rule_test": [
                        {"eval_time": t, "alertname": ALERT_NAME, "exp_alerts": []}
                        for t in ["24h", "30h", f"{HOURS - 1}h"]
                    ],
                },
            ],
        }
        test_path = work / "flux_suspended_negatives_test.yml"
        test_path.write_text(yaml.dump(test_doc, sort_keys=False, width=1000))
        _run_promtool(["test", "rules", test_path.name], work)


def main() -> int:
    print("==> load FluxResourceSuspendedTooLong from live PrometheusRule")
    rule = prometheus_rule(FLUX_INSTANCE_RULE)
    alerts = alerts_by_name(rule)
    require(ALERT_NAME in alerts, f"missing alert {ALERT_NAME}")
    alert = alerts[ALERT_NAME]
    print(f"    found {ALERT_NAME}")

    print("==> structural contract (metric, suspended filter, kind coverage, for:, severity)")
    contract = assert_rule_contract(alert)
    print(f"    OK for={contract['for']} severity={contract['severity']}")
    print(f"    expr={contract['expr']!r}")

    print("==> promtool check + unit-test duration/kind matrix")
    semantics = assert_promtool_semantics(alert, rule)
    print(f"    OK check={semantics['check']!r} test={semantics['test']}")

    print("==> promtool negative-case isolation (resume clears it, out-of-scope kind never fires)")
    assert_resumed_and_out_of_scope_never_fire(rule)
    print("    OK")

    print("PASS: FluxResourceSuspendedTooLong semantics hold")
    print("covered:")
    print("  - a Kustomization suspended for exactly for:24h fires, naming kind/namespace/name")
    print("  - suspended for 23h (one short of the threshold) stays pending, never fires")
    print("  - suspended for 10h then genuinely resumed (label flips to suspended=\"False\") never fires")
    print("  - GitRepository, OCIRepository, HelmRepository, HelmChart and Bucket each fire past 24h")
    print("  - an out-of-scope Alert object suspended forever never fires (kind regex enforced)")
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
