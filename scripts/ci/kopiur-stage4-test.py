#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 4 (matter-server root mover).

Stage 4 onboards `home-automation/matter-server`. It runs as root by design
(0:0:0) and the volume holds Matter fabric credentials that a uid-1000 mover
cannot read (measured 2026-08-31: five 0600 0:0 files). The route is explicit
`KOPIUR_PUID/PGID: 0` on the existing component substitutions - no shared
component change - plus the namespace-wide privileged-mover annotation,
applied through GitOps on the overlay that actually produces the Namespace.

This test pins the matter-server GitOps contract (root identity, annotation,
sibling blast radius), not the live Snapshot. Live proof (MoverPermitted gate,
first ceph Snapshot, restore sha256) is a post-merge gate recorded in the PR.

`selfhosted/changedetection-config` was onboarded separately (#1512) at
1000:1000; this test does not require it to stay off kopiur.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
HA_OVERLAY = REPO / "kubernetes" / "apps" / "main" / "home-automation"
MATTER_KS = HA_OVERLAY / "matter-server.yaml"
HA_KUSTOMIZATION = HA_OVERLAY / "kustomization.yaml"
PRIVILEGED_ANN = "kopiur.home-operations.com/privileged-movers"

SIBLING_IDENTITIES: dict[str, tuple[str, str]] = {
    "esphome": ("2000", "2000"),
    "home-assistant": ("1000", "1000"),
    "zigbee2mqtt": ("2000", "2000"),
}


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_multi(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def matter_ks() -> dict[str, Any]:
    docs = [d for d in load_multi(MATTER_KS) if d.get("kind") == "Kustomization"]
    require(len(docs) == 1, f"expected 1 Flux Kustomization in {MATTER_KS}")
    return docs[0]


def substitute(d: dict[str, Any]) -> dict[str, str]:
    return {
        str(k): str(v)
        for k, v in (((d.get("spec") or {}).get("postBuild") or {}).get("substitute") or {}).items()
    }


def test_matter_server_is_onboarded() -> None:
    d = matter_ks()
    spec = d.get("spec") or {}
    comps = [c for c in (spec.get("components") or []) if isinstance(c, str)]
    require(
        any(c.rstrip("/").endswith("components/kopiur") for c in comps),
        "matter-server must include components/kopiur alongside volsync",
    )
    require(
        any(c.rstrip("/").endswith("components/volsync") for c in comps),
        "matter-server must keep components/volsync - Stage 5 retires it, not Stage 4",
    )
    deps = {(x.get("name"), x.get("namespace")) for x in (spec.get("dependsOn") or [])}
    require(
        ("kopiur-repository", "system") in deps,
        f"matter-server must dependOn kopiur-repository/system, got {sorted(deps)}",
    )
    require(spec.get("wait") is not True, "matter-server must not set wait: true")


def test_root_mover_is_explicit_substitutions() -> None:
    sub = substitute(matter_ks())
    require(
        sub.get("KOPIUR_PUID") == "0" and sub.get("KOPIUR_PGID") == "0",
        f"matter-server mover identity must be explicit 0:0, got "
        f"PUID={sub.get('KOPIUR_PUID')!r} PGID={sub.get('KOPIUR_PGID')!r}",
    )
    spec = yaml.dump(matter_ks().get("spec") or {})
    require(
        "inheritSecurityContextFrom" not in spec,
        "Stage 4 uses KOPIUR_PUID/PGID substitutions so Restore gets the same "
        "identity; do not put inheritSecurityContextFrom on this claim without "
        "also changing the shared Restore",
    )


def test_r2_hour_matches_namespace() -> None:
    sub = substitute(matter_ks())
    require(
        sub.get("KOPIUR_SCHEDULE_R2") == "H 10 * * *",
        f"matter-server r2 must stay on the home-automation hour, got "
        f"{sub.get('KOPIUR_SCHEDULE_R2')!r}",
    )
    require(
        "KOPIUR_SCHEDULE_CEPH" not in sub,
        "ceph must stay at the component default (structurally offset)",
    )


def test_privileged_mover_annotation_is_gitops() -> None:
    """The annotation must land on the Namespace cluster-apps actually applies.

    kubernetes/apps/base/home-automation/namespace.yaml is not in any Flux
    inventory. The live object comes from components/common via this overlay.
    """
    exe = shutil.which("kustomize")
    cmd = [exe, "build", str(HA_OVERLAY)] if exe else None
    if cmd is None:
        kubectl = shutil.which("kubectl")
        if not kubectl:
            raise Failure("neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(HA_OVERLAY)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Failure(f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()}")
    namespaces = [
        d
        for d in yaml.safe_load_all(proc.stdout)
        if d and d.get("kind") == "Namespace"
    ]
    require(len(namespaces) == 1, f"expected 1 Namespace in overlay build, got {len(namespaces)}")
    ns = namespaces[0]
    require(ns.get("metadata", {}).get("name") == "home-automation", ns.get("metadata"))
    anns = ns.get("metadata", {}).get("annotations") or {}
    require(
        anns.get(PRIVILEGED_ANN) == "true",
        f"home-automation Namespace must carry {PRIVILEGED_ANN}=true via GitOps, got {anns}",
    )
    overlay_text = HA_KUSTOMIZATION.read_text()
    require(
        "patches:" in overlay_text
        and (
            PRIVILEGED_ANN in overlay_text
            or "kopiur.home-operations.com~1privileged-movers" in overlay_text
        ),
        "the annotation must be a kustomize patch on the overlay, not a hand-applied kubectl annotate",
    )
    require(
        "name: not-used" in overlay_text,
        "the patch target must be the common component's pre-transform name (not-used)",
    )


def test_siblings_keep_non_root_identities() -> None:
    """Granting the privilege must not silently change the other three movers."""
    for app, want in SIBLING_IDENTITIES.items():
        path = HA_OVERLAY / f"{app}.yaml"
        docs = [d for d in load_multi(path) if d.get("kind") == "Kustomization"]
        require(docs, f"missing Flux Kustomization {path}")
        sub = substitute(docs[0])
        got = (sub.get("KOPIUR_PUID", "1000"), sub.get("KOPIUR_PGID", "1000"))
        require(
            got == want,
            f"{app} mover identity drifted to {got}, expected {want} - the "
            f"privileged-movers annotation is a gate, not an identity change",
        )


def test_changedetection_not_required_deferred() -> None:
    """#1512 already onboarded changedetection; Stage 4 must not re-defer it.

    Presence on kopiur is allowed. This only guards against this PR clobbering
    the sibling overlay into a non-Kustomization or deleting it.
    """
    cd = REPO / "kubernetes" / "apps" / "main" / "selfhosted" / "changedetection.yaml"
    require(cd.is_file(), f"missing {cd}")
    docs = [d for d in load_multi(cd) if d.get("kind") == "Kustomization"]
    require(docs, f"missing Flux Kustomization in {cd}")


def main() -> int:
    tests: list[str] = []
    failures: list[str] = []

    def run(name: str, fn: Any) -> None:
        tests.append(name)
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as e:
            failures.append(name)
            print(f"[FAIL] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append(name)
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")

    run("matter_server_is_onboarded", test_matter_server_is_onboarded)
    run("root_mover_is_explicit_substitutions", test_root_mover_is_explicit_substitutions)
    run("r2_hour_matches_namespace", test_r2_hour_matches_namespace)
    run("privileged_mover_annotation_is_gitops", test_privileged_mover_annotation_is_gitops)
    run("siblings_keep_non_root_identities", test_siblings_keep_non_root_identities)
    run("changedetection_not_required_deferred", test_changedetection_not_required_deferred)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
