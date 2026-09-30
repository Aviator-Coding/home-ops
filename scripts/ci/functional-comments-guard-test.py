#!/usr/bin/env python3
"""Guard the comment lines that are functional, not documentation.

Two kinds of comment line change how the repo behaves:

  - `# yaml-language-server: $schema=...` drives editor/schema validation.
  - `# renovate: datasource=... depName=...` (and the `//` json5 form) is what
    Renovate's regex customManager reads; the tracked value must sit on the
    very next line, or Renovate silently stops updating that dependency.

Trimming comments (the docs-to-skills restructure) must never remove or
reorder these. This test enforces:

  1. Diff guard: for every non-Markdown file that exists both at the merge base
     and at HEAD (renames followed), the base's functional lines must still
     appear at HEAD, in the same order. Deleting a whole file is allowed
     (retiring an app removes its manifests); adding lines is allowed. Each
     base annotation must also still sit directly above a line with the same
     key, so swapping an annotation with its value line fails.
  2. Capture contract, at HEAD, for every file the customManager scans
     (*.env, *.sh, *.yaml, *.yml, *.yaml.j2): each `renovate: datasource=`
     annotation must be captured by one of the customManager's matchStrings,
     i.e. its value still sits on the very next line.

The base is DIFF_GUARD_BASE (the docs-guards job in validate.yaml sets it on
a full-history checkout); when it is set and cannot be resolved the diff guard
fails. Unset, it tries `origin/main` and skips when that cannot be resolved
(a shallow checkout such as the python-tests job, or a fresh local clone).
A deliberate removal (e.g. an image that stops being Renovate-tracked) goes in
ALLOWED_REMOVALS with a reason for the PR that makes it.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE_REQUIRED = bool(os.environ.get("DIFF_GUARD_BASE"))
BASE_REF = os.environ.get("DIFF_GUARD_BASE") or "origin/main"

FUNCTIONAL = re.compile(r"(?:#|//)\s*(?:yaml-language-server:|renovate:)")
RENOVATE_ANNOTATION = re.compile(r"(?:#|//)\s*renovate:.*?(datasource=\S+ depName=\S+)")
# Mirrors .renovate/customManagers.json5 "Process annotated dependencies":
# its managerFilePatterns and matchStrings (Python and JS agree on `.`/`\s`).
MANAGED_FILE = re.compile(r"(^|/).+\.(env|sh|ya?ml(\.j2)?)$")
MATCH_STRINGS = (
    re.compile(
        r"datasource=(?P<datasource>\S+) depName=(?P<depName>\S+)"
        r"( repository=(?P<registryUrl>\S+))?\n.+(:\s|=)(&\S+\s)?(?P<currentValue>\S+)"
    ),
    re.compile(r"datasource=(?P<datasource>\S+) depName=(?P<depName>\S+)\n.+/(?P<currentValue>(v|\d)[^/]+)"),
)

# Annotations the customManager already cannot capture. Each needs a reason;
# an entry that starts matching (or disappears) fails so the list stays true.
KNOWN_UNCAPTURED: dict[str, str] = {
    "kubernetes/apps/base/rook-ceph/rook-ceph/cluster/helmrelease.yaml:quay.io/ceph/ceph":
        "pre-existing: sits above the cephImage block key, so no value is on the next line; "
        "the customManager has never captured it",
    "kubernetes/apps/base/database/cloudnative-pg/cluster-17/cluster-17.yaml:ghcr.io/tensorchord/cloudnative-pgvecto.rs":
        "pre-existing: the trailing ` versioning=docker` field is not in the matchString, "
        "so the customManager has never captured it",
}

# (path at HEAD, stripped functional line) -> reason. Scoped to one PR.
ALLOWED_REMOVALS: dict[tuple[str, str], str] = {
    (
        "kubernetes/apps/base/monitoring/grafana/app/helmrelease.yaml",
        '# renovate: depName="Prometheus Stats"',
    ): "Prometheus Stats (gnet 2) chart entry deleted: 2014 dashboard duplicated by Prometheus / Overview.",
    (
        "kubernetes/apps/base/downloads/sonarr/app/helmrelease.yaml",
        "# renovate: datasource=docker depName=ghcr.io/onedr0p/exportarr",
    ): "exportarr sidecar removed: the API key is not 20-32 alphanumeric, so it crash-looped.",
    (
        "kubernetes/apps/base/downloads/radarr/app/helmrelease.yaml",
        "# renovate: datasource=docker depName=ghcr.io/onedr0p/exportarr",
    ): "exportarr sidecar removed: the API key is not 20-32 alphanumeric, so it crash-looped.",
}

passed = 0
failed = 0


def record(ok: bool, msg: str) -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"[PASS] {msg}")
    else:
        failed += 1
        print(f"[FAIL] {msg}")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def functional_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if FUNCTIONAL.search(ln)]


def annotation_pairs(text: str) -> list[tuple[str, str]]:
    """(renovate annotation, key of the line right below it), in file order.

    The customManager captures the value from the very next line, so an
    annotation that ends up above a different key silently tracks the wrong
    value while every annotation line is still present and in order.
    """
    lines = text.splitlines()
    pairs = []
    for i, ln in enumerate(lines):
        if RENOVATE_ANNOTATION.search(ln):
            below = lines[i + 1].strip() if i + 1 < len(lines) else ""
            pairs.append((ln.strip(), re.split(r"[:=]", below, maxsplit=1)[0].strip()))
    return pairs


def is_subsequence(needle: list[str], hay: list[str]) -> bool:
    it = iter(hay)
    return all(any(h == n for h in it) for n in needle)


def resolve_base() -> str | None:
    try:
        return git("merge-base", "HEAD", BASE_REF).strip()
    except subprocess.CalledProcessError:
        return None


def test_diff_guard() -> None:
    base = resolve_base()
    if base is None:
        if BASE_REQUIRED:
            record(False, f"cannot resolve merge base with DIFF_GUARD_BASE={BASE_REF} (fetch-depth: 0?)")
        else:
            print(f"[SKIP] diff guard: cannot resolve merge base with {BASE_REF}")
        return

    base_files = set(git("ls-tree", "-r", "--name-only", base).splitlines())
    head_files = set(git("ls-files").splitlines())
    renames: dict[str, str] = {}
    for line in git("diff", "-M", "--name-status", base).splitlines():
        parts = line.split("\t")
        if parts[0].startswith("R") and len(parts) == 3:
            renames[parts[1]] = parts[2]

    candidates = [
        p for p in base_files
        if not p.endswith(".md") and (p in head_files or p in renames)
    ]
    changed = set(git("diff", "--name-only", base).splitlines())
    checked = 0
    violations = 0
    for old in sorted(candidates):
        new = renames.get(old, old)
        if old not in changed and old == new:
            continue
        try:
            before = functional_lines(git("show", f"{base}:{old}"))
        except (subprocess.CalledProcessError, UnicodeDecodeError):
            continue
        if not before:
            continue
        head_path = ROOT / new
        if not head_path.is_file():
            continue
        after = functional_lines(head_path.read_text())
        checked += 1
        head_pairs = annotation_pairs(head_path.read_text())
        moved = [
            p for p in annotation_pairs(git("show", f"{base}:{old}"))
            if p not in head_pairs and (new, p[0]) not in ALLOWED_REMOVALS
        ]
        if moved:
            record(False, f"{new}: renovate annotation no longer above its original key {moved}")
            violations += 1
            continue
        if is_subsequence(before, after):
            continue
        missing = [ln for ln in before if ln not in after]
        unexplained = [ln for ln in missing if (new, ln) not in ALLOWED_REMOVALS]
        if missing and not unexplained:
            continue
        detail = f"removed {unexplained}" if unexplained else "reordered"
        record(False, f"{new}: functional comment lines {detail}")
        violations += 1
    if not violations:
        record(True, f"diff guard vs {BASE_REF} ({base[:12]}): {checked} changed files with functional lines")


def test_next_line_contract() -> None:
    bad: list[str] = []
    seen_known: set[str] = set()
    total = 0
    for rel in git("ls-files").splitlines():
        if not MANAGED_FILE.search(rel):
            continue
        path = ROOT / rel
        if path.is_symlink() or not path.is_file():
            continue
        text = path.read_text()
        offset = 0
        for i, ln in enumerate(text.splitlines(keepends=True)):
            m = RENOVATE_ANNOTATION.search(ln)
            if m:
                total += 1
                pos = offset + m.start(1)
                dep = m.group(1).split("depName=", 1)[1]
                key = f"{rel}:{dep}"
                captured = any(r.match(text, pos) for r in MATCH_STRINGS)
                if key in KNOWN_UNCAPTURED:
                    seen_known.add(key)
                    if captured:
                        bad.append(f"{key} is captured now; drop it from KNOWN_UNCAPTURED")
                elif not captured:
                    bad.append(f"{rel}:{i + 1}")
            offset += len(ln)
    for key, reason in KNOWN_UNCAPTURED.items():
        if key not in seen_known:
            bad.append(f"KNOWN_UNCAPTURED {key} no longer exists; remove it")
        if not reason.strip():
            bad.append(f"KNOWN_UNCAPTURED {key} has no reason")
    record(not bad, f"{total} renovate annotations are captured by the customManager; bad={bad}")


def test_swap_is_detected() -> None:
    """Regression for the #1834 gap: an annotation swapped with its value line."""
    ann = "# renovate: datasource=docker depName=ghcr.io/example/app"
    good = f"env:\n  {ann}\n  APP_VERSION: v1.0.0\n  OTHER: x\n"
    swapped = f"env:\n  APP_VERSION: v1.0.0\n  {ann}\n  OTHER: x\n"
    bumped = good.replace("v1.0.0", "v1.0.1")
    record(annotation_pairs(good) != annotation_pairs(swapped), "swapped annotation changes its next-line key")
    record(annotation_pairs(good) == annotation_pairs(bumped), "a value bump keeps the next-line key")


def main() -> int:
    for key, reason in ALLOWED_REMOVALS.items():
        record(bool(reason.strip()), f"ALLOWED_REMOVALS {key} has a reason")
    test_swap_is_detected()
    test_diff_guard()
    test_next_line_contract()
    print(f"Summary: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
