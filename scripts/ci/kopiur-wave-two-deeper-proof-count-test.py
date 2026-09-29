#!/usr/bin/env python3
"""The five near-empty kopiur claims stay in two disjoint sets.

Cannot-deeper: the volume holds essentially nothing, so another drill does
not deepen the proof. Complete: the file count is small and the proof already
covers 100% of the real content. Collapsing them into one "too small" count
mis-states retirement readiness. The sets live in the proof ledger.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / ".agents/skills/kopiur-backups/references/proof-ledger.md"

CANNOT_DEEPER = frozenset(
    {
        "downloads/autobrr",
        "selfhosted/paperless-ngx-media",
        "selfhosted/syncthing-data",
    }
)
COMPLETE = frozenset(
    {
        "selfhosted/ntfy",
        "selfhosted/obsidian-livesync",
    }
)


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def _claims_in_section(text: str, heading: str) -> set[str]:
    m = re.search(rf"^{re.escape(heading)}.*$", text, re.M)
    require(m is not None, f"missing heading {heading!r}")
    rest = text[m.end() :]
    nxt = re.search(r"^## ", rest, re.M)
    body = rest[: nxt.start()] if nxt else rest
    # Stop at the next bold lead-in so the two tables do not bleed.
    stop = re.search(r"^\*\*", body, re.M)
    # The heading we matched may itself be a bold line inside a section.
    # Take until the following bold heading after the table.
    parts = re.split(r"\n\*\*", body, maxsplit=1)
    table = parts[0]
    found = set(re.findall(r"`((?:downloads|selfhosted)/[a-z0-9-]+)`", table))
    require(found, f"no claims under {heading!r}")
    return found


def test_near_empty_sets_stay_disjoint() -> None:
    require(LEDGER.is_file(), f"missing {LEDGER.relative_to(REPO)}")
    text = LEDGER.read_text()
    cannot = _claims_in_section(text, "**Cannot be given a deeper proof**")
    complete = _claims_in_section(text, "**Already complete**")
    require(cannot == CANNOT_DEEPER, f"cannot-deeper drifted: {sorted(cannot)}")
    require(complete == COMPLETE, f"complete drifted: {sorted(complete)}")
    require(cannot.isdisjoint(complete), f"overlap: {sorted(cannot & complete)}")
    require(
        cannot | complete == CANNOT_DEEPER | COMPLETE,
        "the two sets must cover exactly the five near-empty claims",
    )


def main() -> int:
    try:
        test_near_empty_sets_stay_disjoint()
    except Failure as e:
        print(f"[FAIL] near_empty_sets: {e}")
        return 1
    print("[PASS] near_empty_sets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
