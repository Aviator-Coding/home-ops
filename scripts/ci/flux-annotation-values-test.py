#!/usr/bin/env python3
"""Guard: every kustomize.toolkit.fluxcd.io/* annotation carries a value Flux honours.

Measured 2026-09-28 on ai/litellm-pgvector: its db-init Job carried
`kustomize.toolkit.fluxcd.io/force: "true"`. kustomize-controller (v1.9.4,
api/v1/kustomization_types.go) only acts on the exact value `enabled`, so the
annotation was a silent no-op - there is no warning for an unrecognised value.
The first change to the Job's (immutable) spec then failed Flux's dry-run with
"field is immutable" and the Kustomization stayed Ready=False, which is the one
situation the annotation exists for. The same dead value had been sitting in
ai/litellm/app/dbinit.yaml since the operator migration, never exercised.

Neither `flate` nor `kustomize build` can see this: an annotation value is an
opaque string to both. So this walks every YAML document under kubernetes/,
finds each of these annotation keys at ANY depth (top-level metadata, Flux
`commonMetadata`, HelmRelease values that render manifests), and requires the
value to be one kustomize-controller actually compares against:

  force       enabled                  (EnabledValue)
  prune       disabled                 (DisabledValue)
  reconcile   disabled                 (DisabledValue)
  substitute  disabled                 (DisabledValue)
  ssa         Ignore | IfNotPresent | Merge

Case matters: the controller compares strings exactly.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
KUBERNETES = ROOT / "kubernetes"
PREFIX = "kustomize.toolkit.fluxcd.io/"

ALLOWED: dict[str, frozenset[str]] = {
    "force": frozenset({"enabled"}),
    "prune": frozenset({"disabled"}),
    "reconcile": frozenset({"disabled"}),
    "substitute": frozenset({"disabled"}),
    "ssa": frozenset({"Ignore", "IfNotPresent", "Merge"}),
}


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def annotation_maps(node: Any) -> Iterator[dict[str, Any]]:
    """Every `annotations:` mapping anywhere in a parsed document."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "annotations" and isinstance(value, dict):
                yield value
            yield from annotation_maps(value)
    elif isinstance(node, list):
        for item in node:
            yield from annotation_maps(item)


def violations(doc: Any) -> list[str]:
    bad = []
    for annotations in annotation_maps(doc):
        for key, value in annotations.items():
            if not isinstance(key, str) or not key.startswith(PREFIX):
                continue
            name = key.removeprefix(PREFIX)
            if name not in ALLOWED:
                continue
            if not isinstance(value, str) or value not in ALLOWED[name]:
                bad.append(f"{key}: {value!r} (Flux honours only {sorted(ALLOWED[name])})")
    return bad


def repo_documents() -> Iterator[tuple[Path, Any]]:
    for path in sorted(KUBERNETES.rglob("*.y*ml")):
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except yaml.YAMLError:
            continue  # not plain YAML (templated); check-yaml owns syntax
        for doc in docs:
            if doc is not None:
                yield path, doc


def test_every_flux_annotation_value_is_honoured() -> dict[str, Any]:
    found: list[str] = []
    checked = 0
    for path, doc in repo_documents():
        for annotations in annotation_maps(doc):
            checked += sum(1 for k in annotations if isinstance(k, str) and k.startswith(PREFIX))
        found += [f"{path.relative_to(ROOT)}: {v}" for v in violations(doc)]
    require(checked > 0, "found no kustomize.toolkit.fluxcd.io annotations at all - walker is broken")
    require(not found, "unhonoured Flux annotation values:\n  " + "\n  ".join(found))
    return {"annotations_checked": checked}


def test_checker_refuses_the_measured_defect() -> dict[str, Any]:
    job = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": "x", "annotations": {PREFIX + "force": "true"}},
    }
    require(violations(job), 'checker accepted force: "true"')
    nested = {"spec": {"commonMetadata": {"annotations": {PREFIX + "ssa": "ifnotpresent"}}}}
    require(violations(nested), "checker accepted a wrong-case ssa value in commonMetadata")
    good = {"metadata": {"annotations": {PREFIX + "force": "enabled", PREFIX + "ssa": "IfNotPresent"}}}
    require(not violations(good), "checker refused honoured values")
    return {"refused": ['force: "true"', "ssa: ifnotpresent"]}


def main() -> int:
    tests = [
        test_every_flux_annotation_value_is_honoured,
        test_checker_refuses_the_measured_defect,
    ]
    failed = 0
    for test in tests:
        try:
            evidence = test()
        except Failure as exc:
            failed += 1
            print(f"[FAIL] {test.__name__}: {exc}")
        else:
            print(f"[PASS] {test.__name__} {evidence}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
