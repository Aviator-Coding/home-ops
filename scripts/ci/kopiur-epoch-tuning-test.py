#!/usr/bin/env python3
"""Semantic regression test for the kopiur `ceph` epoch tuning.

`parameters.epoch.minDuration` on the `ceph` ClusterRepository looks like a
cosmetic knob and is actually what keeps `IndexBlobHealth` inside its threshold.
Measured 2026-09-03 (docs/backups/kopiur-ceph-index-blob-compaction-2026-09-03.md):

kopiur advances epochs ONLY during a maintenance run, and writes the epoch marker
~9 minutes INTO that run. So an epoch is ~9 minutes short of maturity at the run
exactly one period later and waits a whole further period. At the 24h default the
epochs measured 26.7h / 30.0h / 27.0h - never 24h - each 23.8h old at the run that
should have closed it. Exactly two epochs are live at once (one closed-but-not-yet-
compacted plus the open one), so at ceph's measured 32.5 index blobs/hour the count
oscillated between ~900 and ~1810 against a threshold of 1000.

This test exists because the alert message itself recommends the wrong values. It
suggests `6h` - which loses to the SAME rounding effect against a 6h quick cron and
silently delivers 11.85h epochs - and `spec.health.indexBlobWarnThreshold`, which
hides the inefficiency rather than fixing it. Someone reading only the alert would
naturally "correct" 5h to 6h, or drop the block as clutter, and nothing else in CI
would notice: `flate` validates the manifest builds, not what the value means, and
there is no Prometheus metric for the index-blob count at all.

Assertions are on the parsed manifest, not on source text, so a reformat or a move
within the file still passes and a real value change still fails.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
MANIFEST = (
    REPO
    / "kubernetes"
    / "apps"
    / "base"
    / "system"
    / "kopiur"
    / "repository"
    / "clusterrepository.yaml"
)
PROOF_DOC = REPO / "docs" / "backups" / "kopiur-ceph-index-blob-compaction-2026-09-03.md"

# The band derived from ceph's OWN maintenance offsets (quick 0 */6 * * * at
# ~00/06/12/18, full 0 3 * * * at ~03:07), with the ~9 min marker lag:
#
#   upper bound - must be BELOW the shortest quick-to-quick gap minus the marker
#     lag, or the epoch just-misses and stretches to the next slot. That is the
#     whole bug, applied to its own fix.
#   lower bound - must be ABOVE the quick-to-full gap, or the full run also
#     advances the epoch. That would create a fifth epoch per day while
#     compaction stays at one epoch per run, eating the headroom.
BAND_EXCLUSIVE_LOW_H = 3.0
BAND_INCLUSIVE_HIGH_H = 5.85

# Measured on the live repository, 2026-09-03.
BLOBS_PER_HOUR = 32.5
# `spec.health.indexBlobWarnThreshold` is unset on both repositories, so the CRD
# default applies (verified against the live CRD 2026-09-03: `default: 1000`).
THRESHOLD = 1000
# Live index blobs = one closed-but-not-yet-compacted epoch + the open one.
# Read directly off the cluster: indexBlobCount 882 = 879 (xn2) + 1 (xn3) + 2 (xs).
LIVE_EPOCH_MULTIPLE = 2
# Epoch length is minDuration rounded UP to the next maintenance run. Inside the
# band above that is always the next quick run: 6h minus the ~9 min marker lag.
EFFECTIVE_EPOCH_H = 5.85
# Refuse to ship a value with less headroom than this. 6h would land at 23%.
MIN_HEADROOM_FRACTION = 0.40


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def repositories() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for d in yaml.safe_load_all(MANIFEST.read_text()):
        if d and d.get("kind") == "ClusterRepository":
            out[d["metadata"]["name"]] = d
    return out


def parse_hours(value: str) -> float:
    """Go-style duration, restricted to the h/m forms kopiur's CRD accepts."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(h|m)", str(value))
    if not m:
        raise Failure(f"minDuration {value!r} is not a plain Go h/m duration")
    n = float(m.group(1))
    return n if m.group(2) == "h" else n / 60


def test_ceph_min_duration_is_set() -> None:
    """The parameter must exist. Its absence silently reverts to kopia's 24h."""
    ceph = repositories().get("ceph")
    require(ceph is not None, "no `ceph` ClusterRepository in the manifest")
    got = ceph["spec"].get("parameters", {}).get("epoch", {}).get("minDuration")
    require(
        got is not None,
        "ceph: spec.parameters.epoch.minDuration is missing. Without it kopia's 24h "
        f"default applies, which measured 27-30h epochs and ~1810 live index blobs "
        f"against a threshold of {THRESHOLD}. See {PROOF_DOC.relative_to(REPO)}",
    )


def test_ceph_min_duration_is_inside_the_derived_band() -> None:
    """The value must advance at every quick run and never at the full run.

    This is the assertion that catches "helpfully" changing 5h to the 6h the
    condition message suggests: 6h is above the upper bound, so the epoch is
    ~9 minutes short at the next quick run and stretches to 11.85h.
    """
    ceph = repositories()["ceph"]
    got = ceph["spec"].get("parameters", {}).get("epoch", {}).get("minDuration")
    # Absence is test_ceph_min_duration_is_set's failure to report, not this one's.
    require(got is not None, "ceph: spec.parameters.epoch.minDuration is missing")
    hours = parse_hours(got)
    require(
        hours > BAND_EXCLUSIVE_LOW_H,
        f"ceph: minDuration {got!r} ({hours}h) is at or below {BAND_EXCLUSIVE_LOW_H}h, so the "
        f"~03:07 full run would also advance the epoch - a fifth epoch per day while "
        f"compaction stays at one epoch per run",
    )
    require(
        hours <= BAND_INCLUSIVE_HIGH_H,
        f"ceph: minDuration {got!r} ({hours}h) exceeds {BAND_INCLUSIVE_HIGH_H}h, the shortest "
        f"quick-to-quick gap minus the ~9 min epoch-marker lag. The epoch will be just short "
        f"of maturity at the next quick run and stretch to the one after - the exact rounding "
        f"effect this value exists to avoid. This is why the condition message's suggested 6h "
        f"is not the fix: see {PROOF_DOC.relative_to(REPO)}",
    )


def test_headroom_against_the_threshold() -> None:
    """Self-consistency check on the constants above.

    test_ceph_min_duration_is_inside_the_derived_band compares the manifest against
    the band; this one compares the band against the measurement, so widening the
    band itself is caught too. Without it, raising BAND_INCLUSIVE_HIGH_H to 12h
    would make both the band and the manifest agree on a value that puts the
    repository back over its threshold.
    """
    peak = LIVE_EPOCH_MULTIPLE * EFFECTIVE_EPOCH_H * BLOBS_PER_HOUR
    headroom = (THRESHOLD - peak) / THRESHOLD
    require(
        peak < THRESHOLD,
        f"the band's effective {EFFECTIVE_EPOCH_H}h epoch yields a live peak of {peak:.0f} "
        f"index blobs at the measured {BLOBS_PER_HOUR}/hour, at or over the {THRESHOLD} "
        f"threshold",
    )
    require(
        headroom >= MIN_HEADROOM_FRACTION,
        f"live peak {peak:.0f} leaves only {headroom:.0%} headroom against {THRESHOLD}, "
        f"below the {MIN_HEADROOM_FRACTION:.0%} floor. The fleet grows; a value that only "
        f"just clears today re-trips on the next few claims",
    )


def test_r2_is_deliberately_untuned_and_says_so() -> None:
    """r2 must stay untuned, and the manifest must explain why.

    r2 carries the IDENTICAL stalled-compaction structure and is under the
    threshold only because it takes the daily backup slot rather than the
    4-hourly one (422 index blobs vs ceph's 1851). Copying ceph's 5h here would
    be wrong - the band has to be re-derived against r2's own maintenance
    offsets (quick ~00:16, full ~03:35) - and silently dropping the comment
    would leave the next reader believing r2 is structurally healthy.
    """
    r2 = repositories().get("r2")
    require(r2 is not None, "no `r2` ClusterRepository in the manifest")
    got = r2["spec"].get("parameters", {}).get("epoch", {}).get("minDuration")
    require(
        got is None,
        f"r2: minDuration is set to {got!r}. r2 was left untuned deliberately; if that "
        f"changed, re-derive the band against r2's OWN maintenance offsets rather than "
        f"copying ceph's, and update {PROOF_DOC.relative_to(REPO)} and this test together",
    )
    text = MANIFEST.read_text()
    marker = "No `parameters.epoch` override here, DELIBERATELY"
    require(
        marker in text,
        f"r2: the comment explaining why it is untuned is gone. Without it the next reader "
        f"sees IndexBlobHealth=True and concludes r2 is healthy, when it holds the same "
        f"defect at a quarter the write rate",
    )


def test_threshold_is_not_silenced() -> None:
    """Neither repository may raise or disable the index-blob warning threshold.

    `spec.health.indexBlobWarnThreshold` (CRD default 1000, `0` disables) is the
    third fix the condition message suggests, and it is the wrong one: it makes
    the symptom go away while the repository keeps carrying two oversized epochs.
    Raising it would also silently invalidate the headroom arithmetic above,
    which assumes the default.

    If a future measurement genuinely shows the threshold is mis-set for this
    repository's size, that is a finding to argue in the proof document with
    numbers - and this test should be updated in the same change, not deleted.
    """
    for name, repo in repositories().items():
        got = repo["spec"].get("health", {}).get("indexBlobWarnThreshold")
        require(
            got is None,
            f"{name}: spec.health.indexBlobWarnThreshold is set to {got!r} "
            f"({'disabling' if got == 0 else 'raising'} the warning). That silences the "
            f"symptom without compacting anything - fix the epoch length instead. See "
            f"{PROOF_DOC.relative_to(REPO)}",
        )


def test_proof_document_path_exists() -> None:
    """Guard that the proof-doc pointer still resolves on disk.

    Existence only - deliberately does not assert on content. The behavioural
    pins are the manifest assertions above; content needles would not catch a
    rewrite that kept the strings while dropping the model.
    """
    require(
        PROOF_DOC.is_file(),
        f"{PROOF_DOC.relative_to(REPO)} is missing; the manifest and this test both cite it",
    )


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

    run("ceph_min_duration_is_set", test_ceph_min_duration_is_set)
    run("ceph_min_duration_is_inside_the_derived_band", test_ceph_min_duration_is_inside_the_derived_band)
    run("headroom_against_the_threshold", test_headroom_against_the_threshold)
    run("r2_is_deliberately_untuned_and_says_so", test_r2_is_deliberately_untuned_and_says_so)
    run("threshold_is_not_silenced", test_threshold_is_not_silenced)
    run("proof_document_path_exists", test_proof_document_path_exists)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
