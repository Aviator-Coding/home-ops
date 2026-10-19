#!/usr/bin/env python3
"""Wizarr (friend invites) is a born-kopiur app with an invite-only external route.

  1. Image ``ghcr.io/wizarrrr/wizarr`` pinned ``v2026.10.x@sha256:...``.
  2. Hardened pod, claim created by the app itself (ceph-block RWO), kopiur only.
  3. Admin host is internal only; the external join host exposes only the invite flow.
  4. Overlay registered, kopiur schedule in the free media hour, identity recorded.
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

APP = bc.ROOT / "kubernetes/apps/base/media/wizarr/app"
OVERLAY = bc.ROOT / "kubernetes/apps/main/media/wizarr.yaml"
# Paths the invite flow needs; admin (/, /admin, /settings, /plex/scan, ...) must not be here.
JOIN_PATHS = {"/j", "/join", "/invitation", "/wizard", "/static", "/image-proxy",
              "/cinema-posters", "/reset", "/favicon.ico", "/plex/callback"}
FORBIDDEN_PREFIXES = {"/", "/admin", "/settings", "/plex", "/api", "/login", "/health"}


def release() -> dict:
    return bc.load_kind(APP / "helmrelease.yaml", "HelmRelease")["spec"]["values"]


def check_image_and_hardening() -> None:
    ctrl = release()["controllers"]["wizarr"]
    img = ctrl["containers"]["app"]["image"]
    bc.assert_true(img["repository"] == "ghcr.io/wizarrrr/wizarr", "repository")
    bc.assert_true(re.match(r"^v2026\.10\.\d+@sha256:[0-9a-f]{64}$", img["tag"]) is not None, f"tag {img['tag'][:24]}")
    sec = ctrl["containers"]["app"]["securityContext"]
    bc.assert_true(sec.get("readOnlyRootFilesystem") is True and sec.get("allowPrivilegeEscalation") is False, "container hardening")
    bc.assert_true(sec.get("capabilities", {}).get("drop") == ["ALL"], "drop ALL")
    bc.assert_true(ctrl["pod"]["securityContext"].get("runAsNonRoot") is True, "runAsNonRoot")
    envs = {e["name"]: e["value"] for e in ctrl["containers"]["app"]["env"]} if isinstance(ctrl["containers"]["app"]["env"], list) else ctrl["containers"]["app"]["env"]
    bc.assert_true(str(envs.get("PORT")) == "5690", "PORT 5690")


def check_born_kopiur() -> None:
    cfg = release()["persistence"]["config"]
    bc.assert_true(cfg.get("type") == "persistentVolumeClaim" and cfg.get("storageClass") == "ceph-block"
                   and cfg.get("accessMode") == "ReadWriteOnce", "claim must be a ceph-block RWO PVC owned by the app")
    ks = bc.load_kind(OVERLAY, "Kustomization")["spec"]
    comps = ks.get("components", [])
    bc.assert_true(any(c.endswith("components/kopiur") for c in comps), "components/kopiur missing")
    bc.assert_true(not any(c.endswith("components/kopiur/pvc") for c in comps), "born-kopiur must not use kopiur/pvc")
    sub = ks["postBuild"]["substitute"]
    bc.assert_true("KOPIUR_CLAIM" not in sub, "no KOPIUR_CLAIM for a born-kopiur app")
    bc.assert_true(sub.get("KOPIUR_SCHEDULE_R2") == "H 15 * * *", "media namespace r2 hour is H 15")
    deps = {(d["name"], d["namespace"]) for d in ks["dependsOn"]}
    bc.assert_true({("kopiur-repository", "system"), ("onepassword-store", "security")} <= deps, f"dependsOn {deps}")


def check_registered() -> None:
    kz = bc.load_kind(bc.ROOT / "kubernetes/apps/main/media/kustomization.yaml", "Kustomization")
    bc.assert_true("./wizarr.yaml" in kz["resources"], "wizarr.yaml not registered")
    text = (bc.ROOT / "scripts/ci/kopiur-stage3-test.py").read_text()
    bc.assert_true(text.count('("media", "wizarr")') >= 2, "(media, wizarr) must be in both stage3 registries")


def check_routes() -> None:
    routes = release()["route"]
    admin = routes["admin"]
    bc.assert_true({p["name"] for p in admin["parentRefs"]} == {"envoy-internal"}, "admin route must be envoy-internal only")
    join = routes["join"]
    bc.assert_true({p["name"] for p in join["parentRefs"]} == {"envoy-external"}, "join route must be envoy-external")
    bc.assert_true(join["hostnames"] == ["join.${SECRET_DOMAIN}"], "join hostname")
    paths = set()
    for rule in join["rules"]:
        for m in rule["matches"]:
            paths.add(m["path"]["value"])
    bc.assert_true(paths == JOIN_PATHS, f"join paths {sorted(paths)}")
    bc.assert_true(not (paths & FORBIDDEN_PREFIXES), "admin paths exposed on the external route")


def check_no_inline_secret() -> None:
    text = (APP / "helmrelease.yaml").read_text().lower()
    for word in ("token", "apikey", "password"):
        bc.assert_true(f"{word}:" not in text, f"inline {word} in HelmRelease")


if __name__ == "__main__":
    sys.exit(bc.run([
        ("pinned image and hardening", check_image_and_hardening),
        ("born-kopiur claim and overlay", check_born_kopiur),
        ("registered in media and kopiur registry", check_registered),
        ("admin internal, join external allow-list", check_routes),
        ("no inline secrets", check_no_inline_secret),
    ]))
