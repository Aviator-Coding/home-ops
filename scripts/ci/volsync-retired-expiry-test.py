#!/usr/bin/env python3
"""Semantic regression test for the VolSync retired-repository expiry ledger.

Pins the 2026-09-12 captain decision to put an EXPIRY DATE on the retired
pre-kopiur VolSync restic repositories - not delete them now, not keep them
forever - and, much more importantly, pins the one property that makes that
decision safe to execute.

The hazard this file exists for
------------------------------
The `volsync` buckets hold one restic repository per top-level prefix. Two of
those prefixes are `syncthing` (RETIRED) and `syncthing-data` (LIVE), and the
live one is a strict string extension of the retired one. Any match built with
`startswith`, an unanchored regex, a glob, or a bare S3 `Filter.Prefix` of
`syncthing` selects the live repository too - measured: all 107 live
`syncthing-data` objects in the ceph bucket on 2026-09-12. That deletion would
look completely correct in review.

The ledger avoids it structurally rather than carefully: entries are bare path
segments, and `render_lifecycle.py` appends the slash in exactly one place, so
the emitted match is `syncthing/`, which cannot reach `syncthing-data/...`
because the strings differ at index 9 (`/` vs `-`). These tests assert that the
structure is still intact, that the renderer still fails closed when it is not,
and that the ledger's idea of which repositories are live still agrees with what
the repo actually declares.

Scope note: this deliberately does NOT assert the expiry dates themselves. They
are a judgement call the captain can overrule by editing one line, and freezing
them here would turn a legitimate future edit into a red check - the trap
`falkordb-lan-browser-access-test.py` was converted away from. What is asserted
is the SHAPE of a date (absolute, midnight UTC, after the measurement) and the
absence of a `Days`-based expiry, which would be a correctness bug rather than a
preference: `Days` counts from each object's own creation time and would delete
most of this the moment it was installed.

Owning document: docs/backups/volsync-retired-repository-expiry.md
"""

from __future__ import annotations

import copy
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
TOOL_DIR = REPO / "scripts" / "volsync-retired-expiry"
LEDGER = TOOL_DIR / "ledger.yaml"
DOC = REPO / "docs" / "backups" / "volsync-retired-repository-expiry.md"
APPS_MAIN = REPO / "kubernetes" / "apps" / "main"

sys.path.insert(0, str(TOOL_DIR))
import render_lifecycle  # noqa: E402

# Real object keys, copied from the 2026-09-12 read-only listing of the ceph
# `volsync` bucket. The point of using measured keys rather than invented ones
# is that the adversarial case here is a real pair of repositories, not a
# hypothetical naming scheme.
MEASURED_KEYS = {
    "syncthing": ["syncthing/config", "syncthing/snapshots/0ed2", "syncthing/data/00/00ab"],
    "syncthing-data": [
        "syncthing-data/config",
        "syncthing-data/snapshots/9dd2",
        "syncthing-data/data/1f/1f0c",
    ],
}

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T00:00:00Z$")


class Failure(Exception):
    pass


def ledger() -> dict[str, Any]:
    return render_lifecycle.load_ledger(LEDGER)


def rules() -> list[dict[str, Any]]:
    return render_lifecycle.build_rules(ledger())


def live_volsync_apps_from_git() -> set[str]:
    """The APP values whose Flux overlay still wires up components/volsync.

    This is the repo's own declaration of which restic repositories are still
    being written to, so the ledger's `protected` list is checked against the
    manifests rather than against a hand-maintained copy that can drift. YAML
    anchors (`APP: *app`) resolve during parsing, so this reads the effective
    value.
    """
    live: set[str] = set()
    for path in sorted(APPS_MAIN.rglob("*.yaml")):
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except yaml.YAMLError as exc:  # pragma: no cover - malformed manifest
            raise Failure(f"{path.relative_to(REPO)} is not parseable: {exc}") from exc
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "Kustomization":
                continue
            spec = doc.get("spec") or {}
            components = spec.get("components") or []
            uses_volsync = "components/volsync" in (spec.get("path") or "") or any(
                "components/volsync" in str(c) for c in components
            )
            if not uses_volsync:
                continue
            app = ((spec.get("postBuild") or {}).get("substitute") or {}).get("APP")
            if app:
                live.add(str(app))
    return live


def test_ledger_entries_are_bare_segments() -> None:
    led = ledger()
    entries = list(led["protected"])
    for tier in led["tiers"].values():
        entries.extend(tier["prefixes"])
    for name in entries:
        if not isinstance(name, str) or not name or "/" in name or name.strip() != name:
            raise Failure(
                f"ledger entry {name!r} is not a bare single path segment; the "
                "trailing slash must only ever be added by render_lifecycle.py"
            )


def test_protected_matches_what_the_repo_declares_live() -> None:
    declared = live_volsync_apps_from_git()
    protected = set(ledger()["protected"])
    if declared != protected:
        raise Failure(
            "ledger `protected` disagrees with the overlays that still use "
            f"components/volsync. Declared live in Git: {sorted(declared)}; "
            f"ledger protected: {sorted(protected)}. If a claim was just "
            "retired from VolSync, move it into a tier deliberately; if one was "
            "re-onboarded, it must be protected before any rule is applied."
        )


def test_no_retired_entry_still_has_a_live_volsync_source() -> None:
    declared = live_volsync_apps_from_git()
    led = ledger()
    for tier_name, tier in led["tiers"].items():
        clash = sorted(set(tier["prefixes"]) & declared)
        if clash:
            raise Failure(
                f"tier {tier_name!r} lists {clash} as retired, but their "
                "overlays still wire up components/volsync - expiring those "
                "would delete a repository that is still being written to"
            )


def test_every_rule_is_an_exact_segment_match() -> None:
    led = ledger()
    expected = {n for tier in led["tiers"].values() for n in tier["prefixes"]}
    got = set()
    for rule in rules():
        prefix = rule["Filter"]["Prefix"]
        if not prefix.endswith("/") or prefix.count("/") != 1:
            raise Failure(
                f"rule {rule['ID']} has Filter.Prefix {prefix!r}; it must be a "
                "single path segment followed by exactly one slash"
            )
        got.add(prefix[:-1])
    if got != expected:
        raise Failure(f"rendered rules {sorted(got)} != ledger entries {sorted(expected)}")


def test_syncthing_rule_cannot_reach_syncthing_data() -> None:
    """The worked example. This is the whole reason the ledger exists."""
    by_id = {r["ID"]: r for r in rules()}
    rule = by_id.get("volsync-retired-syncthing")
    if rule is None:
        raise Failure("no rule for the retired `syncthing` repository")
    prefix = rule["Filter"]["Prefix"]

    hit = [k for k in MEASURED_KEYS["syncthing"] if k.startswith(prefix)]
    if len(hit) != len(MEASURED_KEYS["syncthing"]):
        raise Failure(f"{prefix!r} failed to match its own repository's keys: {hit}")

    live_hit = [k for k in MEASURED_KEYS["syncthing-data"] if k.startswith(prefix)]
    if live_hit:
        raise Failure(
            f"CATASTROPHIC: rule prefix {prefix!r} matches LIVE syncthing-data "
            f"objects {live_hit}"
        )

    # And prove the hazard is real rather than theoretical, so this test still
    # means something if someone "simplifies" the slash away.
    naive_hit = [k for k in MEASURED_KEYS["syncthing-data"] if k.startswith("syncthing")]
    if not naive_hit:
        raise Failure(
            "the syncthing/syncthing-data collision is no longer reproducible; "
            "this test's fixtures have drifted from the real bucket layout"
        )


def test_renderer_refuses_a_ledger_that_endangers_a_live_repository() -> None:
    """Prove the fail-closed guard actually refuses, rather than trusting it."""
    base = ledger()

    moved = copy.deepcopy(base)
    moved["tiers"]["redundant"]["prefixes"].append("syncthing-data")
    try:
        render_lifecycle.build_rules(moved)
    except render_lifecycle.UnsafeLedger:
        pass
    else:
        raise Failure("renderer accepted a ledger listing the LIVE syncthing-data as retired")

    preslashed = copy.deepcopy(base)
    preslashed["tiers"]["redundant"]["prefixes"].append("syncthing/")
    try:
        render_lifecycle.build_rules(preslashed)
    except render_lifecycle.UnsafeLedger:
        pass
    else:
        raise Failure("renderer accepted a pre-slashed ledger entry")

    dupe = copy.deepcopy(base)
    dupe["tiers"]["sole_copy"]["prefixes"].append(dupe["tiers"]["redundant"]["prefixes"][0])
    try:
        render_lifecycle.build_rules(dupe)
    except render_lifecycle.UnsafeLedger:
        pass
    else:
        raise Failure("renderer accepted the same prefix in two tiers with different dates")


def test_expiry_is_absolute_midnight_utc_and_never_day_based() -> None:
    led = ledger()
    measured = dt.date.fromisoformat(str(led["measured_at"]))
    for rule in rules():
        exp = rule["Expiration"]
        if "Days" in exp:
            raise Failure(
                f"rule {rule['ID']} uses Expiration.Days. Days counts from each "
                "object's own creation time, and these repositories stopped "
                "being written between 2025-05 and 2026-09, so it would delete "
                "them immediately instead of on an expiry date."
            )
        date = exp.get("Date", "")
        if not DATE_RE.match(date):
            raise Failure(
                f"rule {rule['ID']} has Expiration.Date {date!r}; S3 requires an "
                "absolute ISO 8601 date at midnight UTC"
            )
        if dt.date.fromisoformat(date[:10]) <= measured:
            raise Failure(
                f"rule {rule['ID']} expires {date} which is not after the "
                f"ledger's measured_at {measured} - landing it would delete "
                "immediately rather than set a recovery window"
            )


def test_owning_document_exists() -> None:
    if not DOC.is_file():
        raise Failure(f"owning document missing: {DOC.relative_to(REPO)}")
    text = DOC.read_text()
    for needle in ("syncthing-data", "render_lifecycle.py", "ledger.yaml"):
        if needle not in text:
            raise Failure(f"owning document does not mention {needle!r}")


def main() -> int:
    tests: list[str] = []
    failures: list[str] = []

    def run(name: str, fn: Any) -> None:
        tests.append(name)
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as e:
            failures.append(name)
            print(f"[FAIL] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append(name)
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")

    run("ledger_entries_are_bare_segments", test_ledger_entries_are_bare_segments)
    run("protected_matches_what_the_repo_declares_live", test_protected_matches_what_the_repo_declares_live)
    run("no_retired_entry_still_has_a_live_volsync_source", test_no_retired_entry_still_has_a_live_volsync_source)
    run("every_rule_is_an_exact_segment_match", test_every_rule_is_an_exact_segment_match)
    run("syncthing_rule_cannot_reach_syncthing_data", test_syncthing_rule_cannot_reach_syncthing_data)
    run("renderer_refuses_a_ledger_that_endangers_a_live_repository", test_renderer_refuses_a_ledger_that_endangers_a_live_repository)
    run("expiry_is_absolute_midnight_utc_and_never_day_based", test_expiry_is_absolute_midnight_utc_and_never_day_based)
    run("owning_document_exists", test_owning_document_exists)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
