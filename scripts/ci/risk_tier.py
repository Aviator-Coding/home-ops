#!/usr/bin/env python3
"""Classify a PR's risk tier from its changed paths (goal §5, task 2.2).

Each path takes the highest tier whose pattern matches it, and the PR takes the
highest tier of its paths. A Renovate digest or patch update with no risk:high
path is risk:low. Unknown paths are risk:normal, never low.

CLI (used by .github/workflows/risk-tier.yaml): changed paths on stdin, one per
line; optional `--update <renovate update type>` (the workflow passes it only for a
verified Renovate PR); prints the tier label.
Tested by scripts/ci/risk-tier-test.py.
"""

from __future__ import annotations

import re
import sys

# The goal §5 table, plus the agent guard (.claude/hooks, .claude/settings.json),
# which is a security control like .github/workflows.
HIGH = [
    "talos/**", "bootstrap/**", "terraform/**", "kubernetes/clusters/**",
    "kubernetes/apps/*/flux-system/**", "kubernetes/apps/*/kube-system/**",
    "kubernetes/apps/*/rook-ceph/**", "kubernetes/apps/*/network/**",
    "kubernetes/apps/*/security/**", "kubernetes/apps/*/cert-manager/**",
    "kubernetes/apps/*/system-upgrade/**", "kubernetes/apps/*/renovate/**",
    "kubernetes/components/kopiur/**", "kubernetes/components/volsync/**",
    "kubernetes/apps/base/system/kopiur/**",
    ".github/workflows/**", ".renovate/**",
    # Every filename Renovate reads its config from (some take precedence over .renovaterc.json5).
    ".renovaterc", ".renovaterc.json", ".renovaterc.json5", "renovate.json", "renovate.json5",
    ".github/renovate.json", ".github/renovate.json5", ".gitlab/renovate.json",
    ".claude/hooks/**", ".claude/settings.json",
]
# Files that steer agents or the AI reviewer: never low, so the owner merges them.
STEERING = [
    "AGENTS.md", "CLAUDE.md", "**/AGENTS.md", ".github/ai-review-rules.md", ".claude/**",
    ".agents/**/scripts/**",
]
# Cluster-wide paths a Renovate patch must not auto-merge into (they keep their path tier).
RENOVATE_NOT_LOW = [
    "kubernetes/apps/*/kustomization.yaml", "kubernetes/components/**",
    "kubernetes/apps/*/system/**", "kubernetes/apps/*/system-controller/**",
    "kubernetes/apps/*/actions-runner-system/**", "kubernetes/apps/*/database/**",
]
NORMAL = [
    "kubernetes/**", "scripts/**", ".taskfiles/**", ".justfile", "*/mod.just", ".mise.toml",
]
LOW = ["docs/**", ".agents/**", "*.md"]
LOW_EXCEPT = ["kubernetes/**"]  # *.md under kubernetes/ is not low
RANK = {"risk:low": 0, "risk:normal": 1, "risk:high": 2}


def _regex(glob: str) -> re.Pattern[str]:
    """`**` spans directories, `*` stays inside one segment; a bare `*.md` matches anywhere."""
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    if "/" not in glob:
        out = "(?:.*/)?" + out
    return re.compile(f"^{out}$")


_HIGH = [_regex(g) for g in HIGH]
_NORMAL = [_regex(g) for g in NORMAL]
_LOW = [_regex(g) for g in LOW]
_LOW_EXCEPT = [_regex(g) for g in LOW_EXCEPT]
_STEERING = [_regex(g) for g in STEERING]
_RENOVATE_NOT_LOW = [_regex(g) for g in RENOVATE_NOT_LOW]


def path_tier(path: str) -> str:
    if any(r.match(path) for r in _HIGH):
        return "risk:high"
    if any(r.match(path) for r in _NORMAL) or any(r.match(path) for r in _STEERING):
        return "risk:normal"
    if any(r.match(path) for r in _LOW) and not any(r.match(path) for r in _LOW_EXCEPT):
        return "risk:low"
    return "risk:normal"


def tier(paths: list[str], renovate_update: str | None = None) -> str:
    if not paths:
        return "risk:normal"
    tiers = [path_tier(p) for p in paths]
    highest = max(tiers, key=RANK.__getitem__)
    if (renovate_update in {"digest", "patch"} and highest != "risk:high"
            and not any(r.match(p) for p in paths for r in _RENOVATE_NOT_LOW)
            and not any(r.match(p) for p in paths for r in _STEERING)):
        return "risk:low"
    return highest


def main(argv: list[str]) -> int:
    update = argv[argv.index("--update") + 1] if "--update" in argv else None
    paths = [line.strip() for line in sys.stdin if line.strip()]
    print(tier(paths, update or None))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
