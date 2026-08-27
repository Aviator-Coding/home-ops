#!/usr/bin/env python3
"""Semantic regression test for the Intel GPU node Grafana dashboard.

Intent: ship a node-level GPU dashboard for the Arc B70 (talos-3,
devic.es/b70) and fleet iGPUs (gpu.intel.com/xe) from metrics that already
exist in Prometheus - no new exporter workload - following the
agentgateway-dashboards house pattern and avoiding Flux postBuild.substitute
collision risk.

This test does not grep source as a proxy for behavior. It:
  1. Renders kubernetes/apps/base/ai/gpu-node-dashboard/app via kubectl
     kustomize and loads the resulting objects as a structured model.
  2. Renders kubernetes/apps/main/ai and inspects the gpu-node-dashboard
     Flux Kustomization contract.
  3. Parses the dashboard JSON out of the generated ConfigMap and asserts
     panel semantics (required hwmon + device-plugin PromQL, no invented
     exporter metrics, no per-engine/VRAM-util/clock panels).
  4. Asserts the delivery surface: grafana_dashboard label, AI/ML folder,
     no postBuild.substituteFrom, and only a ConfigMap (no new workload).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP_PATH = ROOT / "kubernetes/apps/base/ai/gpu-node-dashboard/app"
MAIN_AI_PATH = ROOT / "kubernetes/apps/main/ai"

EXPECTED_CM_NAME = "gpu-node-dashboard"
EXPECTED_FOLDER = "AI/ML"
EXPECTED_UID = "ai-gpu-node"
EXPECTED_TITLE_SUBSTR = "GPU Node"
EXPECTED_FLUX_PATH = "./kubernetes/apps/base/ai/gpu-node-dashboard/app"
EXPECTED_CHIP = "0000:02:01_0_0000:03:00_0"
EXPECTED_INSTANCE = "talos-3"

# Metrics the captain confirmed exist live on this cluster.
REQUIRED_EXPR_FRAGMENTS = (
    "node_hwmon_energy_joule_total",
    "node_hwmon_temp_celsius",
    "node_hwmon_fan_rpm",
    "node_hwmon_power_cap_watt",
    "node_hwmon_power_crit_watt",
    "kube_node_status_allocatable",
    "kube_pod_container_resource_requests",
    "devic_es_b70",
    "gpu_intel_com_xe",
    EXPECTED_INSTANCE,
    EXPECTED_CHIP,
)

# Invented / other-vendor exporter families that must not appear.
FORBIDDEN_EXPR_FRAGMENTS = (
    "DCGM_",
    "intel_gpu_top",
    "xpu_smi",
    "xpu-smi",
    "level_zero",
    "rocm_",
    "nvidia_smi",
    "nvidia_gpu",
    "GPU_UTIL",
    "gpu_engine_busy",
    "igd_busy",
)

# Panel titles that would claim signals the cluster does not export.
FORBIDDEN_TITLE_FRAGMENTS = (
    "engine busy",
    "per-engine",
    "vram util",
    "vram usage",
    "clock",
    "sm %",
    "graphics engine",
    "video enhance",
)


class Failure(Exception):
    pass


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
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    if not docs:
        raise Failure(f"kubectl kustomize {path} produced no documents")
    return docs


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def walk_panels(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for panel in panels:
        out.append(panel)
        nested = panel.get("panels") or []
        if nested:
            out.extend(walk_panels(nested))
    return out


def all_exprs(panels: list[dict[str, Any]]) -> list[str]:
    exprs: list[str] = []
    for panel in panels:
        for target in panel.get("targets") or []:
            expr = target.get("expr")
            if expr:
                exprs.append(expr)
    return exprs


def assert_configmap(docs: list[dict[str, Any]]) -> dict[str, Any]:
    cms = [
        d
        for d in docs
        if d.get("kind") == "ConfigMap"
        and d.get("metadata", {}).get("name") == EXPECTED_CM_NAME
    ]
    require(len(cms) == 1, f"expected exactly one ConfigMap {EXPECTED_CM_NAME}, got {len(cms)}")
    cm = cms[0]
    md = cm.get("metadata") or {}
    labels = md.get("labels") or {}
    annotations = md.get("annotations") or {}
    require(
        labels.get("grafana_dashboard") == "true",
        f"grafana_dashboard label must be 'true', got {labels.get('grafana_dashboard')!r}",
    )
    require(
        annotations.get("grafana_folder") == EXPECTED_FOLDER,
        f"grafana_folder must be {EXPECTED_FOLDER!r}, got {annotations.get('grafana_folder')!r}",
    )
    # Delivery must be dashboard-only: no Deployments/Services/CronJobs etc.
    non_cm = [f"{d.get('kind')}/{d.get('metadata', {}).get('name')}" for d in docs if d.get("kind") != "ConfigMap"]
    require(not non_cm, f"gpu-node-dashboard must not introduce workloads, found {non_cm}")
    data = cm.get("data") or {}
    require("gpu-node.json" in data, "ConfigMap missing gpu-node.json key")
    return cm


def assert_dashboard(cm: dict[str, Any]) -> dict[str, Any]:
    raw = cm["data"]["gpu-node.json"]
    try:
        dash = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Failure(f"gpu-node.json is not valid JSON: {exc}") from exc

    require(isinstance(dash, dict), "dashboard root must be an object")
    require(dash.get("uid") == EXPECTED_UID, f"uid must be {EXPECTED_UID!r}, got {dash.get('uid')!r}")
    title = dash.get("title") or ""
    require(EXPECTED_TITLE_SUBSTR in title, f"title must contain {EXPECTED_TITLE_SUBSTR!r}, got {title!r}")
    tags = set(dash.get("tags") or [])
    require({"ai", "gpu", "intel"}.issubset(tags), f"tags must include ai/gpu/intel, got {sorted(tags)}")

    panels = walk_panels(dash.get("panels") or [])
    require(panels, "dashboard has no panels")
    content_panels = [p for p in panels if p.get("type") != "row"]
    require(len(content_panels) >= 7, f"expected >=7 content panels, got {len(content_panels)}")

    rows = [p.get("title", "") for p in panels if p.get("type") == "row"]
    require(any("B70" in r for r in rows), f"missing Arc B70 row, rows={rows}")
    require(any("Allocation" in r or "Device-Plugin" in r for r in rows), f"missing allocation row, rows={rows}")

    titles = [(p.get("title") or "").lower() for p in content_panels]
    for title_l in titles:
        for bad in FORBIDDEN_TITLE_FRAGMENTS:
            require(bad not in title_l, f"panel title claims unavailable signal {bad!r}: {title_l!r}")

    exprs = all_exprs(panels)
    require(exprs, "no PromQL expressions found on any panel")
    blob = "\n".join(exprs)
    missing = [frag for frag in REQUIRED_EXPR_FRAGMENTS if frag not in blob]
    require(not missing, f"dashboard PromQL missing required fragments: {missing}")
    forbidden = [frag for frag in FORBIDDEN_EXPR_FRAGMENTS if frag in blob]
    require(not forbidden, f"dashboard PromQL includes forbidden exporter metrics: {forbidden}")

    # Resource names must be the kube-state-metrics sanitized forms.
    require(
        any("devic_es_b70" in e for e in exprs),
        "missing devic_es_b70 device-plugin series (B70 slots)",
    )
    require(
        any("gpu_intel_com_xe" in e for e in exprs),
        "missing gpu_intel_com_xe device-plugin series (iGPU fleet)",
    )

    # Power draw must be derived from energy counters (no direct power gauge).
    power_exprs = [e for e in exprs if "node_hwmon_energy_joule_total" in e]
    require(power_exprs, "missing energy-counter power panels")
    require(
        all("rate(" in e for e in power_exprs),
        "energy counters must be rate()'d to live watts",
    )

    # Datasource must resolve via Prometheus uid used elsewhere in the cluster.
    for panel in content_panels:
        ds = panel.get("datasource")
        if isinstance(ds, dict):
            require(
                ds.get("uid") == "prometheus" or ds.get("type") == "prometheus",
                f"panel {panel.get('title')!r} datasource must be prometheus, got {ds!r}",
            )

    # Avoid Flux strict envsubst collisions: no bare ${VAR} tokens in the CM payload.
    # Grafana's $__rate_interval is fine; Flux only collides on ${...}.
    flux_tokens = sorted(set(re.findall(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", raw)))
    require(not flux_tokens, f"ConfigMap contains Flux-colliding ${'{'}...{'}'} tokens: {flux_tokens}")

    return dash


def assert_flux_kustomization(docs: list[dict[str, Any]]) -> dict[str, Any]:
    ks = [
        d
        for d in docs
        if d.get("kind") == "Kustomization"
        and str(d.get("apiVersion", "")).startswith("kustomize.toolkit.fluxcd.io/")
        and d.get("metadata", {}).get("name") == EXPECTED_CM_NAME
    ]
    require(len(ks) == 1, f"expected Flux Kustomization {EXPECTED_CM_NAME}, found {len(ks)}")
    k = ks[0]
    md = k.get("metadata") or {}
    spec = k.get("spec") or {}
    require(md.get("namespace") == "ai", f"Flux KS namespace must be ai, got {md.get('namespace')!r}")
    require(spec.get("targetNamespace") == "ai", f"targetNamespace must be ai, got {spec.get('targetNamespace')!r}")
    require(spec.get("path") == EXPECTED_FLUX_PATH, f"path must be {EXPECTED_FLUX_PATH!r}, got {spec.get('path')!r}")
    require("postBuild" not in spec, "Flux KS must not set postBuild (substitute collision avoidance)")
    src = spec.get("sourceRef") or {}
    require(src.get("kind") == "GitRepository", f"sourceRef.kind must be GitRepository, got {src.get('kind')!r}")
    require(src.get("name") == "flux-system", f"sourceRef.name must be flux-system, got {src.get('name')!r}")
    labels = (spec.get("commonMetadata") or {}).get("labels") or {}
    require(
        labels.get("app.kubernetes.io/name") == EXPECTED_CM_NAME,
        f"commonMetadata label mismatch: {labels}",
    )
    return k


def main() -> int:
    print("==> kustomize build gpu-node-dashboard/app")
    app_docs = kustomize_build(APP_PATH)
    print(f"    documents: {len(app_docs)}")

    print("==> assert ConfigMap delivery contract")
    cm = assert_configmap(app_docs)
    print(f"    ConfigMap {EXPECTED_CM_NAME} OK (folder={EXPECTED_FOLDER})")

    print("==> assert dashboard JSON semantics")
    dash = assert_dashboard(cm)
    panels = [p for p in walk_panels(dash.get("panels") or []) if p.get("type") != "row"]
    print(f"    title={dash.get('title')!r} uid={dash.get('uid')!r} content_panels={len(panels)}")

    print("==> kustomize build apps/main/ai")
    main_docs = kustomize_build(MAIN_AI_PATH)
    print(f"    documents: {len(main_docs)}")

    print("==> assert Flux Kustomization contract")
    assert_flux_kustomization(main_docs)
    print("    Flux Kustomization OK (no postBuild)")

    print("PASS: gpu-node-dashboard satisfies Intel GPU node dashboard contracts")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
