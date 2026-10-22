"""Shared helpers for the books contract tests (YAML parsing and assertions)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DIGEST_TAG = re.compile(r"^[A-Za-z0-9._-]+@sha256:[0-9a-f]{64}$")


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_all(path: Path) -> list[dict[str, Any]]:
    assert_true(path.exists(), f"missing file: {path.relative_to(ROOT)}")
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def load_kind(path: Path, kind: str) -> dict[str, Any]:
    for doc in load_all(path):
        if doc.get("kind") == kind:
            return doc
    raise Failure(f"no {kind} in {path.relative_to(ROOT)}")


def run(checks: list) -> int:
    failures = 0
    for name, fn in checks:
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as exc:
            failures += 1
            print(f"[FAIL] {name}: {exc}")
    print(f"\n{failures} failure(s)" if failures else "\nall checks passed")
    return 1 if failures else 0
