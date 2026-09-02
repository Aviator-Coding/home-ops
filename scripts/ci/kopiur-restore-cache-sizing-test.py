#!/usr/bin/env python3
"""Semantic regression test for kopiur restore cache sizing.

`KOPIUR_CACHE_CAPACITY` looks like a tuning knob and is actually a correctness
constraint on the RESTORE path. Measured on `ai/hermes` from r2 on 2026-09-02
(docs/backups/kopiur-r2-restore-cache-gate-2026-09-02.md): during a restore the
kopia cache grows ~1:1 with the bytes written into the restore target until it
reaches kopia's own internal budget - observed as a ~6.2 GiB plateau - and only
then holds flat. So:

    required cache ~= min(snapshot sizeBytes, ~6.2 GiB), plus headroom

and it is a CLIFF, not a slope. While a claim's snapshot is smaller than its
cache the limit is never reached and any value works; the first time a growing
claim crosses its capacity the requirement jumps straight to the full plateau,
the `Restore` fails with `no space left on device` on /var/cache/kopia, and
kopiur states plainly that a failed Restore is terminal and never retries. The
failure is therefore discovered during an actual disaster recovery.

That is why these values are pinned rather than left to judgement: reverting
`ai/hermes` to a "tidier" 5Gi would silently restore an unrecoverable-at-DR-time
state, and nothing else in CI - `flate` included - looks at this value at all.

This test asserts on the parsed Flux `postBuild.substitute` map of each overlay,
not on source text, so a reformat or a move within the file still passes and a
real value change still fails.

Scope note: `downloads/sabnzbd` is deliberately NOT re-asserted here. It is
already pinned by kopiur-stage2-test.py and kopiur-stage5-test.py as part of the
retirement pilot, and duplicating it across three files is exactly the drift
this repo has been bitten by before.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APPS_MAIN = REPO / "kubernetes" / "apps" / "main"
PROOF_DOC = REPO / "docs" / "backups" / "kopiur-r2-restore-cache-gate-2026-09-02.md"

# The observed plateau: the ceiling the cache reaches on a large restore before
# kopia's own eviction engages. Any claim whose snapshot exceeds its cache must
# be able to hold this much.
MEASURED_PLATEAU_GIB = 6.2

# overlay -> (required capacity, why). Only claims with a real measurement
# behind them belong here; a guess pinned in CI is worse than no pin.
PINNED: dict[str, tuple[str, str]] = {
    "ai/hermes.yaml": (
        "16Gi",
        "9.70 GiB snapshot, the largest in the fleet. Its previous 5Gi (4.87 GiB usable) "
        "ran out of cache at ~45% restored and could not restore from r2 at all. 16Gi is "
        "sized to survive BOTH regimes: 2.5x the 6.21 GiB measured peak if kopia's "
        "eviction holds, and enough to hold the whole 9.70 GiB snapshot plus ~60% growth "
        "if it does not (kopiur sends `\"cache\":{}`, so the plateau is an unpinned kopia "
        "default this repo does not control). Proven end-to-end from r2 at exactly this "
        "value: 65,978 files / 10,419,954,664 bytes, matching the snapshot's own "
        "filesNew and sizeBytes",
    ),
    "media/plex.yaml": (
        "10Gi",
        "4.16 GiB snapshot. Raised from the 2Gi default by restore-proof finding 2 after "
        "a documented terminal r2 failure at 2Gi. Predicted safe by the 2026-09-02 "
        "measurement (snapshot well under the 9.74 GiB usable cache, so the limit is "
        "never reached) but NOT itself exercised against r2 - do not lower it without a "
        "drill",
    ),
}


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def overlay(rel: str) -> dict[str, Any]:
    path = APPS_MAIN / rel
    require(
        path.is_file(),
        f"{rel} does not exist. If this app was retired or renamed, update PINNED in this "
        f"file and {PROOF_DOC.relative_to(REPO)} together - do not just delete the entry, "
        f"the cache-sizing constraint follows the claim wherever it goes",
    )
    docs = [
        d
        for d in yaml.safe_load_all(path.read_text())
        if d and d.get("kind") == "Kustomization"
    ]
    require(len(docs) == 1, f"expected exactly 1 Flux Kustomization in {rel}, got {len(docs)}")
    return docs[0]


def substitute(d: dict[str, Any]) -> dict[str, str]:
    sub = ((d.get("spec") or {}).get("postBuild") or {}).get("substitute") or {}
    return {str(k): str(v) for k, v in sub.items()}


def parse_gib(value: str) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(Gi|Mi)", value.strip())
    if not m:
        raise Failure(f"cache capacity {value!r} is not a plain Gi/Mi quantity")
    n = float(m.group(1))
    return n if m.group(2) == "Gi" else n / 1024


def test_pinned_capacities() -> None:
    """The measured values must be exactly what is in Git."""
    for rel, (want, why) in PINNED.items():
        got = substitute(overlay(rel)).get("KOPIUR_CACHE_CAPACITY")
        require(
            got == want,
            f"{rel}: KOPIUR_CACHE_CAPACITY must be {want!r}, got {got!r}. Reason it is "
            f"pinned: {why}. A failed Restore is terminal and never retries - see "
            f"{PROOF_DOC.relative_to(REPO)} before changing this",
        )


def test_pinned_capacities_clear_the_plateau() -> None:
    """Self-consistency check on the constants in PINNED above.

    test_pinned_capacities compares the overlay against PINNED; this compares
    PINNED against the measurement, so that relaxing a pin *here* is caught too.
    Without it, editing PINNED to 5Gi would make both the pin and the overlay
    agree on a value that is below the ceiling a large restore actually reaches,
    and CI would go green on a claim that cannot be restored from r2 - no matter
    how much more generous 5Gi looks than the 2Gi component default.
    """
    for rel, (want, _why) in PINNED.items():
        # A PVC request yields ~97.4% usable after filesystem overhead
        # (measured: a 16Gi request reported 15.581 GiB usable).
        usable = parse_gib(want) * 0.974
        require(
            usable >= MEASURED_PLATEAU_GIB,
            f"{rel}: {want} gives only {usable:.2f} GiB usable, below the measured "
            f"{MEASURED_PLATEAU_GIB} GiB plateau a large restore reaches. Restores from r2 "
            f"on this claim would fail terminally",
        )


def test_every_declared_capacity_is_parseable() -> None:
    """Fleet-wide typo guard.

    A malformed quantity is not caught by flate (the value is a Flux substitution
    string, not a schema-validated field) and would surface as a mover pod that
    cannot be scheduled - at restore time.
    """
    seen = 0
    for path in sorted(APPS_MAIN.rglob("*.yaml")):
        for d in yaml.safe_load_all(path.read_text()):
            if not d or d.get("kind") != "Kustomization":
                continue
            value = substitute(d).get("KOPIUR_CACHE_CAPACITY")
            if value is None:
                continue
            seen += 1
            try:
                parse_gib(value)
            except Failure as e:
                raise Failure(f"{path.relative_to(APPS_MAIN)}: {e}") from e
    require(seen >= len(PINNED), f"expected at least {len(PINNED)} declared capacities, saw {seen}")


def test_proof_document_path_exists() -> None:
    """Guard that the proof-doc pointer still resolves on disk.

    Existence only - deliberately does not assert on document content. The
    behavioural pins are the substitute-map assertions above; content needles
    would not catch a rewrite that kept the strings while dropping the model.
    """
    require(
        PROOF_DOC.is_file(),
        f"{PROOF_DOC.relative_to(REPO)} is missing - it is the only record of why these "
        f"capacities are what they are, and the values are meaningless without it",
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

    run("pinned_capacities", test_pinned_capacities)
    run("pinned_capacities_clear_the_plateau", test_pinned_capacities_clear_the_plateau)
    run("every_declared_capacity_is_parseable", test_every_declared_capacity_is_parseable)
    run("proof_document_path_exists", test_proof_document_path_exists)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
