#!/usr/bin/env python3
"""Guards the TimescaleDB extension settings on cluster-18.

The extension is preloaded (shared_preload_libraries) and telemetry is off. The
image itself is built in the separate containers repo.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CLUSTER = ROOT / "kubernetes/apps/base/database/cloudnative-pg/cluster-18/cluster-18.yaml"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def cluster_spec() -> dict[str, Any]:
    docs = [d for d in yaml.safe_load_all(CLUSTER.read_text()) if isinstance(d, dict)]
    clusters = [d for d in docs if d.get("kind") == "Cluster"]
    require(len(clusters) == 1, f"{CLUSTER.relative_to(ROOT)}: expected one Cluster, found {len(clusters)}")
    return clusters[0]["spec"]


def test_extension_is_preloaded_with_telemetry_off() -> dict[str, Any]:
    pg = cluster_spec()["postgresql"]
    require(
        "timescaledb" in (pg.get("shared_preload_libraries") or []),
        "cluster-18: timescaledb must be in postgresql.shared_preload_libraries",
    )
    require(
        (pg.get("parameters") or {}).get("timescaledb.telemetry_level") == "off",
        "cluster-18: set parameters timescaledb.telemetry_level to off",
    )
    return {}


TESTS = [test_extension_is_preloaded_with_telemetry_off]


def main() -> int:
    failures = 0
    for test in TESTS:
        try:
            detail = test()
            print(f"[PASS] {test.__name__} {detail}")
        except Failure as exc:
            failures += 1
            print(f"[FAIL] {test.__name__}: {exc}")
    print(f"Summary: {len(TESTS) - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
