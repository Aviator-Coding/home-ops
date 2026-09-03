#!/usr/bin/env python3
"""Semantic regression test for the kopiur `ceph` epoch tuning.

`parameters.epoch.minDuration` on the `ceph` ClusterRepository looks like a
cosmetic knob and is actually what keeps `IndexBlobHealth` inside its threshold.
Both mechanics below were MEASURED on 2026-09-03
(docs/backups/kopiur-ceph-index-blob-compaction-2026-09-03.md), and the second
one was established by an experiment that falsified the first attempt at this
fix - so neither is a guess:

1. An epoch's age is counted from the FIRST INDEX BLOB written into it, not from
   the epoch-marker blob that opens it. Epoch 3's marker was written 03:08:17 and
   its first blob 04:38:34; a maintenance run at 08:10:43 - 5.03h after the
   marker but only 3.53h after the first blob - did NOT advance it under a 5h
   gate. Writes arrive in the 4-hourly backup bursts, so there is 0-4h of dead
   time before the clock even starts.
2. kopiur advances epochs ONLY during a maintenance run, so maturity is rounded
   up to the next run (quick 00/06/12/18 +<=30m, full 03 +<=1h).

Epoch length is therefore `(wait for first write) + minDuration + (wait for next
run)`, which is strongly NON-MONOTONIC in minDuration - headroom steps rather
than sloping. That is the whole reason this value is pinned: 5h and the `6h` the
condition message recommends both land in a 19%-headroom band, while 3.5-4.75h
sits at 35% and 1.5-2.75h at 38-44%. A "tidier" round number is very likely to
be worse, and nothing else in CI would notice - flate validates that the
manifest builds, not what the value means, and there is no Prometheus metric for
the index-blob count at all.

Assertions are on the parsed manifest, not on source text, so a reformat or a
move within the file still passes and a real value change still fails.
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

# The plateau chosen, from simulating both mechanics against the real maintenance
# AND backup schedules over 2,400 epoch-days (the model reproduces the observed
# 24h epochs: mean 28.8h, range 26.4-30.5h vs measured 26.96h/30.03h).
#
# Headroom STEPS rather than slopes, so these are plateau edges, not a smooth
# tolerance. Measured peaks (2 x worst-case epoch x 32.5 blobs/h):
#
#     1.5-2.75h  peak 564-616   (38-44%)  4 epochs/day
#     3.0-3.25h  peak ~810      (19%)     <- cliff between the two good regions
#     3.5-4.75h  peak 649       (35%)     3 epochs/day   <- the chosen plateau
#     5.0-6.0h   peak ~812      (19%)     <- where 5h AND the suggested 6h land
#     12h / 24h  peak 1201 / 1980         over threshold
#
# The 3.5-4.75h plateau is preferred over the slightly-better 1.5-2.75h one
# because it is wider (so robust to schedule drift) and its 3 epochs/day leaves
# the most compaction margin: compaction advances one epoch per maintenance run
# and there are 5 runs/day. 2.75h peaks marginally better but sits 0.25h from a
# cliff down to 19%.
BAND_INCLUSIVE_LOW_H = 3.5
BAND_INCLUSIVE_HIGH_H = 4.75

BLOBS_PER_HOUR = 32.5
# `spec.health.indexBlobWarnThreshold` is unset on both repositories, so the CRD
# default applies (verified against the live CRD 2026-09-03: `default: 1000`).
THRESHOLD = 1000
# Live index blobs = one closed-but-not-yet-compacted epoch + the open one.
# Read directly off the cluster: indexBlobCount 882 = 879 (xn2) + 1 (xn3) + 2 (xs).
LIVE_EPOCH_MULTIPLE = 2
# WORST-CASE epoch length on the chosen plateau, from the simulation above. The
# threshold is breached by the worst epoch, not the average one (mean is 8.0h),
# so the headroom check below deliberately uses the worst case.
EFFECTIVE_EPOCH_H = 9.99
# Refuse to ship a value with less headroom than this. 6h would land at 23%.
MIN_HEADROOM_FRACTION = 0.30


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

    This is the assertion that catches "helpfully" rounding the value to a nearby
    number. Because headroom steps, 5h and the 6h the condition message suggests
    both fall off this plateau into the same ~19% band.
    """
    ceph = repositories()["ceph"]
    got = ceph["spec"].get("parameters", {}).get("epoch", {}).get("minDuration")
    # Absence is test_ceph_min_duration_is_set's failure to report, not this one's.
    require(got is not None, "ceph: spec.parameters.epoch.minDuration is missing")
    hours = parse_hours(got)
    require(
        BAND_INCLUSIVE_LOW_H <= hours <= BAND_INCLUSIVE_HIGH_H,
        f"ceph: minDuration {got!r} ({hours}h) is outside the measured "
        f"[{BAND_INCLUSIVE_LOW_H}h, {BAND_INCLUSIVE_HIGH_H}h] plateau. Headroom against the "
        f"index-blob threshold STEPS rather than slopes, so a nearby round number is very "
        f"likely to be worse, not slightly different: 3.0-3.25h and 5.0-6.0h both collapse to "
        f"~19% headroom, and 5h - this repository's own first attempt - was one of them, as "
        f"is the 6h the condition message recommends. Re-derive from the maintenance AND "
        f"backup schedules before moving it: see {PROOF_DOC.relative_to(REPO)}",
    )


def test_headroom_against_the_threshold() -> None:
    """Self-consistency check on the constants above.

    test_ceph_min_duration_is_inside_the_derived_band compares the manifest against
    the plateau; this one compares the plateau against the measurement, so widening
    the plateau itself is caught too. Without it, raising BAND_INCLUSIVE_HIGH_H to
    12h would make both the plateau and the manifest agree on a value that puts the
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
