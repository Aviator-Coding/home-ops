#!/usr/bin/env python3
"""Repo-local link checker for docs, skills and the pointers config files carry.

The docs-to-skills restructure moves and deletes a lot of Markdown, and 100+
config files, alert annotations and tests point at those files by path. Nothing
else in CI resolves those pointers, so a deleted doc would leave dangling
references that no gate reports. This test fails on any of:

  1. A relative Markdown link `[text](target)` in a tracked .md file whose
     target does not exist (anchors are stripped; URLs and pure anchors are
     skipped; fenced code blocks are ignored).
  2. A `docs/...md`, `.agents/skills/...` or `.claude/skills/...` path
     mentioned anywhere in a tracked text file that does not exist.
  3. A `references/<file>.md` path mentioned anywhere that no skill carries.
  4. A "skill `<name>`" pointer naming a skill that does not exist.

KNOWN_DANGLING lists mentions that are deliberately left unresolved (history
recorded in a dated report). Each entry needs a reason; an entry that stops
matching anything fails so the list cannot go stale.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / ".agents" / "skills"

TEXT_SUFFIXES = {
    ".md", ".yaml", ".yml", ".json5", ".json", ".j2", ".tofu", ".tf", ".hcl",
    ".py", ".sh", ".js", ".toml", ".just", ".txt", ".conf", ".tpl",
}
TEXT_NAMES = {"Taskfile.yaml", ".justfile", "Dockerfile", ".mise.toml", ".gitignore"}

# (path of the file carrying the mention, mentioned path) -> reason.
KNOWN_DANGLING: dict[tuple[str, str], str] = {
    ("docs/ai-system/agentmemory-retirement-2026-08-31.md", "docs/reference.md"):
        "dated report records which docs it edited; docs/reference.md was replaced by docs/README.md",
    ("docs/ai-system/retired-2026-08-22.md", "docs/reference.md"):
        "dated report records the state of the retired index",
    ("docs/ai-system/agentmemory-retirement-2026-08-31.md", "docs/ai-system/litellm/fallbacks.md"):
        "dated report records which docs it edited; that doc moved into skill litellm-proxy",
    ("docs/backups/autobrr-removal-2026-09-02.md", "docs/reference.md"):
        "dated report records which docs it edited",
    ("docs/grafana-operator-removal.md", "docs/reference.md"):
        "records which docs mentioned grafana-operator at the time",
    ("kubernetes/apps/base/system/kopiur/README.md", "docs/upgrade.md"):
        "upstream kopiur repository's doc, not a path in this repo",
    ("kubernetes/apps/base/rook-ceph/rook-ceph/operator/csi-driver-tolerations.yaml",
     "docs/spec/v1/kustomizations.md"):
        "upstream Flux kustomize-controller doc, not a path in this repo",
}

# Files whose Markdown is runtime payload rather than repo documentation.
EXCLUDED = {
    # Hermes runtime skill shipped via configMapGenerator; its [text](url)
    # links are placeholders the agent fills in, and any edit restarts Hermes.
    "kubernetes/apps/base/ai/hermes/app/skills/homelab-commit-watcher/SKILL.md",
    # This file: its KNOWN_DANGLING keys are the mentions, not pointers.
    "scripts/ci/doc-links-test.py",
}

MD_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
PATH_MENTION = re.compile(
    r"(?<![\w./-])((?:docs/[\w./-]+?\.md)|(?:\.(?:agents|claude)/skills/[\w./-]*[\w-]))(?![\w-])"
)
REF_MENTION = re.compile(r"(?<![\w./-])references/([\w.-]+\.md)")
SKILL_MENTION = re.compile(r"\bskills? `([a-z0-9][a-z0-9-]*)`")


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout.decode()
    return [p for p in out.split("\0") if p]


def is_text(rel: str) -> bool:
    p = Path(rel)
    return p.suffix in TEXT_SUFFIXES or p.name in TEXT_NAMES


def strip_fences(text: str) -> str:
    """Blank out fenced code blocks so example links in them are not checked."""
    out: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        m = re.match(r"^\s*(`{3,}|~{3,})", line)
        if m:
            marker = m.group(1)[0] * 3
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            out.append("")
            continue
        out.append("" if fence else line)
    return "\n".join(out)


def skill_names() -> set[str]:
    return {p.parent.name for p in SKILLS.glob("*/SKILL.md")}


def reference_files() -> set[str]:
    return {p.name for p in SKILLS.glob("*/references/*.md")}


def main() -> int:
    files = tracked_files()
    tracked = set(files)
    skills = skill_names()
    refs = reference_files()
    problems: list[str] = []
    used_known: set[tuple[str, str]] = set()
    checked_links = checked_mentions = 0

    def exists(path: Path) -> bool:
        try:
            rel = path.resolve().relative_to(ROOT.resolve())
        except ValueError:
            return path.exists()
        return path.exists() or str(rel) in tracked

    for rel in files:
        if not is_text(rel) or rel in EXCLUDED:
            continue
        path = ROOT / rel
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text()
        except UnicodeDecodeError:
            continue

        if rel.endswith(".md"):
            for m in MD_LINK.finditer(strip_fences(text)):
                target = m.group(1)
                if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                    continue
                target = target.split("#", 1)[0]
                if not target:
                    continue
                checked_links += 1
                base = ROOT if target.startswith("/") else path.parent
                if not exists(base / target.lstrip("/")):
                    problems.append(f"{rel}: broken link -> {target}")

        for m in PATH_MENTION.finditer(text):
            mention = m.group(1).rstrip(".")
            checked_mentions += 1
            if exists(ROOT / mention):
                continue
            key = (rel, mention)
            if key in KNOWN_DANGLING:
                used_known.add(key)
                continue
            problems.append(f"{rel}: path does not exist -> {mention}")

        for m in REF_MENTION.finditer(text):
            checked_mentions += 1
            if m.group(1) not in refs:
                problems.append(f"{rel}: no skill has references/{m.group(1)}")

        for m in SKILL_MENTION.finditer(text):
            checked_mentions += 1
            if m.group(1) not in skills:
                problems.append(f"{rel}: unknown skill `{m.group(1)}`")

    for key, reason in KNOWN_DANGLING.items():
        if not reason.strip():
            problems.append(f"KNOWN_DANGLING {key} has no reason")
        if key not in used_known:
            problems.append(f"KNOWN_DANGLING {key} no longer matches; remove it")

    for p in problems:
        print(f"[FAIL] {p}")
    print(
        f"checked {checked_links} markdown links and {checked_mentions} path/skill "
        f"mentions across {len(files)} tracked files"
    )
    if problems:
        print(f"Summary: {len(problems)} broken")
        return 1
    print("Summary: 0 broken")
    return 0


if __name__ == "__main__":
    sys.exit(main())
