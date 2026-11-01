#!/usr/bin/env python3
"""Block owner-only commands before an agent runs them.

Second layer behind read-only credentials (goal §3 "Enforced, not just
written"): the agent kubeconfig (~/.kube/home-ops-agent) is bound to `view` and
the talosconfig (~/.talos/home-ops-agent) is `os:reader`. For GitHub this guard
is the only layer until agents get their own token (goal task 2.6). It cannot
stop code an agent writes and runs (python -c, a script file); the credential
layer and ruleset are what stop those.

Claude Code calls it as a PreToolUse hook (`.claude/settings.json`): it reads
the hook JSON on stdin and exits 2 with the reason on stderr to block. Other
harnesses (Hermes) can import `is_blocked(command) -> str | None`.

The hook also checks the environment the session inherited (#2208): a
KUBECONFIG/TALOSCONFIG entry that is not an `*-agent` file refuses every
command (`env_credential_reason`), and an unset one refuses the cluster clients
that would fall back to ~/.kube/config or ~/.talos/config
(`fallback_credential_reason`). `is_blocked` stays a function of the command.
It checks the environment the hook receives, as the settings.json command
captures it into GUARD_* before a mise shim can rewrite it; the Bash tool's
shell profile can still change it later. The unset check is best effort: it
matches command words, so indirection (`K=kubectl; $K`, aliases,
`eval "$(...)"`, `npx kubectl`) is not caught.

Stdlib only. Tested by scripts/ci/agent-guard-test.py.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Iterator, Mapping

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
    "-v", "--v", "-o", "--output", "--tls-server-name", "--certificate-authority",
    "--client-certificate", "--client-key", "-l", "--selector", "--field-selector",
}
KUBECTL_WRITES = {
    "apply", "create", "delete", "edit", "patch", "replace", "scale", "annotate",
    "label", "cordon", "uncordon", "drain", "taint", "set", "run", "expose",
    "autoscale",
}
KUBECTL_ROLLOUT_WRITES = {"restart", "undo", "pause", "resume"}

TALOSCTL_VALUE_FLAGS = {"-n", "--nodes", "-e", "--endpoints", "--talosconfig", "--context", "--cluster", "-c"}
TALOSCTL_WRITES = {
    "apply-config", "apply", "upgrade", "upgrade-k8s", "reboot", "reset", "shutdown",
    "edit", "patch", "bootstrap", "rollback", "wipe", "restart",
}
# talosctl <group> <verb> writes.
TALOSCTL_PAIR_WRITES = {
    ("service", "restart"), ("service", "stop"), ("service", "start"),
    ("etcd", "remove-member"), ("etcd", "leave"), ("etcd", "forfeit-leadership"),
    ("etcd", "snapshot-restore"), ("meta", "write"), ("meta", "delete"),
}
FLUX_VALUE_FLAGS = {"-n", "--namespace", "--kubeconfig", "--context", "--timeout"}

# Credential files: agents use only the *-agent files; anything else is the owner's.
CREDENTIAL_FLAGS = {"--kubeconfig": "KUBECONFIG", "--talosconfig": "TALOSCONFIG"}
CREDENTIAL_VARS = {"KUBECONFIG", "TALOSCONFIG"}
ADMIN_MARKERS = ("home-ops-admin", "HOME_OPS_KUBECONFIG", "HOME_OPS_TALOSCONFIG")
UNSET_FLAGS = {"-u", "--unset"}
ENV_CLEAR_FLAGS = {"-i", "-", "--ignore-environment"}
# settings.json captures the raw values here before a mise shim can rewrite them.
GUARD_PREFIX, GUARD_UNSET = "GUARD_", "__unset__"

# Cluster clients and the variable each reads; unset, they fall back to FALLBACK_FILES.
KUBE_CLIENTS = {"kubectl", "kubecolor", "flux", "helm", "helmfile", "k9s", "stern", "cilium"}
FALLBACK_FILES = {"KUBECONFIG": "~/.kube/config", "TALOSCONFIG": "~/.talos/config"}
FRESH_SHELL = ("start agents from a fresh shell in the repo so mise sets "
               "~/.kube/home-ops-agent and ~/.talos/home-ops-agent")

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
    """Commands inside $(...) (nesting included) and backticks."""
    inner: list[str] = re.findall(r"`([^`]*)`", command)
    i = 0
    while True:
        start = command.find("$(", i)
        if start < 0:
            return inner
        depth, j = 1, start + 2
        while j < len(command) and depth:
            if command[j] == "(":
                depth += 1
            elif command[j] == ")":
                depth -= 1
            j += 1
        inner.append(command[start + 2:j - 1])
        i = start + 2


HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def _strip_heredocs(command: str) -> str:
    """Drop heredoc bodies: they are data (commit messages, file contents). A shell
    fed a heredoc is caught as a shell reading stdin."""
    out: list[str] = []
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        out.append(lines[i])
        delims = [m.group(2) for m in HEREDOC.finditer(lines[i])]
        i += 1
        for delim in delims:
            while i < len(lines) and lines[i].strip() != delim:
                i += 1
            i += 1
    return "\n".join(out)


def _credential_reason(words: list[str]) -> str | None:
    """Explicit credential paths other than the agent files, and admin overrides."""
    for k, word in enumerate(words):
        if any(marker in word for marker in ADMIN_MARKERS):
            return "references the owner's admin credentials; agents use ~/.kube/home-ops-agent"
        name, eq, value = word.partition("=")
        name = name.removesuffix("+")  # KUBECONFIG+=:x appends an entry
        if eq and (name in CREDENTIAL_FLAGS or name in CREDENTIAL_VARS):
            candidate = value
        elif word in CREDENTIAL_FLAGS and k + 1 < len(words):
            name, candidate = word, words[k + 1]
        else:
            continue
        # Every list entry is an agent file or the variable itself ($KUBECONFIG); empty means unset.
        var = CREDENTIAL_FLAGS.get(name, name)
        entries = [entry for entry in candidate.split(":") if entry]
        if entries and all(entry in {f"${var}", f"${{{var}}}"} or _basename(entry).endswith("-agent")
                           for entry in entries):
            continue
        return f"{name} points at {candidate!r}, not an agent credential file"
    return None


def _env_effects(prefix: list[str]) -> tuple[bool, set[str]]:
    """(environment cleared, variables unset) by `env` in a command's wrapper words."""
    cleared, unset = False, set()
    i = 0
    while i < len(prefix):
        i += 1
        if _basename(prefix[i - 1]) != "env":
            continue
        while i < len(prefix) and prefix[i].startswith("-"):
            flag = prefix[i]
            i += 1
            if flag in UNSET_FLAGS and i < len(prefix):
                unset.add(prefix[i])
                i += 1
            elif flag.startswith("--unset="):
                unset.add(flag.partition("=")[2])
            elif flag.startswith("-u") and not flag.startswith("--"):
                unset.add(flag[2:])
            elif flag in ENV_CLEAR_FLAGS or (not flag.startswith("--") and "i" in flag[1:]):
                cleared = True
            elif flag in WRAPPERS["env"] and i < len(prefix):
                i += 1
    return cleared, unset


def _unset_reason(words: list[str]) -> str | None:
    """Unsetting, un-exporting or clearing a credential variable: the client then reads
    the owner's default file instead."""
    stripped = _strip_wrappers(words)
    cleared, unset = _env_effects(words[:len(words) - len(stripped)])
    if stripped:
        prog, args = _basename(stripped[0]), stripped[1:]
        if (prog == "unset"
                or (prog == "export" and any(w[:1] == "-" and w[:2] != "--" and "n" in w for w in args))
                or (prog in {"typeset", "declare"} and any(w[:1] == "+" and "x" in w for w in args))):
            unset.update(args)
    hit = sorted(unset & CREDENTIAL_VARS)
    if hit:
        return f"unsetting {' and '.join(hit)} makes the client fall back to the owner's default config"
    client = _client_var(stripped) if cleared else None
    if client:
        return f"env -i clears KUBECONFIG and TALOSCONFIG, so {client[1]} would read the owner's default config"
    return None


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


def _nested(words: list[str]) -> tuple[str | None, str | None]:
    """For a shell or eval: (the command it runs, None), or (None, why it cannot be read)."""
    words = _strip_wrappers(words)
    if not words:
        return None, None
    prog = _basename(words[0])
    args = words[1:]
    if prog == "eval":
        return " ".join(args), None
    if prog not in SHELLS:
        return None, None
    if "<<<" in args:
        i = args.index("<<<")
        return (args[i + 1] if i + 1 < len(args) else None), None
    if any(w in {"<", "<<"} for w in args):
        return None, f"{prog} reading commands from a redirect cannot be checked; pass them with -c"
    for i, word in enumerate(args):
        if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
            rest = [w for w in args[i + 1:] if w != "--"]
            return (rest[0] if rest else None), None
    if not [w for w in args if not w.startswith("-")]:
        return None, f"{prog} reading commands from stdin cannot be checked; pass them with -c"
    return None, None


def _walk(command: str, depth: int = 0) -> Iterator[tuple[list[str], str | None]]:
    """Every simple command, inside shells, eval and $(...) too, as (words, None);
    ([], reason) where part of the command cannot be read."""
    if depth > 5:
        yield [], "command nests shells too deeply to check"
        return
    command = _strip_heredocs(command)
    try:
        segments = _segments(command)
    except ValueError:
        yield [], "command could not be parsed (unbalanced quotes); not run"
        return
    for words in segments:
        yield words, None
        inner, reason = _nested(words)
        if reason:
            yield [], reason
        elif inner is not None:
            yield from _walk(inner, depth + 1)
    for inner in _substitutions(command):
        yield from _walk(inner, depth + 1)


def _check_simple(words: list[str]) -> str | None:
    reason = _credential_reason(words) or _unset_reason(words)
    if reason:
        return reason
    words = _strip_wrappers(words)
    if not words:
        return None
    prog = _basename(words[0])

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
        if len(pos) > 1 and any((pos[0], verb) in TALOSCTL_PAIR_WRITES for verb in pos[1:3]):
            return f"talosctl {' '.join(pos[:3])} changes a node; only the owner runs it"
        return None

    if prog in {"tofu", "terraform"}:
        pos = _positional(words, set())
        if pos and pos[0] in {"apply", "destroy", "import", "taint", "untaint", "force-unlock"}:
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
            if action == "graphql":
                if any("mutation" in w for w in words):
                    return "gh api graphql mutation changes GitHub state; owner-only"
                return None
            if method is None and any(
                w[:2] in {"-f", "-F"} or w.split("=", 1)[0] in GH_API_FIELD_FLAGS for w in words[2:]
            ):
                return "gh api with -f/-F/--input defaults to POST; agents read with plain gh api"
            return None
        if sub == "alias" and action in {"set", "import"}:
            return "gh alias set can hide a write behind a new name; owner-only"
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
        if pos and pos[0] == "config" and any(p.startswith("alias.") for p in pos[1:]):
            return "git config alias.* can hide a write behind a new name; owner-only"
        if pos and pos[0] == "push":
            flags = set(words)
            clustered = [w for w in words if re.match(r"^-[A-Za-z]{2,}$", w)]
            if (flags & GIT_FORCE_FLAGS or any(w.startswith("--force") for w in words)
                    or any("f" in w or "d" in w for w in clustered)):
                return "git push --force/--delete rewrites or deletes remote history; owner-only"
            if any(p.startswith("+") or p.startswith(":") for p in pos[1:]):
                return "git push +ref or :ref force-pushes or deletes a remote branch; owner-only"
        return None

    if prog == "flux":
        pos = _positional(words, FLUX_VALUE_FLAGS)
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


def is_blocked(command: str) -> str | None:
    """Return why `command` is owner-only, or None when an agent may run it."""
    for words, reason in _walk(command):
        reason = reason or _check_simple(words)
        if reason:
            return reason
    return None


def env_credential_reason(environ: Mapping[str, str]) -> str | None:
    """Why the inherited KUBECONFIG/TALOSCONFIG are not agent files, or None.
    Empty values and empty list entries count as unset."""
    stale = sorted(
        var for var in CREDENTIAL_VARS
        if any(not _basename(entry).endswith("-agent")
               for entry in environ.get(var, "").split(os.pathsep) if entry)
    )
    if not stale:
        return None
    return (f"this session inherited {' and '.join(stale)} naming a file that is not an agent "
            f"credential (*-agent); {FRESH_SHELL}")


def _client_var(words: list[str]) -> tuple[str, str] | None:
    """(credential variable, program) when `words` runs a cluster client."""
    words = _strip_wrappers(words)
    if not words:
        return None
    prog = _basename(words[0])
    if prog in KUBE_CLIENTS or prog.startswith("kubectl-"):
        return "KUBECONFIG", prog
    if prog == "talosctl":
        return "TALOSCONFIG", prog
    if prog == "just":
        pos = _positional(words, set())
        if pos and (pos[0] == "kube" or pos[0].startswith("kube::")):
            return "KUBECONFIG", "just kube"
    return None


def fallback_credential_reason(command: str, environ: Mapping[str, str]) -> str | None:
    """Why `command` would run a cluster client whose variable is unset or empty (so it
    reads the owner's default file), or None. Fails closed on unreadable commands."""
    missing = {var for var in CREDENTIAL_VARS if not environ.get(var)}
    if not missing:
        return None
    for words, reason in _walk(command):
        if reason:
            return reason
        client = _client_var(words)
        if client and client[0] in missing:
            var, prog = client
            return f"{var} is not set, so {prog} would read {FALLBACK_FILES[var]} (owner credentials); {FRESH_SHELL}"
    return None


def hook_environ(environ: Mapping[str, str]) -> dict[str, str]:
    """`environ` with the GUARD_* values the settings.json command captured before
    python started (a mise shim rewrites KUBECONFIG/TALOSCONFIG to the agent files)."""
    out = dict(environ)
    for var in CREDENTIAL_VARS:
        raw = environ.get(GUARD_PREFIX + var)
        if raw == GUARD_UNSET:
            out.pop(var, None)
        elif raw is not None:
            out[var] = raw
    return out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("Blocked by .claude/hooks/guard.py: unreadable hook input", file=sys.stderr)
        return 2
    if payload.get("tool_name") != "Bash":
        return 0
    admin = [v for v in ("HOME_OPS_KUBECONFIG", "HOME_OPS_TALOSCONFIG") if os.environ.get(v)]
    if admin:
        print(f"Blocked by .claude/hooks/guard.py: this session was started with {', '.join(admin)} "
              "set (owner admin credentials); start agents from a shell without it", file=sys.stderr)
        return 2
    command = (payload.get("tool_input") or {}).get("command") or ""
    environ = hook_environ(os.environ)
    reason = (env_credential_reason(environ) or is_blocked(command)
              or fallback_credential_reason(command, environ))
    if reason:
        print(f"Blocked by .claude/hooks/guard.py: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail closed: exit 1 would let the command run
        print(f"Blocked by .claude/hooks/guard.py: guard error {exc!r}", file=sys.stderr)
        sys.exit(2)
