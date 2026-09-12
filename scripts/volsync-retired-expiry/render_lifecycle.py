#!/usr/bin/env python3
"""Render the VolSync retired-repository expiry ledger into S3 lifecycle JSON.

This is the ONLY place an S3 match expression for a retired restic repository is
constructed, and it is deliberately tiny so that the whole safety argument fits
in one screen.

Why the match cannot widen
--------------------------
`ledger.yaml` stores bare prefix names - `syncthing`, never `syncthing/`. This
function appends the slash, unconditionally, in exactly one place
(`_rule_prefix`). The resulting S3 `Filter.Prefix` is `"<name>/"`, and S3 prefix
matching is plain byte-wise `startswith` on the object key.

That makes a widened match structurally impossible rather than merely unlikely:

    "syncthing-data/snapshots/abc".startswith("syncthing/")  ->  False

because the two strings first differ at index 9, where the live repository has
`-` and the rule has `/`. There is no ordering, escaping or globbing subtlety to
get wrong, and no way to express the dangerous version by accident - a ledger
entry of `syncthing` cannot produce a rule that reaches `syncthing-data`, and a
ledger entry containing a slash is rejected outright.

On top of that structural property, `_assert_protected_untouched` re-derives the
safety of every emitted rule against every protected prefix and raises rather
than emitting anything at all if one is even arguably in range. Belt and braces:
the structure is what makes it correct, the assertion is what makes a future
edit that breaks the structure fail loudly instead of quietly.

Nothing here deletes anything, reads any credential, or talks to any network.
It prints JSON. Applying that JSON is a separate, deliberate operator action -
see docs/backups/volsync-retired-repository-expiry.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

LEDGER = Path(__file__).resolve().parent / "ledger.yaml"


class UnsafeLedger(Exception):
    """The ledger cannot be rendered without risking a live repository."""


def load_ledger(path: Path = LEDGER) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def _rule_prefix(name: str) -> str:
    """The one and only construction of an S3 match for a retired repository.

    A ledger entry is a single path segment. Appending the slash here - and
    nowhere else - is what confines the rule to exactly that segment.
    """
    if not name or "/" in name or name.strip() != name:
        raise UnsafeLedger(
            f"ledger entry {name!r} is not a bare single path segment; "
            "entries must be plain names with no slash so the slash can only "
            "ever be added here"
        )
    return f"{name}/"


def _assert_protected_untouched(rule_prefix: str, protected: list[str]) -> None:
    """Refuse to emit a rule that could reach a live repository's object space.

    A live repository's keys all begin with `<protected>/`. The rule matches a
    key iff the key begins with `rule_prefix`. Those two sets can only intersect
    if one of the two strings is a prefix of the other - which also catches the
    exact-match case where someone lists a live repo as retired.
    """
    for name in protected:
        live_space = f"{name}/"
        if rule_prefix.startswith(live_space) or live_space.startswith(rule_prefix):
            raise UnsafeLedger(
                f"rule prefix {rule_prefix!r} is in range of protected live "
                f"repository {name!r} (object space {live_space!r}) - refusing "
                "to render any rules"
            )


def build_rules(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the full S3 lifecycle rule list, or raise."""
    protected: list[str] = list(ledger["protected"])
    for name in protected:
        # Validate the protected names through the same gate, so a malformed
        # entry there cannot weaken the comparison above.
        _rule_prefix(name)

    rules: list[dict[str, Any]] = []
    seen: dict[str, str] = {}

    for tier_name, tier in ledger["tiers"].items():
        expires = str(tier["expires"])
        for name in tier["prefixes"]:
            if name in protected:
                raise UnsafeLedger(
                    f"{name!r} is listed as both protected and retired "
                    f"(tier {tier_name!r})"
                )
            if name in seen:
                raise UnsafeLedger(
                    f"{name!r} appears in two tiers ({seen[name]!r} and "
                    f"{tier_name!r}) - its expiry would be ambiguous"
                )
            seen[name] = tier_name

            prefix = _rule_prefix(name)
            _assert_protected_untouched(prefix, protected)
            rules.append(
                {
                    "ID": f"volsync-retired-{name}",
                    "Status": "Enabled",
                    "Filter": {"Prefix": prefix},
                    # Absolute date, always midnight UTC as S3 requires. Days
                    # would be measured from each object's own creation time and
                    # would therefore delete most of this immediately.
                    "Expiration": {"Date": f"{expires}T00:00:00Z"},
                }
            )

    rules.sort(key=lambda r: r["ID"])
    return rules


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--ledger", type=Path, default=LEDGER, help="ledger path (default: alongside this script)"
    )
    args = ap.parse_args(argv)

    try:
        rules = build_rules(load_ledger(args.ledger))
    except UnsafeLedger as exc:
        print(f"REFUSING TO RENDER: {exc}", file=sys.stderr)
        return 2

    # One rule set, applied identically to all three buckets. A rule whose
    # prefix holds no objects on a given destination is an inert no-op, and
    # identical configuration everywhere means the three buckets can be verified
    # by comparing them to each other.
    print(json.dumps({"Rules": rules}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
