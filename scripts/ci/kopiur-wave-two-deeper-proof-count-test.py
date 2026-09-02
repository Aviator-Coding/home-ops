#!/usr/bin/env python3
"""Semantic regression for the wave-two near-empty deeper-proof count.

docs/backups/kopiur-wave-two-reproof-2026-09-02.md is the operator-facing
deliverable for the 2026-09-02 kopiur wave-two exercise. Its Verdict headline
and the matching skill finding (f) classify five near-empty claims into two
disjoint sets:

  cannot-deeper  - volume holds essentially nothing; no ceremony deepens proof
  complete       - small file count, but the proof already covers 100% of real
                   claim content (not "thin")

A skimming captain who trusts only the headline number can mis-read retirement
readiness if that number drifts from the body enumeration. This was caught once
already (headline said four; body enumerated three). Pin the structured
classification so the number, the named set, Part 1's judgment, and skill (f)
cannot disagree again.

The markdown files are the owned text contract here (same class as the
corrupt-claim runbook pins): we parse their classification structure into a
normalized model and assert meaning, not incidental wording.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WAVE_TWO = REPO / "docs" / "backups" / "kopiur-wave-two-reproof-2026-09-02.md"
SKILL = REPO / ".claude" / "skills" / "kopiur-backups" / "SKILL.md"

# Authoritative classification from Part 1 "What properly can and cannot mean".
# The three cannot-deeper claims hold essentially nothing; the two complete
# claims already cover 100% of real content.
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
NEAR_EMPTY_FIVE = CANNOT_DEEPER | COMPLETE


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def _normalize_claim(raw: str) -> str:
    """Collapse short forms used in body prose to the canonical ns/name form."""
    token = raw.strip().strip("`")
    # Body sometimes says bare short names after the first mention.
    shorts = {
        "autobrr": "downloads/autobrr",
        "paperless-ngx-media": "selfhosted/paperless-ngx-media",
        "syncthing-data": "selfhosted/syncthing-data",
        "ntfy": "selfhosted/ntfy",
        "obsidian-livesync": "selfhosted/obsidian-livesync",
        "prowlarr-config": "downloads/prowlarr-config",
        "prowlarr": "downloads/prowlarr-config",
    }
    if token in shorts:
        return shorts[token]
    if "/" in token:
        return token
    return token


def _claims_in(text: str) -> set[str]:
    """Pull backtick-wrapped claim tokens and normalize them."""
    found: set[str] = set()
    for m in re.finditer(r"`([^`]+)`", text):
        token = m.group(1)
        # Skip non-claim tokens (sizes, digests, paths, statuses, etc.).
        if re.fullmatch(
            r"(?:downloads|selfhosted|media|ai|home-automation)/[a-z0-9-]+",
            token,
        ):
            found.add(token)
            continue
        if token in {
            "autobrr",
            "paperless-ngx-media",
            "syncthing-data",
            "ntfy",
            "obsidian-livesync",
            "prowlarr-config",
            "prowlarr",
        }:
            found.add(_normalize_claim(token))
    return found


def _section(text: str, heading: str) -> str:
    """Return body text under a markdown heading until the next same/higher heading."""
    # Match AT-N heading; capture until next heading of equal or higher level.
    m = re.search(
        rf"^(#{{1,3}})\s+{re.escape(heading)}\s*$",
        text,
        re.M,
    )
    require(m is not None, f"missing heading: {heading!r}")
    level = len(m.group(1))
    start = m.end()
    nxt = re.search(rf"^#{{1,{level}}}\s+\S", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    return text[start:end]


def parse_part1_classification(doc: str) -> tuple[set[str], set[str]]:
    """Parse Part 1 'What properly…' bullets into cannot-deeper vs complete sets.

    Structure owned by the document:
      - five bullets naming each claim's honest judgment
      - a closing 'So:' paragraph that restates the split
    We take the closing paragraph as the structured classification, and require
    every near-empty claim appears in exactly one side.
    """
    part1 = _section(doc, "Part 1 - the five near-empty claims")
    # Sub-heading lives inside Part 1; search the Part 1 body directly.
    proper = _section(
        "## Part 1 - the five near-empty claims\n" + part1,
        'What "properly" can and cannot mean here',
    )
    # The closing classification paragraph is the contract.
    so = re.search(
        r"So:\s+\*\*`ntfy` and `obsidian-livesync`.*?(?:\n\n|\Z)",
        proper,
        re.S,
    )
    require(so is not None, "Part 1 missing closing 'So:' classification paragraph")
    so_text = so.group(0)

    # complete side: first sentence names ntfy + obsidian-livesync as NOT thin.
    complete_m = re.search(
        r"\*\*`ntfy` and `obsidian-livesync` are not really \"too small to prove much\"\*\*",
        so_text,
    )
    require(
        complete_m is not None,
        "Part 1 must state ntfy and obsidian-livesync are complete, not thin",
    )
    complete = {_normalize_claim("ntfy"), _normalize_claim("obsidian-livesync")}

    # cannot-deeper side: next sentence names the three that stay thin.
    thin_m = re.search(
        r"`autobrr`,\s*`paperless-ngx-media`\s+and\s*`syncthing-data`",
        so_text,
    )
    require(
        thin_m is not None,
        "Part 1 must name autobrr, paperless-ngx-media and syncthing-data as the thin set",
    )
    cannot = {
        _normalize_claim("autobrr"),
        _normalize_claim("paperless-ngx-media"),
        _normalize_claim("syncthing-data"),
    }

    require(
        complete.isdisjoint(cannot),
        f"Part 1 classification overlap: {complete & cannot}",
    )
    require(
        complete | cannot == NEAR_EMPTY_FIVE,
        f"Part 1 classification must cover exactly the five near-empty claims; "
        f"got {(complete | cannot)}",
    )
    return cannot, complete


def parse_verdict_headline(doc: str) -> tuple[int, set[str]]:
    """Extract (count, named-set) from the Verdict 'honest headline' sentence."""
    verdict = _section(doc, "Verdict")
    # Stop before Part 0.
    m = re.search(
        r"The honest headline:\s+\*\*([Tt]hree|[Ff]our|[Ff]ive|[Oo]ne|[Tt]wo)\s+"
        r"of the five near-empty claims\*\*\s*-\s*(.+?)\s*-\s*\*\*cannot be given "
        r"a stronger proof",
        verdict,
        re.S,
    )
    require(
        m is not None,
        "Verdict missing 'honest headline: N of the five near-empty claims - <named> - cannot…'",
    )
    word = m.group(1).lower()
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    count = words[word]
    named = _claims_in(m.group(2))
    # Only keep the five near-empty claims from the named span.
    named &= NEAR_EMPTY_FIVE
    return count, named


def parse_part4_verdicts(doc: str) -> dict[str, str]:
    """Parse Part 4 retirement table into claim -> Ready|Not ready."""
    part4 = _section(doc, "Part 4 - per-claim retirement verdict")
    rows: dict[str, str] = {}
    for line in part4.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        claim_cell, _proof, verdict_cell = cells[0], cells[1], cells[2]
        if claim_cell.lower() in {"claim", "---"} or claim_cell.startswith("-"):
            continue
        claim_m = re.search(r"`([^`]+)`", claim_cell)
        if not claim_m:
            continue
        claim = _normalize_claim(claim_m.group(1))
        # Doc uses **Not ready.** / **Ready.** (period inside the bold markers).
        if re.search(r"\*\*Not ready\.?\*\*", verdict_cell):
            rows[claim] = "Not ready"
        elif re.search(r"\*\*Ready", verdict_cell):
            rows[claim] = "Ready"
    return rows


def parse_skill_f(skill: str) -> tuple[int, set[str], set[str]]:
    """Extract finding (f): count, cannot-deeper named set, complete named set."""
    # Finding (f) is one long paragraph starting with "(f) **Three of the five..."
    m = re.search(
        r"\(f\)\s+\*\*([Tt]hree|[Ff]our|[Ff]ive|[Oo]ne|[Tt]wo)\s+of the five "
        r"\"too small to prove much\" claims\*\*\s*-\s*(.+?)\s*-\s*\*\*cannot be "
        r"given a deeper proof",
        skill,
        re.S,
    )
    require(
        m is not None,
        "skill finding (f) missing 'N of the five \"too small…\" claims - <named> - cannot…'",
    )
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    count = words[m.group(1).lower()]
    cannot = _claims_in(m.group(2)) & NEAR_EMPTY_FIVE

    # After the em-dash clause, the same finding names the complete pair.
    # Find the full (f) paragraph to extract the complete side.
    para_m = re.search(r"\(f\)\s+\*\*.*?(?=\n\n|\n\(g\)|\Z)", skill, re.S)
    require(para_m is not None, "skill finding (f) paragraph not found")
    para = para_m.group(0)
    complete_m = re.search(
        r"`ntfy`'s 2 files are its whole state.*and `obsidian-livesync`'s 8 are a real "
        r"CouchDB vault\s*-\s*complete rather than thin",
        para,
        re.S,
    )
    require(
        complete_m is not None,
        "skill (f) must mark ntfy and obsidian-livesync as complete rather than thin",
    )
    complete = {_normalize_claim("ntfy"), _normalize_claim("obsidian-livesync")}
    return count, cannot, complete


def test_wave_two_deeper_proof_count() -> dict[str, object]:
    require(WAVE_TWO.is_file(), f"missing {WAVE_TWO.relative_to(REPO)}")
    require(SKILL.is_file(), f"missing {SKILL.relative_to(REPO)}")
    doc = WAVE_TWO.read_text()
    skill = SKILL.read_text()

    # 1. Part 1 body classification is the ground truth.
    part1_cannot, part1_complete = parse_part1_classification(doc)
    require(
        part1_cannot == CANNOT_DEEPER,
        f"Part 1 cannot-deeper set drifted: {sorted(part1_cannot)}",
    )
    require(
        part1_complete == COMPLETE,
        f"Part 1 complete set drifted: {sorted(part1_complete)}",
    )

    # 2. Verdict headline count + named set must match Part 1 cannot-deeper.
    headline_count, headline_named = parse_verdict_headline(doc)
    require(
        headline_count == len(CANNOT_DEEPER),
        f"Verdict headline count is {headline_count}, expected {len(CANNOT_DEEPER)}",
    )
    require(
        headline_named == CANNOT_DEEPER,
        f"Verdict headline named set {sorted(headline_named)} != "
        f"cannot-deeper {sorted(CANNOT_DEEPER)}",
    )
    require(
        headline_named.isdisjoint(COMPLETE),
        f"Verdict headline must NOT name complete claims; leaked {headline_named & COMPLETE}",
    )
    require(
        headline_count == len(headline_named),
        f"Verdict headline count ({headline_count}) disagrees with its own "
        f"named set size ({len(headline_named)}: {sorted(headline_named)})",
    )

    # 3. Skill finding (f) must tell the same story.
    skill_count, skill_cannot, skill_complete = parse_skill_f(skill)
    require(
        skill_count == len(CANNOT_DEEPER),
        f"skill (f) count is {skill_count}, expected {len(CANNOT_DEEPER)}",
    )
    require(
        skill_cannot == CANNOT_DEEPER,
        f"skill (f) named cannot-deeper {sorted(skill_cannot)} != {sorted(CANNOT_DEEPER)}",
    )
    require(
        skill_complete == COMPLETE,
        f"skill (f) complete set {sorted(skill_complete)} != {sorted(COMPLETE)}",
    )
    require(
        skill_count == len(skill_cannot),
        f"skill (f) count ({skill_count}) disagrees with its named set "
        f"({len(skill_cannot)}: {sorted(skill_cannot)})",
    )

    # 4. Part 4 retirement table stays coherent with the classification and is
    #    the captain-facing verdict surface - must not have been rewritten by a
    #    count-only fix. Pin the Ready/Not-ready split for the five near-empty
    #    claims (prowlarr is a sixth, separate row).
    part4 = parse_part4_verdicts(doc)
    require(
        "downloads/prowlarr-config" in part4,
        "Part 4 must still carry the prowlarr-config row",
    )
    expected_part4_near_empty = {
        "downloads/autobrr": "Ready",
        "selfhosted/ntfy": "Ready",
        "selfhosted/obsidian-livesync": "Ready",
        "selfhosted/syncthing-data": "Not ready",
        "selfhosted/paperless-ngx-media": "Not ready",
    }
    for claim, expected in expected_part4_near_empty.items():
        require(claim in part4, f"Part 4 missing near-empty claim row: {claim}")
        require(
            part4[claim] == expected,
            f"Part 4 verdict for {claim} is {part4[claim]!r}, expected {expected!r}",
        )

    # 5. No residual wrong count anywhere in the two owned surfaces.
    for label, text in (("wave-two doc", doc), ("skill", skill)):
        bad = re.findall(
            r"[Ff]our of the five(?:\s+near-empty|\s+\"too small to prove much\")?",
            text,
        )
        require(
            not bad,
            f"{label} still carries residual 'four of the five' phrasing: {bad}",
        )

    return {
        "cannot_deeper": sorted(CANNOT_DEEPER),
        "complete": sorted(COMPLETE),
        "headline_count": headline_count,
        "headline_named": sorted(headline_named),
        "skill_count": skill_count,
        "skill_cannot": sorted(skill_cannot),
        "skill_complete": sorted(skill_complete),
        "part4_near_empty": {
            k: part4[k] for k in sorted(expected_part4_near_empty)
        },
        "part4_prowlarr": part4.get("downloads/prowlarr-config"),
    }


def main() -> int:
    try:
        model = test_wave_two_deeper_proof_count()
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: wave-two deeper-proof count is internally consistent")
    print(f"  cannot-deeper ({len(model['cannot_deeper'])}): {model['cannot_deeper']}")
    print(f"  complete      ({len(model['complete'])}): {model['complete']}")
    print(
        f"  headline: {model['headline_count']} of five -> {model['headline_named']}"
    )
    print(
        f"  skill(f): {model['skill_count']} of five -> {model['skill_cannot']} "
        f"(complete={model['skill_complete']})"
    )
    print(f"  part4 near-empty: {model['part4_near_empty']}")
    print(f"  part4 prowlarr:   {model['part4_prowlarr']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
