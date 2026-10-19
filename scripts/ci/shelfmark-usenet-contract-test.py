#!/usr/bin/env python3
"""Shelfmark (book downloader) must run the upstream lite image, Usenet only, in `downloads`.

  1. Image ``ghcr.io/calibrain/shelfmark-lite`` pinned ``v1.4.0@sha256:...``.
  2. SABnzbd + Prowlarr (Usenet client) + external FlareSolverr wired by env.
  3. No torrent client env; API keys only via ``shelfmark-secret`` (never inline).
  4. ``shared-downloads`` mounted at ``/data/downloads`` (same path SABnzbd uses).
  5. Read-only root has writable mounts for /tmp and the log dir.
  6. Hardened container; internal route only.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("bc", Path(__file__).with_name("_books_common.py"))
bc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bc
_spec.loader.exec_module(bc)  # type: ignore[union-attr]

APP = bc.ROOT / "kubernetes/apps/base/downloads/shelfmark/app"
FORBIDDEN_ENV = ("TORRENT", "QBITTORRENT", "DELUGE", "NZBGET", "TRANSMISSION")


def release() -> dict:
    return bc.load_kind(APP / "helmrelease.yaml", "HelmRelease")


def container() -> dict:
    ctrl = release()["spec"]["values"]["controllers"]["shelfmark"]
    return ctrl["containers"]["app"]


def check_image() -> None:
    img = container()["image"]
    bc.assert_true(img["repository"] == "ghcr.io/calibrain/shelfmark-lite", f"image {img['repository']}")
    bc.assert_true(img["tag"].startswith("v1.4.0@sha256:") and bc.DIGEST_TAG.match(img["tag"]), "tag must be v1.4.0@sha256:<64 hex>")


def env_map() -> dict:
    raw = container()["env"]
    return {e["name"]: e["value"] for e in raw} if isinstance(raw, list) else raw


def check_env() -> None:
    env = env_map()
    for key in ("PUID", "PGID"):
        bc.assert_true(str(env.get(key)) == "2000", f"{key} must be 2000")
    want = {
        "SABNZBD_URL": "http://sabnzbd.downloads.svc.cluster.local:8080",
        "SABNZBD_CATEGORY": "books",
        "PROWLARR_ENABLED": "true",
        "PROWLARR_URL": "http://prowlarr.downloads.svc.cluster.local:9696",
        "PROWLARR_USENET_CLIENT": "sabnzbd",
        "USING_EXTERNAL_BYPASSER": "true",
        "EXT_BYPASSER_URL": "http://flaresolverr.network.svc.cluster.local:8191",
        "INGEST_DIR": "/cwa-book-ingest",
    }
    for key, value in want.items():
        bc.assert_true(str(env.get(key)) == value, f"env {key}={env.get(key)!r}, want {value!r}")


def check_no_torrent_and_secrets() -> None:
    env = env_map()
    for key in env:
        bc.assert_true(not any(f in key.upper() for f in FORBIDDEN_ENV), f"forbidden env key {key}")
    for key in ("SABNZBD_API_KEY", "PROWLARR_API_KEY"):
        bc.assert_true(key not in env, f"{key} must not be inline in env")
    refs = [e.get("secretRef", {}).get("name") for e in container().get("envFrom", [])]
    bc.assert_true("shelfmark-secret" in refs, "envFrom must reference shelfmark-secret")
    es = bc.load_kind(APP / "externalsecret.yaml", "ExternalSecret")
    keys = {d["secretKey"] for d in es["spec"]["data"]}
    bc.assert_true({"SABNZBD_API_KEY", "PROWLARR_API_KEY"} <= keys, f"ExternalSecret keys {keys}")
    bc.assert_true(es["spec"]["target"]["name"] == "shelfmark-secret", "target name")


def check_mounts() -> None:
    persistence = release()["spec"]["values"]["persistence"]
    dl = persistence.get("downloads", {})
    bc.assert_true(dl.get("existingClaim") == "shared-downloads", "downloads must use existingClaim shared-downloads")
    paths = {m["path"] for m in dl.get("globalMounts", [])}
    bc.assert_true("/data/downloads" in paths, "shared-downloads must mount at /data/downloads")
    mounted = {m["path"] for p in persistence.values() for m in p.get("globalMounts", [])}
    for need in ("/tmp", "/var/log/shelfmark", "/config", "/cwa-book-ingest"):
        bc.assert_true(need in mounted, f"missing writable mount {need}")
    bc.assert_true(all("existingClaim" not in p or p["existingClaim"] == "shared-downloads" for p in persistence.values()) and "config" not in persistence, "env-only config: /config is scratch, not a claim")


def check_hardening_and_route() -> None:
    sc = container()["securityContext"]
    bc.assert_true(sc.get("readOnlyRootFilesystem") is True, "readOnlyRootFilesystem")
    bc.assert_true(sc.get("allowPrivilegeEscalation") is False, "allowPrivilegeEscalation")
    bc.assert_true(sc.get("capabilities", {}).get("drop") == ["ALL"], "drop ALL")
    probes = container()["probes"]
    for kind in ("liveness", "readiness"):
        path = probes[kind]["spec"]["httpGet"]["path"]
        bc.assert_true(path == "/api/health", f"{kind} probe path {path}, upstream healthcheck is /api/health")
    route = release()["spec"]["values"]["route"]["app"]
    bc.assert_true("/api/health" in route["annotations"]["gatus.home-operations.com/endpoint"], "gatus endpoint path")
    names = {p["name"] for p in route["parentRefs"]}
    bc.assert_true(names == {"envoy-internal"}, f"route parentRefs {names}")
    bc.assert_true(route["hostnames"] == ["books.${SECRET_DOMAIN}"], "hostname")


if __name__ == "__main__":
    sys.exit(bc.run([
        ("pinned upstream lite image", check_image),
        ("Usenet + bypasser env", check_env),
        ("no torrent env; keys via secret only", check_no_torrent_and_secrets),
        ("shared-downloads and writable mounts", check_mounts),
        ("hardened, internal route", check_hardening_and_route),
    ]))
