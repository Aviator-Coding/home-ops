#!/usr/bin/env python3
"""Guards the TimescaleDB Community extension image build for postgres-18.

  1. cluster-18 references the tag the build context produces
     (ghcr.io/aviator-coding/timescaledb-community:<version>-<hash>), so an edit
     under .github/docker/timescaledb-community cannot ship without the manifest
     following it.
  2. The tag changes with any context byte.
  3. The Dockerfile pins the Timescale signing key fingerprint, an exact
     package version and a digest-pinned base image.
  4. The extension is preloaded (shared_preload_libraries) and telemetry is off.

.github/workflows/build-timescaledb-community.yaml calls `--print-tag` here, so
the workflow and this test cannot disagree about the tag.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCKER_DIR = ROOT / ".github" / "docker" / "timescaledb-community"
CLUSTER = ROOT / "kubernetes/apps/base/database/cloudnative-pg/cluster-18/cluster-18.yaml"
IMAGE_REPOSITORY = "ghcr.io/aviator-coding/timescaledb-community"
EXTENSION_NAME = "timescaledb"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def build_context_files() -> list[Path]:
    """Files git would ship in the build context (tracked + untracked-not-ignored)."""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "."],
        cwd=DOCKER_DIR,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\n")
    files = sorted({DOCKER_DIR / line for line in out if line})
    return [f for f in files if f.is_file()]


def timescaledb_version(dockerfile_text: str) -> str:
    match = re.search(r"^ARG TIMESCALEDB_VERSION=(\d+\.\d+\.\d+)~debian13-\d+$", dockerfile_text, re.M)
    require(match is not None, "Dockerfile: no exact `ARG TIMESCALEDB_VERSION=X.Y.Z~debian13-N` pin")
    return match.group(1)


def image_tag(files: list[Path] | None = None) -> str:
    """<timescaledb version>-<sha256 over every context file's path and bytes>[:12]."""
    files = build_context_files() if files is None else files
    require(any(f.name == "Dockerfile" for f in files), "build context has no Dockerfile")
    digest = hashlib.sha256()
    for f in files:
        rel = f.relative_to(DOCKER_DIR).as_posix().encode()
        data = f.read_bytes()
        digest.update(b"%d:%s\0%d:" % (len(rel), rel, len(data)))
        digest.update(data)
    version = timescaledb_version((DOCKER_DIR / "Dockerfile").read_text())
    return f"{version}-{digest.hexdigest()[:12]}"


def cluster_spec() -> dict[str, Any]:
    docs = [d for d in yaml.safe_load_all(CLUSTER.read_text()) if isinstance(d, dict)]
    clusters = [d for d in docs if d.get("kind") == "Cluster"]
    require(len(clusters) == 1, f"{CLUSTER.relative_to(ROOT)}: expected one Cluster, found {len(clusters)}")
    return clusters[0]["spec"]


def test_cluster_names_the_tag_the_build_context_produces() -> dict[str, Any]:
    expected = f"{IMAGE_REPOSITORY}:{image_tag()}"
    extensions = cluster_spec()["postgresql"].get("extensions") or []
    ours = [e for e in extensions if e.get("name") == EXTENSION_NAME]
    require(len(ours) == 1, f"cluster-18: expected one `{EXTENSION_NAME}` extension, found {len(ours)}")
    reference = ours[0]["image"]["reference"]
    require(
        reference == expected,
        f"cluster-18 extension reference {reference!r} but .github/docker/timescaledb-community "
        f"builds {expected!r} - set postgresql.extensions[{EXTENSION_NAME}].image.reference to it",
    )
    return {"reference": reference}


def test_tag_changes_with_any_context_byte() -> dict[str, Any]:
    base = DOCKER_DIR / "Dockerfile"
    tag = image_tag([base])
    original = base.read_bytes()
    try:
        base.write_bytes(original + b"\n# changed\n")
        changed = image_tag([base])
    finally:
        base.write_bytes(original)
    require(tag != changed, "tag did not change when the Dockerfile changed")
    return {"before": tag, "after": changed}


def test_dockerfile_pins_key_and_package() -> dict[str, Any]:
    text = (DOCKER_DIR / "Dockerfile").read_text()
    require(
        re.search(r"^ARG TIMESCALE_KEY_FPR=[0-9A-F]{40}$", text, re.M) is not None,
        "Dockerfile: no 40-hex `ARG TIMESCALE_KEY_FPR=` signing-key pin",
    )
    require("gpg --show-keys" in text, "Dockerfile: the key fingerprint is pinned but never checked")
    require("@sha256:" in text.split("FROM", 1)[0], "Dockerfile: BASE image is not digest-pinned")
    return {"version": timescaledb_version(text)}


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


TESTS = [
    test_cluster_names_the_tag_the_build_context_produces,
    test_tag_changes_with_any_context_byte,
    test_dockerfile_pins_key_and_package,
    test_extension_is_preloaded_with_telemetry_off,
]


def main() -> int:
    if sys.argv[1:] == ["--print-tag"]:
        print(image_tag())
        return 0
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
