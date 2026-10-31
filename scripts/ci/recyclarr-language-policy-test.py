#!/usr/bin/env python3
# ci: nightly
"""Language policy: every Radarr movie must carry an English audio track.

Policy (owner decision 2026-10-18): releases without English audio are hard-rejected on
every Recyclarr-managed Radarr profile. German is welcome only as an extra track (dual
audio, "DL"); a German-only dub is rejected. Pins the GitOps half; which movie sits on
which profile is Radarr database state.

  1. ``Language: Not English`` is scored at or below HARD_REJECT on every declared profile.
  2. ``German LQ`` is scored at or below HARD_REJECT on every declared profile.
  3. No German-only format carries a positive score (it must not rescue a release lacking English).
  4. ``MULTi`` stays a small nudge, not a block.
  5. Real release names run through the live TRaSH specs are accepted or rejected as the policy says.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "kubernetes/apps/base/downloads/recyclarr/app/config/recyclarr.yml"

_spec = importlib.util.spec_from_file_location(
    "recyclarr_quality_profile_test", ROOT / "scripts/ci/recyclarr-quality-profile-test.py"
)
base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = base
_spec.loader.exec_module(base)  # type: ignore[union-attr]
Failure = base.Failure

HARD_REJECT = -10000
MULTI_FLOOR = -1000

NOT_ENGLISH = "0dc8aec3bd1c47cd6c40c46ecd27e846"
GERMAN_LQ = "263943bc5d99550c68aad0c4278ba1c7"
MULTI = "4b900e171accbfb172729b63323ea8ca"
GERMAN_ONLY_FORMATS = {
    "86bc3115eb4e9873ac96904a4a68e19e": "German",
    "6aad77771dabe9d3e9d7be86f310b867": "German DL (undefined)",
    "ee425f921dd740621523343bffe82fa3": "German Subbed",
}

CF_URL = "https://raw.githubusercontent.com/TRaSH-Guides/Guides/master/docs/json/radarr/cf/{}.json"
LANG_ENGLISH, LANG_GERMAN, LANG_ORIGINAL = 1, 4, -2


def radarr_instance(cfg: dict[str, Any]) -> dict[str, Any]:
    radarr = (cfg.get("radarr") or {}).get("radarr_main")
    base.assert_true(isinstance(radarr, dict), "radarr.radarr_main missing")
    return radarr


def declared_profiles(radarr: dict[str, Any]) -> set[str]:
    return {qp["trash_id"] for qp in base.quality_profiles(radarr) if qp.get("trash_id")}


def scores_for(radarr: dict[str, Any], cf_id: str) -> dict[str, int]:
    """profile trash_id -> score for one custom format (None score = guide default, skipped)."""
    out: dict[str, int] = {}
    for group in base.custom_format_groups(radarr):
        if cf_id not in (group.get("trash_ids") or []) or "score" not in group:
            continue
        for target in group.get("assign_scores_to") or []:
            out[target["trash_id"]] = group["score"]
    return out


def assert_hard_reject(radarr: dict[str, Any], cf_id: str, label: str) -> None:
    scores = scores_for(radarr, cf_id)
    for profile in declared_profiles(radarr):
        score = scores.get(profile)
        base.assert_true(
            score is not None and score <= HARD_REJECT,
            f"{label} ({cf_id}) must score <= {HARD_REJECT} on profile {profile}, got {score!r}",
        )


def assert_policy(cfg: dict[str, Any]) -> None:
    radarr = radarr_instance(cfg)
    assert_hard_reject(radarr, NOT_ENGLISH, "Language: Not English")
    assert_hard_reject(radarr, GERMAN_LQ, "German LQ")

    for cf_id, label in GERMAN_ONLY_FORMATS.items():
        for profile, score in scores_for(radarr, cf_id).items():
            base.assert_true(
                score <= 0,
                f"{label} ({cf_id}) scores {score} on {profile}; German alone must never rescue a release",
            )

    for profile, score in scores_for(radarr, MULTI).items():
        base.assert_true(
            MULTI_FLOOR < score <= 0,
            f"MULTi on {profile} must be a small nudge, got {score}",
        )

    base.assert_assign_scores_use_trash_id(radarr, "radarr")


# --- release-name fixtures against the live TRaSH specs --------------------------------

def fetch_cf(slug: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(CF_URL.format(slug), timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"[WARN] could not fetch TRaSH CF {slug}: {exc}")
        return None


def spec_matches(spec: dict[str, Any], title: str, languages: set[int]) -> bool:
    impl, value = spec["implementation"], spec["fields"]["value"]
    if impl == "LanguageSpecification":
        hit = value in languages
    elif impl == "ReleaseTitleSpecification":
        hit = re.search(value.replace("(?i)", ""), title, re.IGNORECASE) is not None
    else:
        hit = False  # release-group specs etc. are not exercised by these fixtures
    return not hit if spec["negate"] else hit


def cf_matches(cf: dict[str, Any], title: str, languages: set[int]) -> bool:
    """Radarr semantics: all required specs match, and at least one optional spec if any exist."""
    required = [s for s in cf["specifications"] if s["required"]]
    optional = [s for s in cf["specifications"] if not s["required"]]
    if not all(spec_matches(s, title, languages) for s in required):
        return False
    return not optional or any(spec_matches(s, title, languages) for s in optional)


FIXTURES = [
    # (release title, languages Radarr parses from it, expected: True = passes the policy)
    ("Movie.2020.2160p.WEB-DL.DL.German.DTS-GRP", {LANG_GERMAN, LANG_ENGLISH}, True),
    ("Film.2020.1080p.WEB.x265.English-GRP", {LANG_ENGLISH}, True),
    ("Movie.2020.1080p.WEB.GERMAN.x264-GRP", {LANG_GERMAN}, False),
    ("Film.2020.1080p.BluRay.FRENCH.x264-GRP", {2}, False),
    # German-original film, German-only release: "German DL" would match (German is the
    # original language) but the hard reject on missing English must still win.
    ("Das.Boot.1981.1080p.BluRay.German.x264-GRP", {LANG_GERMAN}, False),
]


def assert_fixtures() -> None:
    not_english = fetch_cf("language-not-english")
    if not_english is None:
        return
    base.assert_true(not_english["trash_id"] == NOT_ENGLISH, "language-not-english trash_id drifted")
    for title, languages, passes in FIXTURES:
        rejected = cf_matches(not_english, title, languages)
        base.assert_true(
            rejected == (not passes),
            f"{title!r} languages={sorted(languages)}: expected {'pass' if passes else 'reject'}",
        )


def main() -> int:
    failures = 0

    def run(name: str, fn) -> None:  # type: ignore[no-untyped-def]
        nonlocal failures
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as exc:
            failures += 1
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors as failures
            failures += 1
            print(f"[FAIL] {name}: unexpected {type(exc).__name__}: {exc}")

    run("source recyclarr.yml enforces the English-audio policy",
        lambda: assert_policy(base.load_file_config(CONFIG_PATH)))
    run("kustomize ConfigMap enforces the English-audio policy",
        lambda: assert_policy(base.kustomize_built_config()))
    run("release-name fixtures against TRaSH specs", assert_fixtures)

    print(f"\n{failures} failure(s)" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
