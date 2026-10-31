#!/usr/bin/env python3
"""FalkorDB keeps its graph: no eviction, a write ceiling below the cgroup limit, AOF on, a kept claim.

Prevents: silent data loss and an outage of database/falkordb.

  1. No evicting `--maxmemory-policy`. The graph is a single Redis key, so any eviction
     policy deletes the whole graph at the ceiling instead of refusing writes.
  2. `--maxmemory` is set and sits strictly below `limits.memory`. At or above the limit
     the kernel OOM-kills first, so the clean write refusal becomes a crash loop with a
     long AOF replay on every start.
  3. `--appendonly yes`. kopiur snapshots the claim with no application hook; on the
     image's stock RDB-only settings one mid-write snapshot restored 200 of 2199 nodes
     and the restored pod still started clean.
  4. The data claim keeps `retain: true`, so a Helm uninstall (failed-install
     remediation, a deleted HelmRelease) leaves the volume in place.

Relationships, not values: the 12gb / 16Gi numbers can move together without editing
this file. The claim name is pinned fleet-wide by kopiur-stage3-test.py
(`test_every_covered_claim_is_created`). Why each rule exists: skill databases,
references/falkordb.md.

Run: python3 scripts/ci/falkordb-durability-test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
HELMRELEASE = REPO / "kubernetes/apps/base/database/falkordb/app/helmrelease.yaml"

_K8S_MEM_SUFFIX = {
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
    "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
}
# Redis's own size grammar, not the Kubernetes one: `gb` is 1024^3 and `g` is 10^9
# (redis util.c memtoull).
_REDIS_MEM_SUFFIX = {
    "b": 1, "k": 10**3, "kb": 1024, "m": 10**6, "mb": 1024**2,
    "g": 10**9, "gb": 1024**3,
}


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def parse_mem(value: object, suffixes: dict[str, int], fold_case: bool) -> int | None:
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([A-Za-z]*)", value.strip())
    if not m:
        return None
    suffix = m.group(2).lower() if fold_case else m.group(2)
    if suffix and suffix not in suffixes:
        return None
    return int(float(m.group(1)) * (suffixes[suffix] if suffix else 1))


def values() -> dict[str, Any]:
    docs = [d for d in yaml.safe_load_all(HELMRELEASE.read_text()) if d]
    hr = next((d for d in docs if d.get("kind") == "HelmRelease"), None)
    require(hr is not None, f"{HELMRELEASE.relative_to(REPO)} has no HelmRelease")
    return (hr.get("spec") or {}).get("values") or {}


def database_container(vals: dict[str, Any]) -> dict[str, Any]:
    app = ((((vals.get("controllers") or {}).get("falkordb") or {}).get("containers") or {}).get("app")) or {}
    require(app, "controllers.falkordb.containers.app is missing")
    return app


def command(app: dict[str, Any]) -> str:
    cmd = " ".join(str(c) for c in (app.get("command") or []))
    require("redis-server" in cmd, "the database container command no longer runs redis-server")
    return cmd


def test_no_eviction_policy(vals: dict[str, Any]) -> None:
    cmd = command(database_container(vals))
    policies = re.findall(r"--maxmemory-policy\s+(\S+)", cmd)
    require(
        all(p == "noeviction" for p in policies),
        f"--maxmemory-policy {policies}: the graph is one key, any eviction policy deletes it",
    )


def test_ceiling_below_limit(vals: dict[str, Any]) -> None:
    app = database_container(vals)
    cmd = command(app)
    found = re.search(r"--maxmemory\s+(\S+)", cmd)
    require(found, "--maxmemory is missing: writes would never refuse before the kernel OOM-kills")
    ceiling = parse_mem(found.group(1), _REDIS_MEM_SUFFIX, fold_case=True)
    limit_s = ((app.get("resources") or {}).get("limits") or {}).get("memory")
    limit = parse_mem(limit_s, _K8S_MEM_SUFFIX, fold_case=False)
    require(ceiling and ceiling > 0, f"--maxmemory {found.group(1)!r} does not parse to a positive size")
    require(limit, f"limits.memory {limit_s!r} is missing or unparseable")
    require(
        ceiling < limit,
        f"--maxmemory {found.group(1)} ({ceiling} B) must sit below limits.memory {limit_s} ({limit} B)",
    )


def test_aof_on(vals: dict[str, Any]) -> None:
    cmd = command(database_container(vals))
    settings = re.findall(r"--appendonly\s+(\S+)", cmd)
    require(settings and all(s == "yes" for s in settings), f"--appendonly must be yes, got {settings}")


def test_claim_is_retained(vals: dict[str, Any]) -> None:
    data = (vals.get("persistence") or {}).get("data") or {}
    require(data.get("type") == "persistentVolumeClaim", "persistence.data must stay the chart-owned claim")
    require(not data.get("existingClaim"), "persistence.data is the claim app-template creates, not an existingClaim")
    require(data.get("retain") is True, "persistence.data must keep retain: true so a Helm uninstall keeps the volume")


def main() -> int:
    try:
        vals = values()
    except (Failure, OSError, yaml.YAMLError) as exc:
        print(f"[FAIL] load: {exc}")
        return 1
    failures = 0
    for name, fn in (
        ("no_eviction_policy", test_no_eviction_policy),
        ("ceiling_below_limit", test_ceiling_below_limit),
        ("aof_on", test_aof_on),
        ("claim_is_retained", test_claim_is_retained),
    ):
        try:
            fn(vals)
            print(f"[PASS] {name}")
        except Failure as exc:
            failures += 1
            print(f"[FAIL] {name}: {exc}")
    print(f"Summary: {4 - failures} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
