#!/usr/bin/env python3
"""Calibre-Web-Automated pinned to 4.0.8 (OPDS, Kobo, KOReader sync built in), LAN-only.

  1. Image tag ``V4.0.8@sha256:...``.
  2. Ingest/library NFS mounts, NETWORK_SHARE_MODE and the config claim unchanged.
  3. Route hostname ``calibre.${SECRET_DOMAIN}`` on ``envoy-internal`` only.
  4. No reader credentials in the ExternalSecret.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("bc", Path(__file__).with_name("_books_common.py"))
bc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bc
_spec.loader.exec_module(bc)  # type: ignore[union-attr]

BASE = bc.ROOT / "kubernetes/apps/base/media/calibre/calibre-web-automated"


def release() -> dict:
    return bc.load_kind(BASE / "helmrelease.yaml", "HelmRelease")["spec"]["values"]


def check_version() -> None:
    img = release()["controllers"]["calibre-web-automated"]["containers"]["app"]["image"]
    bc.assert_true(img["repository"] == "ghcr.io/crocodilestick/calibre-web-automated", "repository")
    bc.assert_true(img["tag"].startswith("V4.0.8@sha256:") and bc.DIGEST_TAG.match(img["tag"]), f"tag {img['tag'][:20]}")


def check_mounts() -> None:
    v = release()
    raw = v["controllers"]["calibre-web-automated"]["containers"]["app"]["env"]
    env = {e["name"]: e["value"] for e in raw} if isinstance(raw, list) else raw
    bc.assert_true(str(env.get("NETWORK_SHARE_MODE")).lower() == "true", "NETWORK_SHARE_MODE")
    mounted = {m["path"] for p in v["persistence"].values() for m in p.get("globalMounts", [])}
    for need in ("/cwa-book-ingest", "/calibre-library"):
        bc.assert_true(need in mounted, f"missing mount {need}")


def check_route_internal() -> None:
    route = release()["route"]["app"]
    bc.assert_true(route["hostnames"] == ["calibre.${SECRET_DOMAIN}"], f"hostnames {route['hostnames']}")
    names = {p["name"] for p in route["parentRefs"]}
    bc.assert_true(names == {"envoy-internal"}, f"parentRefs {names}")


def check_no_reader_secrets() -> None:
    es = bc.load_kind(BASE / "externalsecret.yaml", "ExternalSecret")
    text = str(es).lower()
    for bad in ("kobo", "koreader", "opds"):
        bc.assert_true(bad not in text, f"reader credential key {bad} in ExternalSecret")


if __name__ == "__main__":
    sys.exit(bc.run([
        ("CWA pinned to 4.0.8", check_version),
        ("ingest/library mounts kept", check_mounts),
        ("route internal only", check_route_internal),
        ("no reader credentials in Git", check_no_reader_secrets),
    ]))
