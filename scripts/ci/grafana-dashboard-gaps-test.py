#!/usr/bin/env python3
"""Grafana home dashboard, Ceph/LiteLLM boards, and retired TrueNAS boards.

The landing page 500s when GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH points
at /tmp/dashboards/home.json, because that env var overrides grafana.ini and
the sidecar directory has no home.json. The chart already writes the landing
dashboard to the ini path.

Ceph Usage and Ceph Hosts Overview, plus the LiteLLM ops and routing boards,
are sidecar ConfigMaps. Their ${...} tokens are Grafana variables, so each
ConfigMap must opt out of Flux postBuild substitution. UniFi boards stay on
the Helm download list and must also rewrite the DS_UNIFI_POLLER input.
The three TrueNAS boards are gone: they queried graphite names this
Prometheus does not have.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAFANA_APP = ROOT / "kubernetes/apps/base/monitoring/grafana/app"
HELMRELEASE = GRAFANA_APP / "helmrelease.yaml"
MONITORING_MAIN = ROOT / "kubernetes/apps/main/monitoring"
TRUENAS_DIR = (
    ROOT
    / "kubernetes/apps/base/monitoring/exporters/graphite-exporter/dashboard/truenas-scale"
)
HOME_PATH = "/var/lib/grafana/dashboards/default/home-dashboard.json"
UNIFI_KEYS = ("unifi-insights", "unifi-network-sites", "unifi-usw", "unifi-uap")

EXPECTED = {
    "ceph-usage-dashboard": ("Storage", "ceph-usage.json", "ceph-usage"),
    "ceph-hosts-overview-dashboard": (
        "Storage",
        "ceph-hosts-overview.json",
        "ceph-hosts-overview",
    ),
    "litellm-dashboard": ("AI/ML", "litellm.json", "litellm"),
    "litellm-routing-dashboard": ("AI/ML", "litellm-routing.json", "llm-routing"),
}


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def kustomize_build(path: Path) -> list[dict[str, Any]]:
    proc = subprocess.run(
        ["kubectl", "kustomize", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise Failure(
            f"kubectl kustomize {path} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def dashboard_json(cm: dict[str, Any], key: str) -> dict[str, Any]:
    raw = (cm.get("data") or {}).get(key)
    require(isinstance(raw, str) and raw.strip().startswith("{"), f"{key} missing from {cm['metadata']['name']}")
    return json.loads(raw)


def assert_helmrelease() -> None:
    hr = yaml.safe_load(HELMRELEASE.read_text())
    values = hr["spec"]["values"]
    env = values.get("env") or {}
    require(
        "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH" not in env,
        "GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH overrides grafana.ini and 500s the home page",
    )
    ini_path = ((values.get("grafana.ini") or {}).get("dashboards") or {}).get(
        "default_home_dashboard_path"
    )
    require(ini_path == HOME_PATH, f"home dashboard path must be {HOME_PATH}, got {ini_path!r}")
    home = values["dashboards"]["default"]["home-dashboard"]["json"]
    for link in ("/d/litellm", "/d/llm-routing", "/d/ceph-usage", "/d/ceph-hosts-overview"):
        require(link in home, f"home dashboard missing link {link}")
    monitoring = values["dashboards"]["monitoring"]
    for key in UNIFI_KEYS:
        names = {item["name"]: item["value"] for item in monitoring[key]["datasource"]}
        require(
            names.get("DS_UNIFI_POLLER") == "Prometheus",
            f"{key} must map DS_UNIFI_POLLER to Prometheus, got {names}",
        )
    blob = HELMRELEASE.read_text()
    require("gnetId: 23027" not in blob, "PDU dashboard 23027 has no outlet metrics here")


def assert_configmaps(docs: list[dict[str, Any]]) -> None:
    by_name = {
        d["metadata"]["name"]: d
        for d in docs
        if d.get("kind") == "ConfigMap"
    }
    for name, (folder, key, uid) in EXPECTED.items():
        cm = by_name.get(name)
        require(cm is not None, f"missing ConfigMap {name}")
        assert cm is not None
        ann = cm["metadata"].get("annotations") or {}
        labels = cm["metadata"].get("labels") or {}
        require(
            ann.get("kustomize.toolkit.fluxcd.io/substitute") == "disabled",
            f"{name} must disable Flux substitution",
        )
        require(ann.get("grafana_folder") == folder, f"{name} folder must be {folder}")
        require(labels.get("grafana_dashboard") == "true", f"{name} missing grafana_dashboard label")
        dash = dashboard_json(cm, key)
        require(dash.get("uid") == uid, f"{name} uid must be {uid}, got {dash.get('uid')!r}")
        text = json.dumps(dash)
        require("prometheus-main" not in text, f"{name} still points at prometheus-main")
        require("unpoller_device_outlet" not in text, f"{name} queries a PDU metric this cluster lacks")
        require("${DS_VICTORIAMETRICS}" not in text, f"{name} still uses the VictoriaMetrics input")
        current_values = []
        for var in (dash.get("templating") or {}).get("list") or []:
            if var.get("type") == "datasource":
                current_values.append((var.get("current") or {}).get("value"))
        require(
            "prometheus" in current_values,
            f"{name} datasource variable must default to uid prometheus, got {current_values}",
        )


def assert_truenas_retired(docs: list[dict[str, Any]]) -> None:
    require(not TRUENAS_DIR.exists(), f"TrueNAS dashboard directory still present: {TRUENAS_DIR}")
    names = [d.get("metadata", {}).get("name") for d in docs]
    require(
        "graphite-exporter-dashboard" not in names,
        "graphite-exporter-dashboard Flux Kustomization must be removed",
    )
    for doc in docs:
        if doc.get("kind") != "ConfigMap":
            continue
        require(
            "truenas" not in doc["metadata"]["name"],
            f"TrueNAS ConfigMap still rendered: {doc['metadata']['name']}",
        )


def main() -> int:
    print("==> assert Grafana HelmRelease home path and UniFi inputs")
    assert_helmrelease()
    print("==> kustomize build grafana app")
    app_docs = kustomize_build(GRAFANA_APP)
    print(f"    documents: {len(app_docs)}")
    assert_configmaps(app_docs)
    print("==> kustomize build monitoring overlay")
    main_docs = kustomize_build(MONITORING_MAIN)
    assert_truenas_retired(main_docs)
    print("PASS: grafana home path, Ceph/LiteLLM boards, and TrueNAS retirement")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
