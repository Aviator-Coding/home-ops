#!/usr/bin/env python3
"""Contract test for the Cleanuparr deployment in the downloads namespace.

Cleanuparr removes and blocklists stalled/failed queue items so the *arr apps
re-search. It stores API keys for Radarr, Sonarr, Lidarr and SABnzbd in its own
database, so the invariants are about blast radius and honest configuration:

  1. Hardened pod: non-root, read-only rootfs, no privilege escalation, all
     capabilities dropped, image tag pinned (never ``latest``).
  2. Settings live in the app (UI/REST API), backed up by kopiur: Git sets no
     env vars Cleanuparr ignores and injects no secret.
  3. Reachable from the internal gateway only, never ``envoy-external``.
  4. The Flux Kustomization waits for ``kopiur-repository`` and is registered
     in ``kubernetes/apps/main/downloads/kustomization.yaml``.

It parses YAML into structures; it does not grep source strings.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/downloads/cleanuparr/app"
WRAPPER = ROOT / "kubernetes/apps/main/downloads/cleanuparr.yaml"
MAIN_KUSTOMIZATION = ROOT / "kubernetes/apps/main/downloads/kustomization.yaml"

DEAD_ENV_PREFIXES = ("QUEUECLEANER__", "RADARR__", "SONARR__", "LIDARR__", "DOWNLOAD_CLIENT")


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_yaml(path: Path) -> dict[str, Any]:
    assert_true(path.is_file(), f"missing {path.relative_to(ROOT)}")
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    assert_true(len(docs) >= 1, f"{path.relative_to(ROOT)} is empty")
    return docs[0]


def test_workload() -> None:
    release = load_yaml(APP / "helmrelease.yaml")
    assert_true(release["spec"]["chartRef"]["name"] == "app-template", "must use the app-template chartRef")
    controller = release["spec"]["values"]["controllers"]["cleanuparr"]

    pod_ctx = controller["pod"]["securityContext"]
    assert_true(pod_ctx["runAsNonRoot"] is True, "pod must run as non-root")

    container = controller["containers"]["app"]
    tag = str(container["image"]["tag"])
    assert_true(bool(tag) and not tag.startswith("latest"), "image tag must be pinned, not latest")

    ctx = container["securityContext"]
    assert_true(ctx["readOnlyRootFilesystem"] is True, "rootfs must be read-only")
    assert_true(ctx["allowPrivilegeEscalation"] is False, "privilege escalation must be off")
    assert_true(ctx["capabilities"]["drop"] == ["ALL"], "all capabilities must be dropped")

    literal_keys = [
        name for name, value in (container.get("env") or {}).items() if "KEY" in name.upper() and isinstance(value, str)
    ]
    assert_true(not literal_keys, f"API keys must not be literal env values: {literal_keys}")


def test_route_is_internal_only() -> None:
    release = load_yaml(APP / "helmrelease.yaml")
    routes = release["spec"]["values"].get("route") or {}
    assert_true(bool(routes), "Cleanuparr UI needs an internal route")
    for name, route in routes.items():
        gateways = {ref["name"] for ref in route["parentRefs"]}
        assert_true(gateways == {"envoy-internal"}, f"route {name} must attach to envoy-internal only, got {gateways}")


def test_settings_live_in_the_app() -> None:
    # Cleanuparr 2.10 stores instances, clients and rules in its own database (UI/REST API)
    # and ignores these env vars, so Git must not pretend to define them.
    release = load_yaml(APP / "helmrelease.yaml")
    container = release["spec"]["values"]["controllers"]["cleanuparr"]["containers"]["app"]
    dead = [name for name in (container.get("env") or {}) if name.startswith(DEAD_ENV_PREFIXES)]
    assert_true(not dead, f"env vars Cleanuparr ignores must not be set: {dead}")
    assert_true(not container.get("envFrom"), "no secret is injected: API keys are stored in the app")
    assert_true(not (APP / "externalsecret.yaml").exists(), "unused ExternalSecret must be removed")


def test_flux_wiring() -> None:
    wrapper = load_yaml(WRAPPER)
    deps = {dep["name"] for dep in wrapper["spec"]["dependsOn"]}
    assert_true("kopiur-repository" in deps, "Flux wrapper must depend on kopiur-repository for its backups")
    assert_true("onepassword-store" not in deps, "no secret is used, so no onepassword-store dependency")
    assert_true(
        wrapper["spec"]["path"] == "./kubernetes/apps/base/downloads/cleanuparr/app",
        "Flux wrapper must point at the cleanuparr app directory",
    )
    registered = load_yaml(MAIN_KUSTOMIZATION)["resources"]
    assert_true("./cleanuparr.yaml" in registered, "cleanuparr.yaml not registered in main/downloads")


def main() -> int:
    for check in (test_workload, test_route_is_internal_only, test_settings_live_in_the_app, test_flux_wiring):
        check()
    print("cleanuparr-contract-test: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
