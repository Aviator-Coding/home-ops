#!/usr/bin/env python3
"""Behavioral regression for FluxResourceNotReadyTooLong.

A HelmRelease whose first install fails and retries as an upgrade has no
rollback target, so it goes Stalled (MissingRollbackTarget) with Ready=False
and emits no further events. It happened to downloads/sonarr on 2026-09-26 and
ai/litellm-pgvector on 2026-09-28, and the only signal was a one-shot Flux
Alert event. The alert keys on the live `flux_resource_info` gauge from
flux-operator (same metric and label shape as FluxResourceSuspendedTooLong,
see flux-suspended-alert-test.py), whose `ready` label is "True", "False" or
"Unknown" and whose `namespace` is always flux-system.

Loads the real PrometheusRule and evaluates it with promtool:

  - a HelmRelease and a Kustomization Ready=False for 30m fire, naming the
    object.
  - Ready=False for 29m stays pending.
  - Ready=False that recovers to True before 30m never fires.
  - a series flapping between False and Unknown with a changing reason for
    over 30m fires (a retrying release keeps its timer).
  - a suspended object (even flapping), an
    out-of-scope kind and a Ready=True object never fire.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location(
    "flux_suspended_alert_test", HERE / "flux-suspended-alert-test.py"
)
base = importlib.util.module_from_spec(_spec)
sys.modules["flux_suspended_alert_test"] = base
_spec.loader.exec_module(base)

ALERT_NAME = "FluxResourceNotReadyTooLong"
MINUTES = 60


def info(kind: str, name: str, ns: str, *, ready: str, suspended: str = "False",
         reason: str = "UpgradeFailed") -> str:
    return (
        f'flux_resource_info{{kind="{kind}", name="{name}", exported_namespace="{ns}", '
        f'ready="{ready}", suspended="{suspended}", reason="{reason}", '
        'job="flux-operator", namespace="flux-system"}'
    )


def expected(kind: str, name: str, ns: str) -> dict:
    return {
        "exp_labels": {
            "alertname": ALERT_NAME,
            "severity": "warning",
            "kind": kind,
            "name": name,
            "exported_namespace": ns,
        },
        "exp_annotations": {
            "summary": f"Flux {kind} {ns}/{name} has been not Ready for over 30m",
            "description": (
                f"{kind} {ns}/{name} has not been Ready for more than 30 minutes, "
                "including while it retries and flips between False and Unknown. "
                "A HelmRelease whose first install failed goes "
                "Stalled (MissingRollbackTarget) and Flux stops reconciling it "
                "while its Kustomization can still report Ready. Inspect it with "
                f"flux get hr -n {ns} {name} "
                "(or flux get ks for a Kustomization). If it is Stalled, once "
                f"the cause is fixed run flux reconcile hr {name} "
                f"-n {ns} --reset."
            ),
        },
    }


def contract(alert: dict) -> None:
    expr = (alert.get("expr") or "").strip()
    base.require("flux_resource_info" in expr, f"expr must key on flux_resource_info: {expr!r}")
    base.require(
        'suspended!="True"' in expr,
        f'expr must exclude suspended objects (FluxResourceSuspendedTooLong owns them): {expr!r}',
    )
    base.require(alert.get("for") == "30m", f"for must be 30m, got {alert.get('for')!r}")
    base.require(
        (alert.get("labels") or {}).get("severity") == "warning",
        "severity must be warning (routes to the root pushover-warning receiver)",
    )
    for field in ("summary", "description"):
        text = (alert.get("annotations") or {}).get(field, "")
        for lbl in ("kind", "exported_namespace", "name"):
            base.require("{{ $labels.%s }}" % lbl in text, f"{field} must name {lbl}: {text!r}")
    base.require(
        "--reset" in alert["annotations"]["description"],
        "description must say how to recover a Stalled release",
    )


def run(rule: dict) -> None:
    def vals(seq: list[str]) -> base._Q:
        return base._Q(" ".join(seq))

    always_false = vals(["1"] * MINUTES)
    recovers = [
        {"series": info("HelmRelease", "flapper", "ai", ready="False"), "values": vals(["1"] * 20)},
        {"series": info("HelmRelease", "flapper", "ai", ready="True"), "values": vals(["_"] * 20 + ["1"] * 40)},
    ]
    flap_false = vals(["1", "_"] * (MINUTES // 2))
    flap_unknown = vals(["_", "1"] * (MINUTES // 2))
    series = [
        {"series": info("HelmRelease", "retrying", "ai", ready="False", reason="UpgradeFailed"),
         "values": flap_false},
        {"series": info("HelmRelease", "retrying", "ai", ready="Unknown", reason="Progressing"),
         "values": flap_unknown},
        {"series": info("Kustomization", "retrying-ks", "media", ready="False", reason="BuildFailed"),
         "values": flap_false},
        {"series": info("Kustomization", "retrying-ks", "media", ready="Unknown", reason="Progressing"),
         "values": flap_unknown},
        {"series": info("HelmRelease", "flapping-suspended", "ai", ready="False", suspended="True"),
         "values": flap_false},
        {"series": info("HelmRelease", "flapping-suspended", "ai", ready="Unknown", suspended="True"),
         "values": flap_unknown},
        {"series": info("HelmRelease", "litellm-pgvector", "ai", ready="False"), "values": always_false},
        {"series": info("Kustomization", "broken-ks", "media", ready="False", reason="BuildFailed"),
         "values": always_false},
        {"series": info("HelmRelease", "suspended-app", "ai", ready="False", suspended="True"),
         "values": always_false},
        {"series": info("HelmRelease", "healthy", "ai", ready="True", reason="InstallSucceeded"),
         "values": always_false},
        {"series": info("OCIRepository", "src", "flux-system", ready="False", reason="Failed"),
         "values": always_false},
        {"series": info("Alert", "an-alert", "flux-system", ready="False", reason="Failed"),
         "values": always_false},
        *recovers,
    ]
    firing = [
        expected("HelmRelease", "litellm-pgvector", "ai"),
        expected("Kustomization", "broken-ks", "media"),
        expected("HelmRelease", "retrying", "ai"),
        expected("Kustomization", "retrying-ks", "media"),
    ]
    with tempfile.TemporaryDirectory(prefix="flux-not-ready-alert-") as tmp:
        work = Path(tmp)
        base._write_rule_file(work / "flux_rules.yml", rule)
        base._run_promtool(["check", "rules", "flux_rules.yml"], work)
        doc = {
            "rule_files": ["flux_rules.yml"],
            "evaluation_interval": "1m",
            "tests": [
                {
                    "name": "not_ready_duration_and_scope_matrix",
                    "interval": "1m",
                    "input_series": series,
                    "alert_rule_test": [
                        {"eval_time": "29m", "alertname": ALERT_NAME, "exp_alerts": []},
                        {"eval_time": "30m", "alertname": ALERT_NAME, "exp_alerts": firing},
                        {"eval_time": "59m", "alertname": ALERT_NAME, "exp_alerts": firing},
                    ],
                }
            ],
        }
        (work / "t.yml").write_text(base.yaml.dump(doc, sort_keys=False, width=1000))
        base._run_promtool(["test", "rules", "t.yml"], work)


def main() -> int:
    rule = base.prometheus_rule(base.FLUX_INSTANCE_RULE)
    alerts = base.alerts_by_name(rule)
    base.require(ALERT_NAME in alerts, f"missing alert {ALERT_NAME}")
    print("==> structural contract")
    contract(alerts[ALERT_NAME])
    print("==> promtool check + unit-test matrix")
    run(rule)
    print(f"PASS: {ALERT_NAME} semantics hold")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except base.Failure as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
