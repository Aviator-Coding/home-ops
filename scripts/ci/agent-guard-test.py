#!/usr/bin/env python3
"""Behavioral test for the agent command guard (.claude/hooks/guard.py).

Prevents: an agent session running a cluster, Talos, Terraform or GitHub
settings write that only the owner may run (goal §3 "Never without the
owner's explicit go-ahead"), including through wrappers such as `bash -c`,
`env X=1`, `git -C .` or `&&` chains.

Imports `is_blocked(command) -> str | None` from the hook file and checks:

  1. every write form in BLOCKED returns a reason;
  2. every read form in ALLOWED returns None;
  3. the hook entrypoint exits 2 with the reason on stderr for a blocked
     command, and 0 for an allowed one, given Claude Code's hook JSON;
  4. the inherited environment (#2208): a KUBECONFIG/TALOSCONFIG entry that is
     not an agent file refuses every command, and an unset one refuses the
     cluster clients that would fall back to the owner's ~/.kube/config or
     ~/.talos/config. Entrypoint runs pass an explicit env (CI has none),
     including through the exact .claude/settings.json hook command, whose
     GUARD_* capture must win over a KUBECONFIG a mise shim rewrote.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / ".claude" / "hooks" / "guard.py"

BLOCKED = [
    "kubectl apply -f x.yaml",
    "kubectl -n ai delete pod x",
    "kubectl --kubeconfig kubeconfig patch hr x",
    "env FOO=1 kubectl delete ns x",
    "bash -c 'kubectl apply -f -'",
    "ls && kubectl replace -f x",
    "kubectl -n ai rollout restart deploy/x",
    "kubectl scale deploy x --replicas=0",
    "kubectl cordon talos-1",
    "kubectl annotate ks x reconcile.fluxcd.io/requestedAt=now",
    "talosctl -n 10.10.10.11 reboot",
    "talosctl apply-config -f c.yaml",
    "talosctl -n 10.10.10.11 upgrade --image x",
    "tofu -chdir=terraform/authentik apply",
    "terraform destroy",
    "gh pr merge 12 --squash",
    "gh api -XPUT repos/a/b/rulesets/1",
    "gh api repos/a/b/rulesets/1 --method=PATCH",
    "gh api -X DELETE repos/a/b/git/refs/heads/x",
    "gh api --method POST repos/a/b/labels",
    "gh label create risk:low",
    "gh repo edit --enable-auto-merge",
    "git -C . push --force",
    "git push -f origin x",
    "git push origin +main",
    "git push --force-with-lease",
    "flux suspend ks renovate",
    "flux resume hr x -n ai",
    "flux reconcile ks cluster-apps --with-source",
    "timeout 30 kubectl delete pod x",
    "mise exec -- kubectl apply -f x",
    "echo ok; sh -c \"talosctl reset --graceful=false\"",
    "for p in a b; do kubectl delete pod $p; done",
    # Admin credentials, explicit or through the owner override (review of #2154).
    "kubectl --kubeconfig kubeconfig get secret -A -o yaml",
    "kubectl --kubeconfig=/Users/x/Projects/home-ops/kubeconfig get secret -A",
    "KUBECONFIG=./kubeconfig kubectl get secret -A",
    "talosctl --talosconfig talos/talosconfig -n 10.10.10.11 get members",
    "cat ~/.kube/home-ops-admin",
    "HOME_OPS_KUBECONFIG=~/x mise exec -- python3 x.py",
    # Shell, flag and quoting forms.
    "bash <<< 'kubectl delete pod x'",
    "echo 'kubectl delete pod x' | bash",
    "bash --norc -c 'kubectl delete pod x'",
    "bash -c -- 'kubectl delete pod x'",
    "kubectl -v 6 delete pod x",
    "kubectl -o yaml delete pod x",
    "flux --timeout 5m suspend ks x",
    "talosctl -c home -n 10.10.10.11 reboot",
    "talosctl -n 10.10.10.11 service kubelet restart",
    "talosctl -n 10.10.10.11 etcd remove-member talos-2",
    "tofu taint x",
    "git push -uf origin x",
    "gh api repos/a/b/labels -fname=x",
    "echo $(echo $(kubectl delete pod x))",
    "gh alias set m 'pr merge'",
    "git config alias.pf 'push --force'",
    "gh api graphql -f query='mutation{deleteRef(input:{refId:\"x\"}){clientMutationId}}'",
    "echo $(kubectl delete pod x)",
    "gh api repos/a/b/issues -f title=x",
    "git push origin :feat/x",
    "helm upgrade x oci://y",
    "just talos apply-node talos-1",
    # Unsetting a credential var makes the client fall back to the owner's default file.
    "unset KUBECONFIG; kubectl get pods",
    "env -u KUBECONFIG kubectl get pods -A",
    "env --unset=TALOSCONFIG talosctl version",
    "env -uKUBECONFIG kubectl get pods",
    "export -n KUBECONFIG; kubectl get pods",
    "typeset +x KUBECONFIG",
    "declare +x TALOSCONFIG",
    # env -i / env - clear both variables (review of #2208).
    "env -i HOME=/Users/aviator /opt/homebrew/bin/kubectl get secret -A",
    "env - kubectl get pods",
    "env --ignore-environment talosctl version",
    "env -iv flux get ks -A",
    "timeout 5 env -i helm list -A",
    # $-values other than the variable itself, lists and += (review of #2208).
    "KUBECONFIG=$HOME/.kube/config kubectl get pods",
    "KUBECONFIG=$KUBECONFIG:$HOME/.kube/config kubectl get pods",
    "kubectl --kubeconfig=$HOME/.kube/config get pods",
    "kubectl --kubeconfig ${HOME}/.kube/config get pods",
    "KUBECONFIG+=:/Users/aviator/.kube/config kubectl get pods",
    "export KUBECONFIG+=:/Users/aviator/.kube/config",
    "TALOSCONFIG=$KUBECONFIG talosctl version",
    "KUBECONFIG= kubectl get pods",
]

ALLOWED = [
    "kubectl get pods -A",
    "kubectl -n ai logs deploy/x",
    "kubectl auth can-i create pods",
    "kubectl kustomize kubernetes/apps/base/ai",
    "kubectl --kubeconfig \"$KUBECONFIG\" get pods -A",
    "kubectl --kubeconfig ~/.kube/home-ops-agent get pods -A",
    "gh ruleset list",
    "gh api graphql -f query='query{viewer{login}}'",
    "git commit -m \"$(cat <<'EOF'\nfix: x\n\nkubectl apply is owner-only\nEOF\n)\"",
    "kubectl logs x -c app -n ai",
    "talosctl -n 10.10.10.11 get members",
    "gh pr view 12",
    "gh pr create --fill",
    "gh api repos/a/b/pulls",
    "gh api -X GET repos/a/b/rulesets",
    "git push -u origin feat/x",
    "flux get ks -A",
    "tofu plan",
    'grep -r "kubectl apply" docs/',
    "git commit -m 'never run kubectl delete by hand'",
    "kubectl -n ai get deploy -o yaml | grep apply",
    "helm template x oci://y --version 1",
    "just talos render-config talos-1",
    "echo 'a && kubectl delete pod x'",
    "env -i PATH=/usr/bin git status",
    "env -i python3 x.py",
    "rg -u KUBECONFIG .",
    "git log -u KUBECONFIG",
    "echo unset KUBECONFIG",
    "KUBECONFIG=$KUBECONFIG kubectl get pods",
    "KUBECONFIG=${KUBECONFIG} kubectl get pods",
    "KUBECONFIG=$HOME/.kube/home-ops-agent kubectl get pods",
    "KUBECONFIG=$KUBECONFIG:$HOME/.kube/home-ops-agent kubectl get pods",
    "TALOSCONFIG=${TALOSCONFIG} talosctl version",
]

AGENT_ENV = {"KUBECONFIG": "/Users/x/.kube/home-ops-agent", "TALOSCONFIG": "/Users/x/.talos/home-ops-agent"}
STALE_KUBECONFIG = "/Users/x/Projects/home-ops/kubeconfig"
STALE_TALOSCONFIG = "/Users/x/Projects/home-ops/talos/talosconfig"

# Inherited environments env_credential_reason() must refuse (name, environ, variable named).
ENV_REFUSED = [
    ("stale repo kubeconfig", {**AGENT_ENV, "KUBECONFIG": STALE_KUBECONFIG}, "KUBECONFIG"),
    ("stale repo talosconfig", {**AGENT_ENV, "TALOSCONFIG": STALE_TALOSCONFIG}, "TALOSCONFIG"),
    ("list with one non-agent entry",
     {**AGENT_ENV, "KUBECONFIG": f"{AGENT_ENV['KUBECONFIG']}{os.pathsep}/Users/x/.kube/config"}, "KUBECONFIG"),
]
ENV_ACCEPTED = [
    ("agent files", AGENT_ENV),
    ("both unset", {}),
    ("empty string is unset", {"KUBECONFIG": "", "TALOSCONFIG": ""}),
    ("empty list entries skipped", {**AGENT_ENV, "KUBECONFIG": f"{os.pathsep}{AGENT_ENV['KUBECONFIG']}{os.pathsep}"}),
]

NO_KUBECONFIG = {"TALOSCONFIG": AGENT_ENV["TALOSCONFIG"]}
NO_TALOSCONFIG = {"KUBECONFIG": AGENT_ENV["KUBECONFIG"]}
# (environ, command) pairs fallback_credential_reason() must refuse: the client
# would read ~/.kube/config or ~/.talos/config, which hold admin credentials.
FALLBACK_REFUSED = [
    (NO_KUBECONFIG, "kubectl get pods"),
    ({**NO_KUBECONFIG, "KUBECONFIG": ""}, "kubectl get pods"),
    (NO_KUBECONFIG, "kubectl-rook-ceph ceph status"),
    (NO_KUBECONFIG, "flux get ks -A"),
    (NO_KUBECONFIG, "helm list -A"),
    (NO_KUBECONFIG, "k9s"),
    (NO_KUBECONFIG, "stern -n ai x"),
    (NO_KUBECONFIG, "kubecolor get pods"),
    (NO_KUBECONFIG, "cilium status"),
    (NO_KUBECONFIG, "helmfile -f x.yaml list"),
    (NO_KUBECONFIG, "just kube view-secret ai x"),
    (NO_KUBECONFIG, "git status && kubectl get ns"),
    (NO_KUBECONFIG, "bash -c 'kubectl get ns'"),
    (NO_KUBECONFIG, "echo $(kubectl get ns)"),
    (NO_KUBECONFIG, "env FOO=1 /usr/local/bin/kubectl get ns"),
    (NO_KUBECONFIG, "mise exec -- kubectl get pods"),
    (NO_KUBECONFIG, "echo x | xargs kubectl get"),
    (NO_TALOSCONFIG, "talosctl version"),
    ({**NO_TALOSCONFIG, "TALOSCONFIG": ""}, "talosctl -n 10.10.10.11 get members"),
    ({}, "talosctl version"),
]
FALLBACK_ACCEPTED = [
    (NO_KUBECONFIG, "git status"),
    (NO_KUBECONFIG, "gh pr view 12"),
    (NO_KUBECONFIG, "python3 scripts/ci/agent-guard-test.py"),
    (NO_KUBECONFIG, "flate test all --path kubernetes/clusters/main"),
    (NO_KUBECONFIG, "mise exec -- flate test all --path kubernetes/clusters/main"),
    (NO_KUBECONFIG, "grep -r kubectl docs/"),
    (NO_KUBECONFIG, "task flux:test:all"),
    (NO_KUBECONFIG, "talosctl -n 10.10.10.11 get members"),
    (NO_TALOSCONFIG, "kubectl get pods -A"),
    (NO_TALOSCONFIG, "just talos render-config talos-1"),
    (AGENT_ENV, "kubectl get pods -A"),
    (AGENT_ENV, "talosctl version"),
]

failures: list[str] = []


def load_guard():
    spec = importlib.util.spec_from_file_location("agent_guard", GUARD)
    if spec is None or spec.loader is None:
        raise SystemExit(f"FAIL: cannot load {GUARD}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


def test_blocks_writes(guard) -> None:
    for cmd in BLOCKED:
        check(guard.is_blocked(cmd) is not None, f"blocks: {cmd}")


def test_allows_reads(guard) -> None:
    for cmd in ALLOWED:
        reason = guard.is_blocked(cmd)
        check(reason is None, f"allows: {cmd}" + (f" (got {reason!r})" if reason else ""))


def test_inherited_env(guard) -> None:
    for name, environ, var in ENV_REFUSED:
        reason = guard.env_credential_reason(environ)
        check(reason is not None and var in reason and "home-ops-agent" in reason,
              f"env refused: {name} (got {reason!r})")
    for name, environ in ENV_ACCEPTED:
        reason = guard.env_credential_reason(environ)
        check(reason is None, f"env accepted: {name}" + (f" (got {reason!r})" if reason else ""))
    for environ, cmd in FALLBACK_REFUSED:
        reason = guard.fallback_credential_reason(cmd, environ)
        check(reason is not None, f"fallback refused with {sorted(environ)} set: {cmd}")
    for environ, cmd in FALLBACK_ACCEPTED:
        reason = guard.fallback_credential_reason(cmd, environ)
        check(reason is None, f"fallback allowed with {sorted(environ)} set: {cmd}"
              + (f" (got {reason!r})" if reason else ""))


def test_hook_entrypoint() -> None:
    def run(cmd: str, environ: dict[str, str] = AGENT_ENV) -> subprocess.CompletedProcess:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
        return subprocess.run(
            [sys.executable, str(GUARD)], input=payload, capture_output=True, text=True,
            env={"PATH": os.defpath, **environ},
        )

    blocked = run("kubectl -n ai delete pod x")
    check(blocked.returncode == 2 and blocked.stderr.strip() != "",
          f"hook exits 2 with a reason for a write (rc={blocked.returncode})")
    allowed = run("kubectl get pods -A")
    check(allowed.returncode == 0, f"hook exits 0 for a read (rc={allowed.returncode})")
    other = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}}),
        capture_output=True, text=True, env={"PATH": os.defpath, **AGENT_ENV},
    )
    check(other.returncode == 0, "hook ignores non-Bash tools")
    stale = run("git status", {**AGENT_ENV, "KUBECONFIG": STALE_KUBECONFIG})
    check(stale.returncode == 2 and "KUBECONFIG" in stale.stderr,
          f"hook exits 2 for an inherited non-agent KUBECONFIG (rc={stale.returncode})")
    admin = run("git status", {**AGENT_ENV, "HOME_OPS_KUBECONFIG": "/Users/x/.kube/home-ops-admin"})
    check(admin.returncode == 2, f"hook exits 2 when HOME_OPS_KUBECONFIG is set (rc={admin.returncode})")
    unset = run("kubectl get pods -A", NO_KUBECONFIG)
    check(unset.returncode == 2 and "KUBECONFIG" in unset.stderr,
          f"hook exits 2 for kubectl with KUBECONFIG unset (rc={unset.returncode})")
    unset_git = run("git status", {})
    check(unset_git.returncode == 0, f"hook exits 0 for git status with nothing set (rc={unset_git.returncode})")
    # A mise shim rewrites KUBECONFIG to the agent path; the GUARD_* capture wins.
    shimmed = run("git status", {**AGENT_ENV, "GUARD_KUBECONFIG": STALE_KUBECONFIG})
    check(shimmed.returncode == 2, f"hook prefers GUARD_KUBECONFIG over KUBECONFIG (rc={shimmed.returncode})")
    shim_unset = run("kubectl get pods", {**AGENT_ENV, "GUARD_KUBECONFIG": "__unset__"})
    check(shim_unset.returncode == 2, f"hook treats GUARD_KUBECONFIG=__unset__ as unset (rc={shim_unset.returncode})")
    shim_unset_git = run("git status", {**AGENT_ENV, "GUARD_KUBECONFIG": "__unset__"})
    check(shim_unset_git.returncode == 0, f"__unset__ still allows git status (rc={shim_unset_git.returncode})")


def test_settings_command() -> None:
    """Drive the hook through the exact settings.json command string, as Claude Code does."""
    settings = json.loads((ROOT / ".claude" / "settings.json").read_text())
    commands = [h["command"] for entry in settings["hooks"]["PreToolUse"] if entry.get("matcher") == "Bash"
                for h in entry["hooks"] if "guard.py" in h.get("command", "")]
    check(len(commands) == 1, f"settings.json wires guard.py once for Bash (got {len(commands)})")
    if not commands:
        return
    path = os.pathsep.join([str(Path(sys.executable).parent), os.defpath])

    def run(cmd: str, environ: dict[str, str]) -> int:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
        env = {"PATH": path, "CLAUDE_PROJECT_DIR": str(ROOT), **environ}
        return subprocess.run(["sh", "-c", commands[0]], input=payload, capture_output=True,
                              text=True, env=env).returncode

    check(run("git status", {**AGENT_ENV, "KUBECONFIG": STALE_KUBECONFIG}) == 2,
          "settings command exits 2 for an inherited stale KUBECONFIG")
    check(run("kubectl get pods", NO_KUBECONFIG) == 2, "settings command exits 2 for kubectl with KUBECONFIG unset")
    check(run("kubectl get pods", AGENT_ENV) == 0, "settings command exits 0 for kubectl with the agent env")
    check(run("git status", {}) == 0, "settings command exits 0 for git status with nothing set")
    check(run("git status", {**AGENT_ENV, "GUARD_KUBECONFIG": STALE_KUBECONFIG}) == 0,
          "settings command captures KUBECONFIG itself, ignoring an inherited GUARD_KUBECONFIG")


def main() -> int:
    if not GUARD.is_file():
        print(f"FAIL: {GUARD} not found")
        return 1
    guard = load_guard()
    test_blocks_writes(guard)
    test_allows_reads(guard)
    test_inherited_env(guard)
    test_hook_entrypoint()
    test_settings_command()
    print(f"Summary: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
