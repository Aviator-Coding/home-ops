#!/usr/bin/env python3
"""Readarr and reading-glasses are gone from Git (owner: app removed, data kept).

Kopiur snapshots are retained by the policy; the NAS ebooks and Postgres databases are never
touched by Git. The fleet-pin and retirement records must drop the readarr-config claim.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("bc", Path(__file__).with_name("_books_common.py"))
bc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bc
_spec.loader.exec_module(bc)  # type: ignore[union-attr]

ROOT = bc.ROOT


def check_dirs_gone() -> None:
    for rel in ("kubernetes/apps/base/downloads/readarr", "kubernetes/apps/base/downloads/reading-glasses",
                "kubernetes/apps/main/downloads/readarr.yaml", "kubernetes/apps/main/downloads/reading-glasses.yaml"):
        bc.assert_true(not (ROOT / rel).exists(), f"{rel} still exists")


def check_unregistered() -> None:
    kz = bc.load_kind(ROOT / "kubernetes/apps/main/downloads/kustomization.yaml", "Kustomization")
    for res in kz["resources"]:
        bc.assert_true("readarr" not in res and "reading-glasses" not in res, f"{res} still registered")


def check_records_dropped() -> None:
    for rel in ("scripts/ci/kopiur-stage3-test.py", "scripts/ci/kopiur-stage5-test.py"):
        text = (ROOT / rel).read_text()
        bc.assert_true("readarr-config" not in text, f"{rel} still pins readarr-config")


if __name__ == "__main__":
    sys.exit(bc.run([
        ("Readarr/reading-glasses files removed", check_dirs_gone),
        ("not registered in downloads kustomization", check_unregistered),
        ("kopiur records drop readarr-config", check_records_dropped),
    ]))
