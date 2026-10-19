#!/usr/bin/env python3
"""Shelfmark lives in `downloads` (its RWX claim is there); the media copy is gone."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("bc", Path(__file__).with_name("_books_common.py"))
bc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bc
_spec.loader.exec_module(bc)  # type: ignore[union-attr]

MAIN = bc.ROOT / "kubernetes/apps/main"


def check_wrapper() -> None:
    ks = bc.load_kind(MAIN / "downloads/shelfmark.yaml", "Kustomization")
    bc.assert_true(ks["metadata"]["namespace"] == "downloads", "KS namespace")
    bc.assert_true(ks["spec"]["targetNamespace"] == "downloads", "targetNamespace")
    deps = {(d["name"], d.get("namespace")) for d in ks["spec"].get("dependsOn", [])}
    bc.assert_true(("downloads-pvc", "downloads") in deps, f"dependsOn downloads-pvc, got {deps}")
    bc.assert_true(ks["spec"]["path"] == "./kubernetes/apps/base/downloads/shelfmark/app", "path")


def check_registered() -> None:
    kz = bc.load_kind(MAIN / "downloads/kustomization.yaml", "Kustomization")
    bc.assert_true("./shelfmark.yaml" in kz["resources"], "shelfmark.yaml not registered")


def check_media_copy_gone() -> None:
    names = [d["metadata"]["name"] for d in bc.load_all(MAIN / "media/calibre.yaml")]
    bc.assert_true("calibre-downloader" not in names, "calibre-downloader KS still in media")
    old = bc.ROOT / "kubernetes/apps/base/media/calibre/calibre-downloader"
    bc.assert_true(not old.exists(), "calibre-downloader dir still exists")


if __name__ == "__main__":
    sys.exit(bc.run([
        ("Flux KS in downloads, after downloads-pvc", check_wrapper),
        ("registered in downloads kustomization", check_registered),
        ("media calibre-downloader removed", check_media_copy_gone),
    ]))
