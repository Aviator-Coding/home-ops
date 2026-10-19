#!/usr/bin/env python3
"""Seerr is reachable by friends from outside the LAN (owner decision 2026-10-18).

  1. Image is v3.5.0 or newer (per-user quotas).
  2. The route is on ``envoy-external``; no secret values inline (envFrom only).
Approval and quota policy is application state (see media-stack/references/friends.md).
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("bc", Path(__file__).with_name("_books_common.py"))
bc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bc
_spec.loader.exec_module(bc)  # type: ignore[union-attr]

HR = bc.ROOT / "kubernetes/apps/base/media/seerr/app/helmrelease.yaml"


def values() -> dict:
    return bc.load_kind(HR, "HelmRelease")["spec"]["values"]


def check_version() -> None:
    tag = values()["controllers"]["seerr"]["containers"]["app"]["image"]["tag"]
    m = re.match(r"^v3\.(\d+)\.\d+", tag)
    bc.assert_true(m is not None and int(m.group(1)) >= 5, f"seerr tag {tag}")


def check_external_route() -> None:
    route = values()["route"]["app"]
    bc.assert_true({p["name"] for p in route["parentRefs"]} == {"envoy-external"}, "route must be envoy-external")
    bc.assert_true(route["hostnames"] == ["seerr.${SECRET_DOMAIN}"], "hostname")


def check_no_inline_secret() -> None:
    env = values()["controllers"]["seerr"]["containers"]["app"]["env"]
    for key in env:
        bc.assert_true(not any(w in key.upper() for w in ("TOKEN", "PASSWORD", "SECRET", "APIKEY")), f"inline secret-ish env {key}")


if __name__ == "__main__":
    sys.exit(bc.run([
        ("Seerr v3.5+", check_version),
        ("route external", check_external_route),
        ("no inline secrets", check_no_inline_secret),
    ]))
