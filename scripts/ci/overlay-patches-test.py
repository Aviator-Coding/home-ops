#!/usr/bin/env python3
"""No Flux Kustomization under kubernetes/apps/main declares spec.patches.

Prevents: a per-app patch that renders in Git but never reaches the cluster.
kubernetes/clusters/main/apps.yaml strategic-merges a defaults patch onto every
child Kustomization, and its `spec.patches` list has no merge key, so kustomize
REPLACES the child's list with the HelmRelease defaults. Found 2026-11-06: the
surrealdb SnapshotPolicies had lost their `surreal export` hooks (every backup
since 2026-09-12 crash-consistent only) and the litellm-pgvector Database CR its
`vector` extension. flate and `task flux:test:all` render the overlay as written,
so neither catches it.

Put the patch in a base kustomization the Kustomization's `path` builds
(kubernetes/apps/base/database/surrealdb/backup is the shape). Skill `flux-gitops`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APPS_MAIN = ROOT / "kubernetes" / "apps" / "main"
FLUX_KS_GROUP = "kustomize.toolkit.fluxcd.io/"


def offenders(docs_by_path: dict[Path, list]) -> list[str]:
    out = []
    for path, docs in docs_by_path.items():
        for d in docs:
            if not isinstance(d, dict) or d.get("kind") != "Kustomization":
                continue
            if not str(d.get("apiVersion", "")).startswith(FLUX_KS_GROUP):
                continue
            if "patches" in (d.get("spec") or {}):
                name = (d.get("metadata") or {}).get("name", "?")
                out.append(f"{path.relative_to(ROOT)}: Kustomization {name}")
    return out


def load_overlays() -> dict[Path, list]:
    return {p: list(yaml.safe_load_all(p.read_text())) for p in sorted(APPS_MAIN.rglob("*.yaml"))}


def main() -> int:
    failed = 0
    overlays = load_overlays()
    found = offenders(overlays)
    if found:
        failed += 1
        print("[FAIL] overlay spec.patches is replaced by the cluster-apps defaults patch and never applied:")
        for line in found:
            print(f"  {line}")
        print("  Move each patch into a base kustomization the Kustomization's path builds.")
    else:
        print(f"[PASS] no Flux Kustomization in {len(overlays)} overlay files declares spec.patches")

    probe = {APPS_MAIN / "probe.yaml": [{
        "apiVersion": "kustomize.toolkit.fluxcd.io/v1",
        "kind": "Kustomization",
        "metadata": {"name": "probe"},
        "spec": {"patches": [{"patch": "x"}]},
    }]}
    if offenders(probe):
        print("[PASS] checker refuses a probe overlay with spec.patches")
    else:
        failed += 1
        print("[FAIL] checker accepted a probe overlay with spec.patches")

    print(f"Summary: {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
