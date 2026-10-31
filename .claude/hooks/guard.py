#!/usr/bin/env python3
"""Block owner-only commands before an agent runs them.

Second layer behind read-only credentials (goal §3 "Enforced, not just
written"): the agent kubeconfig is bound to `view`, the talosconfig is
`os:reader` and the GitHub token has no Administration permission. This guard
stops the same writes earlier, with a reason the agent can read.

Claude Code calls it as a PreToolUse hook (`.claude/settings.json`): it reads
the hook JSON on stdin and exits 2 with the reason on stderr to block. Other
harnesses (Hermes) can import `is_blocked(command) -> str | None`.

Stdlib only. Tested by scripts/ci/agent-guard-test.py.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys

SEPARATORS = {"&&", "||", ";", "|", "&", "|&", ";;", "(", ")"}

# Wrappers that run the rest of the words as a command: name -> flags taking a value.
WRAPPERS: dict[str, set[str]] = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "command": set(),
    "builtin": set(),
    "exec": set(),
    "nohup": set(),
    "sudo": {"-u", "-g", "-C", "-D"},
    "time": set(),
    "nice": {"-n", "--adjustment"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-n", "-I", "-L", "-P", "-d", "-E", "-s", "-a"},
    "watch": {"-n", "--interval", "-d"},
    "stdbuf": {"-i", "-o", "-e"},
}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
# Shell keywords that can start a simple command inside a loop or condition.
KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "}"}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

KUBECTL_VALUE_FLAGS = {
    "-n", "--namespace", "--kubeconfig", "--context", "--cluster", "--user", "-s",
    "--server", "--token", "--as", "--as-group", "--request-timeout", "--cache-dir",
}
KUBECTL_WRITES = {
    "apply", "create", "delete", "edit", "patch", "replace", "scale", "annotate",
    "label", "cordon", "uncordon", "drain", "taint", "set", "run", "expose",
    "autoscale",
}
KUBECTL_ROLLOUT_WRITES = {"restart", "undo", "pause", "resume"}

TALOSCTL_VALUE_FLAGS = {"-n", "--nodes", "-e", "--endpoints", "--talosconfig", "--context", "--cluster"}
TALOSCTL_WRITES = {
    "apply-config", "apply", "upgrade", "upgrade-k8s", "reboot", "reset", "shutdown",
    "edit", "patch", "bootstrap", "rollback", "wipe",
}

GIT_VALUE_FLAGS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
GIT_FORCE_FLAGS = {"--force", "-f", "--force-with-lease", "--force-if-includes", "--delete", "-d", "--mirror", "--prune"}

FLUX_WRITES = {"suspend", "resume", "reconcile", "delete", "uninstall", "bootstrap", "create"}
HELM_WRITES = {"install", "upgrade", "uninstall", "delete", "rollback"}
JUST_TALOS_WRITES = {"apply-node", "upgrade-node", "upgrade-k8s", "reboot-node", "shutdown-node", "reset-node"}

GH_API_FIELD_FLAGS = {"-f", "--raw-field", "-F", "--field", "--input"}


def _basename(word: str) -> str:
    return os.path.basename(word)


def _segments(command: str) -> list[list[str]]:
    """Split a shell command line into simple commands (lists of words)."""
    lexer = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    out: list[list[str]] = [[]]
    for token in lexer:
        if token in SEPARATORS or set(token) <= set("&|;()"):
            out.append([])
        else:
            out[-1].append(token)
    return [seg for seg in out if seg]


def _substitutions(command: str) -> list[str]:
    """Commands inside $(...) and backticks, which shlex keeps as plain words."""
    inner = re.findall(r"\$\(([^()]*)\)", command)
    inner += re.findall(r"`([^`]*)`", command)
    return inner


def _strip_wrappers(words: list[str]) -> list[str]:
    i = 0
    while i < len(words):
        word = words[i]
        name = _basename(word)
        if ASSIGNMENT.match(word) or word in KEYWORDS:
            i += 1
        elif name in WRAPPERS:
            i += 1
            value_flags = WRAPPERS[name]
            while i < len(words) and (words[i].startswith("-") or ASSIGNMENT.match(words[i])):
                flag = words[i]
                i += 1
                if flag in value_flags and i < len(words):
                    i += 1
            if name == "timeout" and i < len(words) and re.match(r"^\d+[smhd]?$", words[i]):
                i += 1
            if name == "nice" and i < len(words) and re.match(r"^-?\d+$", words[i]):
                i += 1
        elif name == "mise" and i + 1 < len(words) and words[i + 1] in {"exec", "x"}:
            rest = words[i + 2:]
            if "--" not in rest:
                return []
            i = i + 2 + rest.index("--") + 1
        else:
            break
    return words[i:]


def _positional(words: list[str], value_flags: set[str]) -> list[str]:
    """Non-flag words after the program, skipping the values of known value flags."""
    out: list[str] = []
    i = 1
    while i < len(words):
        word = words[i]
        if word == "--":
            out.extend(words[i + 1:])
            break
        if word.startswith("-"):
            if "=" not in word and word in value_flags:
                i += 1
        else:
            out.append(word)
        i += 1
    return out


def _gh_api_method(words: list[str]) -> str | None:
    for i, word in enumerate(words):
        if word in {"-X", "--method"} and i + 1 < len(words):
            return words[i + 1].upper()
        if word.startswith("--method="):
            return word.split("=", 1)[1].upper()
        if word.startswith("-X") and len(word) > 2:
            return word[2:].lstrip("=").upper()
    return None


def _check_simple(words: list[str], depth: int) -> str | None:
    words = _strip_wrappers(words)
    if not words:
        return None
    prog = _basename(words[0])

    if prog in SHELLS:
        for i, word in enumerate(words[1:], start=1):
            if word.startswith("-") and "c" in word.lstrip("-") and i + 1 < len(words):
                return _check(words[i + 1], depth + 1)
        return None
    if prog == "eval":
        return _check(" ".join(words[1:]), depth + 1)

    if prog == "kubectl":
        pos = _positional(words, KUBECTL_VALUE_FLAGS)
        if pos and pos[0] in KUBECTL_WRITES:
            return f"kubectl {pos[0]} is a cluster write; only the owner runs it"
        if len(pos) > 1 and pos[0] == "rollout" and pos[1] in KUBECTL_ROLLOUT_WRITES:
            return f"kubectl rollout {pos[1]} is a cluster write; only the owner runs it"
        return None

    if prog == "talosctl":
        pos = _positional(words, TALOSCTL_VALUE_FLAGS)
        if pos and pos[0] in TALOSCTL_WRITES:
            return f"talosctl {pos[0]} changes a node; only the owner runs it"
        return None

    if prog in {"tofu", "terraform"}:
        pos = _positional(words, set())
        if pos and pos[0] in {"apply", "destroy", "import"}:
            return f"{prog} {pos[0]} changes live infrastructure; only the owner runs it"
        if len(pos) > 1 and pos[0] == "state" and pos[1] in {"rm", "mv", "push", "replace-provider"}:
            return f"{prog} state {pos[1]} edits live state; only the owner runs it"
        return None

    if prog == "gh":
        pos = _positional(words, {"-R", "--repo", "--hostname"})
        if not pos:
            return None
        sub = pos[0]
        action = pos[1] if len(pos) > 1 else ""
        if sub == "pr" and action == "merge":
            return "gh pr merge: agents never merge (the risk tier decides who does)"
        if sub == "api":
            method = _gh_api_method(words)
            if method and method != "GET":
                return f"gh api -X {method} changes GitHub state; agents read with plain gh api"
            if method is None and any(w.split("=", 1)[0] in GH_API_FIELD_FLAGS for w in words):
                return "gh api with -f/-F/--input defaults to POST; agents read with plain gh api"
            return None
        if sub == "ruleset":
            return "gh ruleset: rulesets are owner-only"
        if sub == "label" and action in {"create", "edit", "delete", "clone"}:
            return f"gh label {action}: labels are owner-only"
        if sub == "repo" and action in {"edit", "delete", "rename", "archive", "unarchive"}:
            return f"gh repo {action}: repo settings are owner-only"
        if sub == "workflow" and action in {"enable", "disable"}:
            return f"gh workflow {action}: workflow state is owner-only"
        if sub in {"secret", "variable"} and action in {"set", "delete", "remove"}:
            return f"gh {sub} {action}: repo secrets and variables are owner-only"
        return None

    if prog == "git":
        pos = _positional(words, GIT_VALUE_FLAGS)
        if pos and pos[0] == "push":
            flags = set(words)
            if flags & GIT_FORCE_FLAGS or any(w.startswith("--force") for w in words):
                return "git push --force/--delete rewrites or deletes remote history; owner-only"
            if any(p.startswith("+") or p.startswith(":") for p in pos[1:]):
                return "git push +ref or :ref force-pushes or deletes a remote branch; owner-only"
        return None

    if prog == "flux":
        pos = _positional(words, {"-n", "--namespace", "--kubeconfig", "--context"})
        if pos and pos[0] in FLUX_WRITES:
            return f"flux {pos[0]} is a cluster write; only the owner runs it"
        return None

    if prog == "helm":
        pos = _positional(words, {"-n", "--namespace", "--kubeconfig", "--kube-context"})
        if pos and pos[0] in HELM_WRITES:
            return f"helm {pos[0]} is a cluster write; Flux owns releases"
        return None

    if prog == "just":
        pos = _positional(words, set())
        if pos and pos[0].startswith("bootstrap"):
            return "just bootstrap is DR/first-install only; owner-only"
        if len(pos) > 1 and pos[0] == "talos" and pos[1] in JUST_TALOS_WRITES:
            return f"just talos {pos[1]} changes a node; only the owner runs it"
        return None

    if prog == "task":
        pos = _positional(words, set())
        if pos and pos[0] == "reconcile":
            return "task reconcile forces a Flux sync; only the owner runs it"
        return None

    return None


def _check(command: str, depth: int = 0) -> str | None:
    if depth > 5:
        return "command nests shells too deeply to check"
    try:
        segments = _segments(command)
    except ValueError:
        return "command could not be parsed (unbalanced quotes); not run"
    for words in segments:
        reason = _check_simple(words, depth)
        if reason:
            return reason
    for inner in _substitutions(command):
        reason = _check(inner, depth + 1)
        if reason:
            return reason
    return None


def is_blocked(command: str) -> str | None:
    """Return why `command` is owner-only, or None when an agent may run it."""
    return _check(command)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    reason = is_blocked(command)
    if reason:
        print(f"Blocked by .claude/hooks/guard.py: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
