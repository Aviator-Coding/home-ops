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
     command, and 0 for an allowed one, given Claude Code's hook JSON.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_hook_entrypoint() -> None:
    def run(cmd: str) -> subprocess.CompletedProcess:
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}})
        return subprocess.run(
            [sys.executable, str(GUARD)], input=payload, capture_output=True, text=True
        )

    blocked = run("kubectl -n ai delete pod x")
    check(blocked.returncode == 2 and blocked.stderr.strip() != "",
          f"hook exits 2 with a reason for a write (rc={blocked.returncode})")
    allowed = run("kubectl get pods -A")
    check(allowed.returncode == 0, f"hook exits 0 for a read (rc={allowed.returncode})")
    other = subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps({"tool_name": "Read", "tool_input": {"file_path": "x"}}),
        capture_output=True, text=True,
    )
    check(other.returncode == 0, "hook ignores non-Bash tools")


def main() -> int:
    if not GUARD.is_file():
        print(f"FAIL: {GUARD} not found")
        return 1
    guard = load_guard()
    test_blocks_writes(guard)
    test_allows_reads(guard)
    test_hook_entrypoint()
    print(f"Summary: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
