"""Hermes pre_tool_call guard for the terminal tool: a deterministic seatbelt in front of the model.

Hermes pipes ``{"hook_event_name", "tool_name", "args": {"command": ...}, ...}`` on stdin and reads one
JSON object back: ``{}`` allows, ``{"action": "block", "message": ...}`` refuses, and
``{"action": "approve", "message": ..., "rule_key": ...}`` escalates to the human-approval gate
(unattended cron and gateway runs deny it: approvals.cron_mode / unattended_mode).

Three tiers, matching the approval rule in memories/USER.md:
  BLOCK    never, whoever asks: wiping Hermes' own state, curl | sh, force pushes, raw device writes,
           reading secret files into the conversation.
  APPROVE  ask first: mutating kubectl outside namespace ``ai`` or with -A, deleting volumes / namespaces /
           CRDs / nodes / secrets / snapshots, helm changes (Flux owns releases), Flux suspend / delete.
  ALLOW    everything else.

This is a backstop, not a sandbox: it reads the command string, so execute_code and a determined model can
go around it. It complements ``approvals.mode: smart`` and the pod's RBAC. Standard library only (the image
has no jq). scripts/ci/hermes-hooks-test.py pins the behaviour.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

BLOCK, APPROVE, ALLOW = "block", "approve", "allow"
HOME_NS = "ai"

PROTECTED_RM_TARGETS = frozenset({
    "/", "/*", "~", "~/", "~/*", "$HOME", "$HOME/", "$HOME/*",
    "/opt/data", "/opt/data/", "/opt/data/*", "/opt/data/.*",
})
PROTECTED_STATE = ("state.db", "memories", "sessions", "profiles", "backups", "skills", "cron", "config.yaml")
SECRET_READERS = frozenset({"cat", "less", "more", "head", "tail", "grep", "egrep", "sed", "awk", "strings",
                            "xxd", "od", "base64", "cut", "sort", "diff", "cp", "tar", "zip"})
KUBECTL_MUTATING = frozenset({"delete", "apply", "patch", "replace", "edit", "scale", "drain", "cordon",
                              "uncordon", "taint", "annotate", "label", "create", "set", "rollout"})
KUBECTL_DESTRUCTIVE_KINDS = frozenset({"pvc", "persistentvolumeclaim", "persistentvolumeclaims", "pv",
                                       "persistentvolume", "persistentvolumes", "ns", "namespace", "namespaces",
                                       "crd", "crds", "customresourcedefinition", "customresourcedefinitions",
                                       "node", "nodes", "secret", "secrets", "volumesnapshot", "volumesnapshots",
                                       "snapshot", "snapshots"})
HELM_MUTATING = frozenset({"install", "upgrade", "uninstall", "delete", "rollback"})
FLUX_MUTATING = frozenset({"suspend", "delete", "uninstall", "create", "bootstrap", "install"})
WRAPPERS = frozenset({"sudo", "command", "time", "nohup", "exec"})

CURL_PIPE_SHELL = re.compile(r"\b(?:curl|wget)\b[^|;&\n]*\|\s*(?:sudo\s+)?(?:ba|z|da)?sh\b")
SEGMENT_SPLIT = re.compile(r"\|\||&&|;|\n|\|")


def _segments(command: str) -> list[list[str]]:
    out = []
    for part in SEGMENT_SPLIT.split(command):
        try:
            tokens = shlex.split(part)
        except ValueError:  # unbalanced quote: fall back to whitespace so the rules still see the words
            tokens = part.split()
        while tokens and (tokens[0] in WRAPPERS or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0])):
            tokens = tokens[1:]
        if tokens:
            out.append(tokens)
    return out


def _rm_verdict(args: list[str]) -> tuple[str, str] | None:
    recursive = any(a in ("--recursive",) or (a.startswith("-") and not a.startswith("--") and set(a[1:]) & {"r", "R"})
                    for a in args)
    if not recursive:
        return None
    for target in (a for a in args if not a.startswith("-")):
        direct_state = any(target.rstrip("/") == f"/opt/data/{name}" for name in PROTECTED_STATE)
        if target in PROTECTED_RM_TARGETS or direct_state:
            return BLOCK, f"rm -r on {target} would wipe Hermes' own state or the filesystem root."
    return None


def _namespace(args: list[str]) -> str | None:
    """'' when -A/--all-namespaces, the namespace when given, None when unset (in-cluster default)."""
    for i, a in enumerate(args):
        if a in ("-A", "--all-namespaces"):
            return ""
        if a in ("-n", "--namespace") and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--namespace="):
            return a.split("=", 1)[1]
        if a.startswith("-n") and len(a) > 2 and not a.startswith("--"):
            return a[2:]
    return None


def _prints_secret_values(args: list[str]) -> bool:
    """kubectl get secret(s) with an output format that renders the data."""
    if "get" not in args or not any(a.split("/")[0].lower() in ("secret", "secrets") for a in args if not a.startswith("-")):
        return False
    return any(re.match(r"^(?:-o|--output)[= ]?(?:yaml|json|jsonpath|go-template|template)", a) for a in args) or any(
        prev in ("-o", "--output") and re.match(r"(?:yaml|json|jsonpath|go-template|template)", cur)
        for prev, cur in zip(args, args[1:]))


def _kubectl_verdict(args: list[str]) -> tuple[str, str] | None:
    if _prints_secret_values(args):
        return APPROVE, "Printing Secret values into the conversation needs your approval."
    verb = next((a for a in args if a in KUBECTL_MUTATING), None)
    if verb is None:
        return None
    if verb == "delete":
        kinds = {a.split("/")[0].split(",")[0].lower() for a in args if not a.startswith("-")}
        if kinds & KUBECTL_DESTRUCTIVE_KINDS or "--all" in args:
            return APPROVE, "kubectl delete of volumes, namespaces, CRDs, nodes, secrets, snapshots or --all needs your approval."
    ns = _namespace(args)
    if ns is not None and ns != HOME_NS:
        where = "all namespaces" if ns == "" else f"namespace {ns}"
        return APPROVE, f"kubectl {verb} in {where} is outside '{HOME_NS}' and needs your approval."
    return None


def _segment_verdict(tokens: list[str]) -> tuple[str, str] | None:
    cmd, args = tokens[0].rsplit("/", 1)[-1], tokens[1:]
    if cmd == "rm":
        return _rm_verdict(args)
    if cmd.startswith("mkfs") or (cmd == "dd" and any(a.startswith("of=/dev/") for a in args)):
        return BLOCK, "Raw device writes are never allowed."
    if cmd == "git" and "push" in args and ("--force" in args or "-f" in args):
        return BLOCK, "Force-pushing is never allowed. Use a normal push or --force-with-lease after asking."
    if cmd in SECRET_READERS:
        for a in args:
            if a.startswith("/secrets/") or a == "/secrets" or a.endswith(".git-credentials") \
                    or re.fullmatch(r"/opt/data(?:/profiles/[^/]+)?/\.env", a):
                return BLOCK, f"{a} holds secrets and must not be read into the conversation."
    if cmd == "kubectl":
        return _kubectl_verdict(args)
    if cmd == "helm" and any(a in HELM_MUTATING for a in args):
        return APPROVE, "helm changes bypass GitOps (Flux owns releases) and need your approval."
    if cmd == "flux" and any(a in FLUX_MUTATING for a in args):
        return APPROVE, "Suspending or deleting Flux objects needs your approval."
    return None


def decide(command: str) -> tuple[str, str]:
    """Return (verdict, message) for a terminal command. BLOCK beats APPROVE beats ALLOW."""
    if CURL_PIPE_SHELL.search(command):
        return BLOCK, "Piping a download into a shell is never allowed. Download, read, then run."
    approve: tuple[str, str] | None = None
    for tokens in _segments(command):
        verdict = _segment_verdict(tokens)
        if verdict is None:
            continue
        if verdict[0] == BLOCK:
            return verdict
        approve = approve or verdict
    return approve or (ALLOW, "")


def response(payload: dict) -> dict:
    command = ((payload.get("args") or payload.get("tool_input") or {}).get("command") or "")
    verdict, message = decide(str(command))
    if verdict == BLOCK:
        return {"action": "block", "message": message}
    if verdict == APPROVE:
        return {"action": "approve", "message": message, "rule_key": "guard-terminal"}
    return {}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
    except ValueError as exc:  # fail closed: a payload we cannot read is a command we cannot judge
        print(json.dumps({"action": "block", "message": f"guard-terminal could not read the hook payload: {exc}"}))
        return 0
    print(json.dumps(response(payload)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
