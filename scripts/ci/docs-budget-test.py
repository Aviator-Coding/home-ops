#!/usr/bin/env python3
"""Hard size budget for always-loaded agent context, skills and config comments.

AGENTS.md is loaded into every agent session, and it grew from 6.5 KB to
125 KB in two months while carrying a written "keep this small" rule. A written
rule did not hold, so this gate enforces the budgets instead:

  agents_md_bytes          AGENTS.md size                          <= 20000
  skill_description_chars  each skill's frontmatter description    <= 400
  skill_md_lines           each .agents/skills/*/SKILL.md          <= 250
  reference_md_lines       each .agents/skills/*/references/*.md   <= 600
  comment_block_lines      longest contiguous comment block in a
                           *.yaml / *.yml / *.yaml.j2 / *.json5 file <= 15

Functional comment lines (`yaml-language-server:`, `renovate:`) and blank lines
end a comment block and are not counted.

scripts/ci/docs-budget.json holds only an allowlist of {path, metric, limit, reason} entries.
An entry raises one item's limit above its budget for a genuine exception (for example a
ConfigMap payload a workload reads at runtime). The reason is required, the limit
must exceed the budget, and an entry whose path is gone or now within budget is
itself a failure. There is no baseline: every other item must meet its budget.

The same file carries the skill consistency checks: every skill directory has
a SKILL.md whose frontmatter name matches it, is listed in AGENTS.md's SKILL
INDEX, and .claude/skills is a symlink to .agents/skills.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "scripts" / "ci" / "docs-budget.json"
AGENTS = ROOT / "AGENTS.md"
SKILLS = ROOT / ".agents" / "skills"
CLAUDE_SKILLS = ROOT / ".claude" / "skills"

BUDGETS = {
    "agents_md_bytes": 20000,
    "skill_description_chars": 400,
    "skill_md_lines": 250,
    "reference_md_lines": 600,
    "comment_block_lines": 15,
}

COMMENT_FILE = re.compile(r"\.(ya?ml|yaml\.j2|json5)$")
FUNCTIONAL = re.compile(r"(?:#|//)\s*(?:yaml-language-server:|renovate:)")
DESCRIPTION = re.compile(r"^description:\s*(.*)$", re.M)
NAME = re.compile(r"^name:\s*(\S+)\s*$", re.M)

passed = 0
failed = 0


def record(ok: bool, msg: str) -> None:
    global passed, failed
    if ok:
        passed += 1
    else:
        failed += 1
        print(f"[FAIL] {msg}")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [p for p in out.splitlines() if (ROOT / p).is_file()]


def frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return ""
    end = text.find("\n---", 4)
    return text[4:end] if end > 0 else ""


def description_of(skill_md: Path) -> str:
    m = DESCRIPTION.search(frontmatter(skill_md.read_text()))
    if not m:
        return ""
    value = m.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def longest_comment_block(text: str, marker: str) -> int:
    longest = run = 0
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(marker) and not FUNCTIONAL.search(stripped):
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    return longest


def measure() -> dict[str, dict[str, int]]:
    """Every measured item: metric -> path -> value."""
    values: dict[str, dict[str, int]] = {m: {} for m in BUDGETS}
    values["agents_md_bytes"]["AGENTS.md"] = AGENTS.stat().st_size
    for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
        rel = str(skill_md.relative_to(ROOT))
        values["skill_description_chars"][rel] = len(description_of(skill_md))
        values["skill_md_lines"][rel] = len(skill_md.read_text().splitlines())
    for ref in sorted(SKILLS.glob("*/references/*.md")):
        values["reference_md_lines"][str(ref.relative_to(ROOT))] = len(
            ref.read_text().splitlines()
        )
    for rel in tracked_files():
        if not COMMENT_FILE.search(rel) or (ROOT / rel).is_symlink():
            continue
        marker = "//" if rel.endswith(".json5") else "#"
        try:
            text = (ROOT / rel).read_text()
        except UnicodeDecodeError:
            continue
        values["comment_block_lines"][rel] = longest_comment_block(text, marker)
    return values


def load_config() -> dict:
    return json.loads(CONFIG.read_text())


def check_budgets(values: dict[str, dict[str, int]], cfg: dict) -> None:
    unknown = set(cfg) - {"allowlist"}
    record(not unknown, f"docs-budget.json: only an allowlist is allowed, found {sorted(unknown)}")
    allow: dict[tuple[str, str], int] = {}
    for entry in cfg.get("allowlist", []):
        key = (entry.get("metric", ""), entry.get("path", ""))
        record(key[0] in BUDGETS, f"allowlist {key}: unknown metric")
        record(bool(str(entry.get("reason", "")).strip()), f"allowlist {key}: reason is required")
        limit = entry.get("limit")
        record(
            isinstance(limit, int) and limit > BUDGETS.get(key[0], 0),
            f"allowlist {key}: limit must be an integer above the budget",
        )
        measured = values.get(key[0], {}).get(key[1])
        record(measured is not None, f"allowlist {key}: path is not measured any more")
        if measured is not None:
            record(
                measured > BUDGETS[key[0]],
                f"allowlist {key}: {measured} is within budget {BUDGETS[key[0]]} - remove the entry",
            )
        allow[key] = limit if isinstance(limit, int) else 0

    for metric, budget in BUDGETS.items():
        for path, value in values[metric].items():
            if (metric, path) in allow:
                limit = allow[(metric, path)]
                record(value <= limit, f"{metric} {path}: {value} > allowlisted {limit}")
            else:
                record(value <= budget, f"{metric} {path}: {value} > budget {budget}")


def check_skills() -> None:
    record(
        CLAUDE_SKILLS.is_symlink() and CLAUDE_SKILLS.resolve() == SKILLS.resolve(),
        ".claude/skills must be a symlink to .agents/skills",
    )
    agents_text = AGENTS.read_text()
    index = agents_text.split("## SKILL INDEX", 1)
    record(len(index) == 2, "AGENTS.md must have a SKILL INDEX section")
    index_text = index[1].split("\n## ", 1)[0] if len(index) == 2 else ""
    listed = set(re.findall(r"^\| `([a-z0-9-]+)` \|", index_text, re.M))
    dirs = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    for d in dirs:
        skill_md = d / "SKILL.md"
        record(skill_md.is_file(), f"{d.name}: missing SKILL.md")
        if not skill_md.is_file():
            continue
        fm = frontmatter(skill_md.read_text())
        m = NAME.search(fm)
        record(bool(m) and m.group(1) == d.name, f"{d.name}: frontmatter name must equal the directory name")
        record(bool(description_of(skill_md)), f"{d.name}: frontmatter description is required")
        record(d.name in listed, f"{d.name}: not listed in AGENTS.md SKILL INDEX")
    names = {d.name for d in dirs}
    for name in sorted(listed - names):
        record(False, f"AGENTS.md SKILL INDEX lists `{name}`, which has no skill directory")


def main() -> int:
    values = measure()
    cfg = load_config()
    check_budgets(values, cfg)
    check_skills()
    print(
        f"measured {sum(len(v) for v in values.values())} items; "
        f"{len(cfg.get('allowlist', []))} allowlisted"
    )
    print(f"Summary: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
