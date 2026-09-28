#!/usr/bin/env python3
"""Behavioral regression test for system/pvc-writable-check.

Pins the empty-volume audit check (2026-08-30): a CronJob that discovers every
PVC-mounting container from live pod specs and execs `test -w <mountPath>` so
an app that cannot write to its own claim is caught even when Ready/probes/
Gatus/HelmRelease are all green.

This test does not grep source text as a proxy for behavior. It:

  1. Extracts the real CronJob bash body from the shipped manifest and runs it
     under a mock `kubectl` that serves fixture pod JSON and synthetic exec
     outcomes. Asserts observable WRITABLE / NOT WRITABLE / SKIP lines and exit
     codes for the motivating bugs and awkward cases.
  2. Parses the kustomize-built object model (or source YAML if kustomize is
     unavailable) and asserts the RBAC split-role contract, PrometheusRule
     signal, CronJob safety knobs, and headlamp's readOnly volumeMount so the
     check stays non-noisy on a correctly-designed init-writes/main-reads app.

Live cluster proof (apply + create job --from=cronjob + delete) is documented
in the app README and is out of scope for this offline gate.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/system/pvc-writable-check/app"
CRONJOB_PATH = APP / "cronjob.yaml"
RBAC_PATH = APP / "rbac.yaml"
PROMRULE_PATH = APP / "prometheusrule.yaml"
OVERLAY_PATH = ROOT / "kubernetes/apps/main/system/pvc-writable-check.yaml"
HEADLAMP_HR = ROOT / "kubernetes/apps/base/flux-system/headlamp/app/helmrelease.yaml"
SYSTEM_MAIN = ROOT / "kubernetes/apps/main/system/kustomization.yaml"

EXCLUDED_NAMESPACES = frozenset({"rook-ceph", "database", "security"})
SKIP_ANNOTATION = "pvc-writable-check.home-operations.com/skip"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_yaml_docs(path: Path) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] = []
    with path.open() as f:
        for doc in yaml.safe_load_all(f):
            if isinstance(doc, dict):
                docs.append(doc)
    return docs


def load_app_objects() -> list[dict[str, Any]]:
    """Prefer the kustomize-built object set Flux would apply."""
    try:
        built = subprocess.run(
            ["kubectl", "kustomize", str(APP)],
            check=True,
            capture_output=True,
            text=True,
        )
        docs: list[dict[str, Any]] = []
        for doc in yaml.safe_load_all(built.stdout):
            if isinstance(doc, dict):
                docs.append(doc)
        if docs:
            return docs
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    docs = []
    for name in ("rbac.yaml", "cronjob.yaml", "prometheusrule.yaml"):
        docs.extend(load_yaml_docs(APP / name))
    return docs


def extract_cronjob_script(cronjob: dict[str, Any]) -> str:
    containers = (
        cronjob.get("spec", {})
        .get("jobTemplate", {})
        .get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    require(len(containers) == 1, "expected exactly one check container")
    command = containers[0].get("command") or []
    require(
        len(command) >= 3 and command[0].endswith("bash") and command[1] == "-c",
        f"unexpected CronJob command shape: {command[:3]!r}",
    )
    script = command[2]
    require("test -w" in script, "script must invoke test -w")
    require("classify_exec_failure" in script, "script must classify kubectl errors")
    return script


def pod(
    ns: str,
    name: str,
    *,
    container: str = "app",
    claim: str = "data",
    mount: str = "/config",
    volume: str = "config",
    running: bool = True,
    read_only: bool = False,
    annotations: dict[str, str] | None = None,
    extra_containers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Minimal Running-pod fixture with one PVC volumeMount."""
    volume_mount: dict[str, Any] = {"name": volume, "mountPath": mount}
    if read_only:
        volume_mount["readOnly"] = True
    containers = [
        {
            "name": container,
            "image": "example/app:1",
            "volumeMounts": [volume_mount],
        }
    ]
    statuses = [
        {
            "name": container,
            "state": {"running": {"startedAt": "2026-08-30T00:00:00Z"}}
            if running
            else {"waiting": {"reason": "CrashLoopBackOff"}},
            "ready": running,
        }
    ]
    for extra in extra_containers or []:
        containers.append(extra["spec"])
        statuses.append(extra["status"])
    meta: dict[str, Any] = {"namespace": ns, "name": name}
    if annotations:
        meta["annotations"] = annotations
    return {
        "metadata": meta,
        "spec": {
            "containers": containers,
            "volumes": [
                {
                    "name": volume,
                    "persistentVolumeClaim": {"claimName": claim},
                }
            ],
        },
        "status": {"containerStatuses": statuses, "phase": "Running" if running else "Pending"},
    }


def write_mock_kubectl(
    bin_dir: Path,
    pods: list[dict[str, Any]],
    exec_plan: dict[tuple[str, str, str], dict[str, Any]],
) -> Path:
    """Install a kubectl shim that serves fixture pods and scripted exec results.

    exec_plan keys are (namespace, pod, container). Values:
      {"mode": "writable"|"unwritable"|"no-test"|"no-test-no-sh"|"forbidden"|"api-error"|"timeout"}
    """
    plan_path = bin_dir / "exec-plan.json"
    pods_path = bin_dir / "pods.json"
    pods_path.write_text(json.dumps({"apiVersion": "v1", "kind": "List", "items": pods}))
    # JSON object keys can't be tuples; encode as "ns|pod|container".
    plan_path.write_text(
        json.dumps({f"{ns}|{p}|{c}": v for (ns, p, c), v in exec_plan.items()})
    )
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -uo pipefail
            PODS_JSON={pods_path!s}
            PLAN_JSON={plan_path!s}
            LOG={bin_dir / "kubectl.log"!s}

            log() {{ printf '%s\\n' "$*" >>"$LOG"; }}

            # `kubectl get pods -A -o json`
            if [[ "${{1:-}}" == "get" && "${{2:-}}" == "pods" ]]; then
              log "GET $*"
              cat "$PODS_JSON"
              exit 0
            fi

            # `kubectl exec -n NS POD -c CONTAINER -- CMD...`
            if [[ "${{1:-}}" == "exec" ]]; then
              log "EXEC $*"
              ns=""; pod=""; container="";
              shift
              while [[ $# -gt 0 ]]; do
                case "$1" in
                  -n|--namespace) ns="$2"; shift 2 ;;
                  -c) container="$2"; shift 2 ;;
                  --) shift; break ;;
                  -*) shift ;;
                  *)
                    if [[ -z "$pod" ]]; then pod="$1"; shift
                    else break
                    fi
                    ;;
                esac
              done
              # remaining argv is the remote command
              key="${{ns}}|${{pod}}|${{container}}"
              mode=$(python3 -c 'import json,sys; p=json.load(open(sys.argv[1])); print(p.get(sys.argv[2],{{}}).get("mode","writable"))' "$PLAN_JSON" "$key")
              cmd0="${{1:-}}"
              case "$mode" in
                writable)
                  if [[ "$cmd0" == "test" ]]; then exit 0; fi
                  if [[ "$cmd0" == "sh" ]]; then exit 0; fi
                  exit 0
                  ;;
                unwritable)
                  if [[ "$cmd0" == "test" ]]; then exit 1; fi
                  if [[ "$cmd0" == "sh" ]]; then exit 1; fi
                  exit 1
                  ;;
                no-test)
                  if [[ "$cmd0" == "test" ]]; then
                    echo 'error: Internal error occurred: error executing command in container: failed to exec in container: failed to start exec: exec failed: unable to start container process: exec: "test": executable file not found in $PATH' >&2
                    exit 126
                  fi
                  # sh path present and writable
                  exit 0
                  ;;
                no-test-no-sh)
                  if [[ "$cmd0" == "test" ]]; then
                    echo 'error: Internal error occurred: error executing command in container: failed to exec in container: failed to start exec: exec failed: unable to start container process: exec: "test": executable file not found in $PATH' >&2
                    exit 126
                  fi
                  if [[ "$cmd0" == "sh" ]]; then
                    echo 'error: Internal error occurred: error executing command in container: failed to exec in container: failed to start exec: exec failed: unable to start container process: exec: "sh": executable file not found in $PATH' >&2
                    exit 126
                  fi
                  exit 126
                  ;;
                forbidden)
                  echo "Error from server (Forbidden): pods \\"${{pod}}\\" is forbidden: User \\"system:serviceaccount:system:pvc-writable-check\\" cannot create resource \\"pods/exec\\" in API group \\"\\" in the namespace \\"${{ns}}\\"" >&2
                  exit 1
                  ;;
                api-error)
                  echo "Error from server (NotFound): pods \\"${{pod}}\\" not found" >&2
                  exit 1
                  ;;
                timeout)
                  # Simulate timeout(1) killing kubectl: the real script wraps
                  # kubectl in `timeout 15`, so RC 124 is what classify sees.
                  # The shim itself exits 124 when the plan says timeout.
                  exit 124
                  ;;
                *)
                  echo "mock kubectl: unknown mode $mode for $key" >&2
                  exit 99
                  ;;
              esac
            fi

            echo "mock kubectl: unhandled argv: $*" >&2
            exit 99
            """
        )
    )
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # timeout passthrough: honour real timeout if present, else just exec.
    timeout_bin = bin_dir / "timeout"
    if shutil.which("timeout"):
        real = shutil.which("timeout")
        timeout_bin.symlink_to(real)
    else:
        timeout_bin.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                # Portable stand-in: drop duration args and run the command.
                shift
                while [[ "${1:-}" == -* ]]; do shift; done
                exec "$@"
                """
            )
        )
        timeout_bin.chmod(timeout_bin.stat().st_mode | stat.S_IXUSR)
    return kubectl


def run_script(
    script: str,
    pods: list[dict[str, Any]],
    exec_plan: dict[tuple[str, str, str], dict[str, Any]],
    *,
    excluded: str = "rook-ceph database security",
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="pvc-writable-check-") as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        write_mock_kubectl(bin_dir, pods, exec_plan)
        script_path = tmp_path / "check.sh"
        # Mirror the CronJob env the live job injects.
        full = (
            "#!/usr/bin/env bash\n"
            f"export SKIP_ANNOTATION={SKIP_ANNOTATION!r}\n"
            f"export EXCLUDED_NAMESPACES={excluded!r}\n"
            + script
        )
        script_path.write_text(full)
        script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR)
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        # Force a writable TMPDIR so any accidental temp use is observable and
        # contained; the real job has readOnlyRootFilesystem.
        env["TMPDIR"] = str(tmp_path / "tmp")
        (tmp_path / "tmp").mkdir()
        return subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )


# ---------------------------------------------------------------------------
# Behavioral cases against the real script
# ---------------------------------------------------------------------------


def test_flags_unwritable_motivating_bugs(script: str) -> None:
    """rsshub-playwright-shaped and autobrr-shaped unwritable mounts exit 1."""
    pods = [
        pod("selfhosted", "rsshub-playwright-0", claim="rsshub-playwright", mount="/cache"),
        pod("downloads", "autobrr-0", claim="autobrr", mount="/config"),
        pod("media", "healthy-app-0", claim="healthy", mount="/data"),
    ]
    plan = {
        ("selfhosted", "rsshub-playwright-0", "app"): {"mode": "unwritable"},
        ("downloads", "autobrr-0", "app"): {"mode": "unwritable"},
        ("media", "healthy-app-0", "app"): {"mode": "writable"},
    }
    result = run_script(script, pods, plan)
    out = result.stdout + result.stderr
    require(result.returncode == 1, f"expected exit 1 on unwritable mounts, got {result.returncode}\n{out}")
    require(
        "NOT WRITABLE" in out and "rsshub-playwright" in out,
        f"must flag rsshub-playwright:\n{out}",
    )
    require(
        "NOT WRITABLE" in out and "autobrr" in out,
        f"must flag autobrr:\n{out}",
    )
    require(
        re.search(r"WRITABLE\s+media/healthy-app-0", out) is not None,
        f"healthy mount must still report WRITABLE:\n{out}",
    )
    require(
        "FAILURES" in out
        and "selfhosted/rsshub-playwright" in out
        and "downloads/autobrr" in out,
        f"FAILURES summary must name both claims:\n{out}",
    )


def test_clean_when_all_writable(script: str) -> None:
    """After the motivating fixes, a clean sweep exits 0 with zero NOT WRITABLE."""
    pods = [
        pod("selfhosted", "rsshub-playwright-0", claim="rsshub-playwright", mount="/cache"),
        pod("downloads", "autobrr-0", claim="autobrr", mount="/config"),
    ]
    plan = {
        ("selfhosted", "rsshub-playwright-0", "app"): {"mode": "writable"},
        ("downloads", "autobrr-0", "app"): {"mode": "writable"},
    }
    result = run_script(script, pods, plan)
    out = result.stdout + result.stderr
    require(result.returncode == 0, f"expected clean exit 0, got {result.returncode}\n{out}")
    require("NOT WRITABLE" not in out, f"no NOT WRITABLE on clean sweep:\n{out}")
    require("FAILURES" not in out, f"no FAILURES block on clean sweep:\n{out}")
    writable_lines = [
        ln for ln in out.splitlines()
        if ln.startswith("WRITABLE") and not ln.startswith("NOT WRITABLE")
    ]
    require(len(writable_lines) >= 2, f"both mounts WRITABLE:\n{out}")
    require("not_writable=0" in out, f"summary must show not_writable=0:\n{out}")


def test_skips_readonly_mount_and_skip_annotation(script: str) -> None:
    """readOnly:true volumeMounts and the skip annotation produce zero execs."""
    pods = [
        pod(
            "flux-system",
            "headlamp-0",
            claim="plugins",
            mount="/build/plugins",
            read_only=True,
        ),
        pod(
            "media",
            "special-0",
            claim="special",
            mount="/data",
            annotations={SKIP_ANNOTATION: "true"},
        ),
        pod("media", "normal-0", claim="normal", mount="/data"),
    ]
    plan = {
        ("media", "normal-0", "app"): {"mode": "writable"},
        # If the script incorrectly execs headlamp/special, mock returns unwritable
        # so the test fails loudly instead of silently skipping the bug.
        ("flux-system", "headlamp-0", "app"): {"mode": "unwritable"},
        ("media", "special-0", "app"): {"mode": "unwritable"},
    }
    result = run_script(script, pods, plan)
    out = result.stdout + result.stderr
    require(result.returncode == 0, f"read-only/skip must not fail the job:\n{out}")
    require("headlamp" not in out or "NOT WRITABLE" not in out, f"headlamp must not alert:\n{out}")
    require("special-0" not in out, f"skip-annotated pod must not appear:\n{out}")
    require(re.search(r"WRITABLE\s+media/normal-0", out) is not None, f"normal still checked:\n{out}")
    require("candidates=1" in out, f"only the non-readonly non-skip row is a candidate:\n{out}")


def test_skips_excluded_namespaces_without_exec(script: str) -> None:
    """rook-ceph/database/security are SKIP (namespace excluded) and never fail."""
    pods = [
        pod("database", "postgres-17-1", claim="pg-data", mount="/var/lib/postgresql/data"),
        pod("rook-ceph", "rook-ceph-mon-a", claim="mon-a", mount="/var/lib/ceph"),
        pod("security", "vault-0", claim="vault", mount="/data"),
        pod("downloads", "ok-0", claim="ok", mount="/config"),
    ]
    plan = {
        ("downloads", "ok-0", "app"): {"mode": "writable"},
        # Poison: if exec happens in excluded NS, treat as unwritable so we notice.
        ("database", "postgres-17-1", "app"): {"mode": "unwritable"},
        ("rook-ceph", "rook-ceph-mon-a", "app"): {"mode": "unwritable"},
        ("security", "vault-0", "app"): {"mode": "unwritable"},
    }
    result = run_script(script, pods, plan)
    out = result.stdout + result.stderr
    require(result.returncode == 0, f"excluded namespaces must not fail the job:\n{out}")
    require(out.count("namespace excluded by design") == 3, f"three design exclusions:\n{out}")
    require("NOT WRITABLE" not in out, f"excluded must not become NOT WRITABLE:\n{out}")
    require("skipped_namespace_excluded=3" in out, f"counter for exclusions:\n{out}")


def test_skips_not_running_and_shell_less(script: str) -> None:
    """CrashLoop / missing test+sh are skips, never failures."""
    pods = [
        pod("downloads", "crash-0", claim="crash", mount="/config", running=False),
        pod("monitoring", "loki-0", claim="loki", mount="/loki"),
        pod("media", "ok-0", claim="ok", mount="/data"),
    ]
    plan = {
        ("monitoring", "loki-0", "app"): {"mode": "no-test-no-sh"},
        ("media", "ok-0", "app"): {"mode": "writable"},
        ("downloads", "crash-0", "app"): {"mode": "unwritable"},
    }
    result = run_script(script, pods, plan)
    out = result.stdout + result.stderr
    require(result.returncode == 0, f"awkward skips must exit 0:\n{out}")
    require("container not running" in out, f"not-running skip:\n{out}")
    require("no shell or test binary" in out, f"shell-less skip:\n{out}")
    require("NOT WRITABLE" not in out, f"skips must not be failures:\n{out}")


def test_sh_fallback_writable(script: str) -> None:
    """Missing standalone `test` but present `sh` still reports WRITABLE via sh."""
    pods = [pod("flux-system", "konflate-0", claim="konflate", mount="/data")]
    plan = {("flux-system", "konflate-0", "app"): {"mode": "no-test"}}
    result = run_script(script, pods, plan)
    out = result.stdout + result.stderr
    require(result.returncode == 0, f"sh fallback writable must exit 0:\n{out}")
    require("WRITABLE (via sh)" in out, f"must use sh fallback:\n{out}")


def test_kubectl_errors_are_skips_not_failures(script: str) -> None:
    """Forbidden / API errors / timeouts classify as SKIP, never NOT WRITABLE."""
    pods = [
        pod("ai", "forbidden-0", claim="f", mount="/data"),
        pod("ai", "gone-0", claim="g", mount="/data"),
        pod("ai", "slow-0", claim="s", mount="/data"),
        pod("ai", "ok-0", claim="o", mount="/data"),
    ]
    plan = {
        ("ai", "forbidden-0", "app"): {"mode": "forbidden"},
        ("ai", "gone-0", "app"): {"mode": "api-error"},
        ("ai", "slow-0", "app"): {"mode": "timeout"},
        ("ai", "ok-0", "app"): {"mode": "writable"},
    }
    result = run_script(script, pods, plan)
    out = result.stdout + result.stderr
    require(result.returncode == 0, f"kubectl-level errors must not fail the job:\n{out}")
    require("pods/exec forbidden, RBAC" in out, f"RBAC skip path:\n{out}")
    require(out.count("exec unavailable, transient") >= 2, f"transient skips for api+timeout:\n{out}")
    require("NOT WRITABLE" not in out, f"no false-positive NOT WRITABLE:\n{out}")
    require("skipped_rbac=1" in out, f"rbac counter:\n{out}")
    require("skipped_transient=2" in out, f"transient counter:\n{out}")


def test_empty_pod_list_is_fatal(script: str) -> None:
    """A broken discovery (empty kubectl output) must fail closed, not silently pass."""
    # run_script always serves a List; craft a kubectl that returns empty instead.
    with tempfile.TemporaryDirectory(prefix="pvc-writable-empty-") as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        kubectl = bin_dir / "kubectl"
        kubectl.write_text("#!/usr/bin/env bash\nexit 0\n")
        kubectl.chmod(kubectl.stat().st_mode | stat.S_IXUSR)
        timeout_bin = bin_dir / "timeout"
        timeout_bin.write_text("#!/usr/bin/env bash\nshift; exec \"$@\"\n")
        timeout_bin.chmod(timeout_bin.stat().st_mode | stat.S_IXUSR)
        script_path = tmp_path / "check.sh"
        script_path.write_text(
            "#!/usr/bin/env bash\n"
            f"export SKIP_ANNOTATION={SKIP_ANNOTATION!r}\n"
            "export EXCLUDED_NAMESPACES='rook-ceph database security'\n"
            + script
        )
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["TMPDIR"] = str(tmp_path / "tmp")
        (tmp_path / "tmp").mkdir()
        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
    out = result.stdout + result.stderr
    require(result.returncode != 0, f"empty discovery must be fatal:\n{out}")
    require("FATAL" in out, f"must print FATAL on empty discovery:\n{out}")


def test_no_write_side_effect_in_remote_command(script: str) -> None:
    """Remote argv is only `test -w` / `sh -c test -w` - never a mutating command."""
    # Capture every remote argv the script issues via the mock log.
    pods = [
        pod("media", "a-0", claim="a", mount="/data"),
        pod("media", "b-0", claim="b", mount="/data"),
    ]
    plan = {
        ("media", "a-0", "app"): {"mode": "writable"},
        ("media", "b-0", "app"): {"mode": "no-test"},
    }
    with tempfile.TemporaryDirectory(prefix="pvc-writable-argv-") as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        write_mock_kubectl(bin_dir, pods, plan)
        script_path = tmp_path / "check.sh"
        script_path.write_text(
            "#!/usr/bin/env bash\n"
            f"export SKIP_ANNOTATION={SKIP_ANNOTATION!r}\n"
            "export EXCLUDED_NAMESPACES='rook-ceph database security'\n"
            + script
        )
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["TMPDIR"] = str(tmp_path / "tmp")
        (tmp_path / "tmp").mkdir()
        result = subprocess.run(
            ["bash", str(script_path)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        log = (bin_dir / "kubectl.log").read_text()
    require(result.returncode == 0, f"argv capture run must succeed:\n{result.stdout}\n{result.stderr}")
    exec_lines = [ln for ln in log.splitlines() if ln.startswith("EXEC ")]
    require(len(exec_lines) >= 2, f"expected exec attempts logged:\n{log}")
    for ln in exec_lines:
        # After `--` only test/sh forms are legal.
        require(" -- " in ln, f"exec must use -- argv form: {ln}")
        remote = ln.split(" -- ", 1)[1]
        require(
            remote.startswith("test -w ") or remote.startswith("sh -c "),
            f"remote command must be test -w or sh -c, got: {remote}",
        )
        for banned in ("touch ", "rm ", "chmod ", "chown ", "dd ", "echo ", ">"):
            require(banned not in remote, f"mutating token {banned!r} in remote argv: {remote}")


# ---------------------------------------------------------------------------
# Structured object-model contracts (RBAC / alert / headlamp / wiring)
# ---------------------------------------------------------------------------


def test_rbac_split_role_and_exclusions(objects: list[dict[str, Any]]) -> None:
    """pods/exec is RoleBinding-scoped; excluded NS have no binding; no secrets verbs."""
    cluster_roles = [o for o in objects if o.get("kind") == "ClusterRole"]
    crb = [o for o in objects if o.get("kind") == "ClusterRoleBinding"]
    rbs = [o for o in objects if o.get("kind") == "RoleBinding"]
    sa = [o for o in objects if o.get("kind") == "ServiceAccount"]

    require(len(sa) == 1 and sa[0]["metadata"]["name"] == "pvc-writable-check", "one SA")
    require(sa[0]["metadata"].get("namespace") == "system", "SA lives in system")

    by_name = {o["metadata"]["name"]: o for o in cluster_roles}
    require("pvc-writable-check-read" in by_name, "read ClusterRole present")
    require("pvc-writable-check-exec" in by_name, "exec ClusterRole present")

    read_rules = by_name["pvc-writable-check-read"]["rules"]
    exec_rules = by_name["pvc-writable-check-exec"]["rules"]
    require(
        read_rules == [{"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]}],
        f"read role is pods get/list only: {read_rules}",
    )
    require(
        exec_rules
        == [{"apiGroups": [""], "resources": ["pods/exec"], "verbs": ["create"]}],
        f"exec role is pods/exec create only: {exec_rules}",
    )

    # CRITICAL: exec ClusterRole must NEVER be cluster-bound.
    for binding in crb:
        ref = binding.get("roleRef", {})
        require(
            ref.get("name") != "pvc-writable-check-exec",
            f"exec ClusterRole must not have a ClusterRoleBinding (found {binding['metadata']['name']})",
        )
        require(
            ref.get("name") == "pvc-writable-check-read",
            f"only the read role may be cluster-bound, got {ref}",
        )

    bound_ns = {rb["metadata"]["namespace"] for rb in rbs}
    require(
        EXCLUDED_NAMESPACES.isdisjoint(bound_ns),
        f"excluded namespaces must have no RoleBinding, found {bound_ns & EXCLUDED_NAMESPACES}",
    )
    require(len(rbs) >= 10, f"expected broad per-namespace RoleBinding coverage, got {len(rbs)}")
    for rb in rbs:
        require(rb["metadata"]["name"] == "pvc-writable-check-exec", "RoleBinding name")
        require(rb["roleRef"]["name"] == "pvc-writable-check-exec", "roleRef")
        subs = rb.get("subjects") or []
        require(
            any(
                s.get("kind") == "ServiceAccount"
                and s.get("name") == "pvc-writable-check"
                and s.get("namespace") == "system"
                for s in subs
            ),
            f"RoleBinding {rb['metadata']['namespace']} must subject the system SA",
        )


def test_prometheusrule_job_failure_signal(objects: list[dict[str, Any]]) -> None:
    """Alert fires only on terminal failed Jobs for this CronJob, with reason!="" """
    rules = [o for o in objects if o.get("kind") == "PrometheusRule"]
    require(len(rules) == 1, "one PrometheusRule")
    groups = rules[0]["spec"]["groups"]
    alerts = [r for g in groups for r in g.get("rules", []) if "alert" in r]
    require(len(alerts) == 1 and alerts[0]["alert"] == "PVCVolumeNotWritable", "alert name")
    expr = alerts[0]["expr"]
    require("kube_job_status_failed" in expr, f"uses ksm job metric: {expr}")
    require('namespace="system"' in expr, f"scoped to system: {expr}")
    require("pvc-writable-check-" in expr, f"job_name match: {expr}")
    require('reason!=""' in expr or 'reason!=""' in expr.replace(" ", ""), f"reason non-empty guard: {expr}")
    require(alerts[0].get("for") == "10m", "for: 10m window")
    require(alerts[0].get("labels", {}).get("severity") == "warning", "severity warning")
    ann = alerts[0].get("annotations", {}).get("summary", "")
    require("kubectl" in ann and "FAILURES" in ann, f"actionable summary points at logs: {ann}")


def test_cronjob_safety_and_discovery_contract(objects: list[dict[str, Any]]) -> None:
    """CronJob is non-root/RO-rootfs, excludes the three NS, and has no targetNamespace on overlay."""
    cron = [o for o in objects if o.get("kind") == "CronJob"]
    require(len(cron) == 1, "one CronJob")
    cj = cron[0]
    require(cj["metadata"].get("namespace") == "system", "CronJob namespace system")
    require(cj["spec"]["concurrencyPolicy"] == "Forbid", "Forbid concurrency")
    pod_spec = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    require(pod_spec.get("serviceAccountName") == "pvc-writable-check", "uses dedicated SA")
    sc = pod_spec.get("securityContext") or {}
    require(sc.get("runAsNonRoot") is True, "runAsNonRoot")
    require(sc.get("runAsUser") == 65534, "non-root uid")
    csc = pod_spec["containers"][0].get("securityContext") or {}
    require(csc.get("readOnlyRootFilesystem") is True, "RO rootfs")
    require(csc.get("allowPrivilegeEscalation") is False, "no priv esc")
    require(csc.get("capabilities", {}).get("drop") == ["ALL"], "drop ALL caps")

    env = {e["name"]: e["value"] for e in pod_spec["containers"][0].get("env") or []}
    require(env.get("SKIP_ANNOTATION") == SKIP_ANNOTATION, "skip annotation env")
    excluded = set((env.get("EXCLUDED_NAMESPACES") or "").split())
    require(excluded == EXCLUDED_NAMESPACES, f"EXCLUDED_NAMESPACES match RBAC: {excluded}")

    # Overlay must not set targetNamespace (would collapse RoleBindings).
    overlay_docs = load_yaml_docs(OVERLAY_PATH)
    require(len(overlay_docs) == 1, "one overlay Kustomization")
    require(
        "targetNamespace" not in overlay_docs[0].get("spec", {}),
        "overlay must omit targetNamespace",
    )
    main = yaml.safe_load(SYSTEM_MAIN.read_text())
    resources = main.get("resources") or []
    require(
        any(str(r).endswith("pvc-writable-check.yaml") or str(r) == "./pvc-writable-check.yaml" for r in resources),
        f"system main kustomization must include the overlay: {resources}",
    )


def test_headlamp_main_mount_is_readonly() -> None:
    """headlamp main container declares plugins mount readOnly so the check stays quiet.

    Parsed as structured Helm values, not a source grep: the values tree must
    carry volumeMounts with readOnly true on the plugins path for the main
    container (init may still write).
    """
    docs = load_yaml_docs(HEADLAMP_HR)
    require(len(docs) >= 1, "headlamp HelmRelease present")
    hr = docs[0]
    values = hr.get("spec", {}).get("values") or {}
    # app-template chart: controllers.*.containers.* or persistence/volumeMounts
    # Walk the values tree for any volumeMount dict naming plugins with readOnly.
    found_readonly_plugins = False
    found_plugins_mount = False

    def walk(node: Any) -> None:
        nonlocal found_readonly_plugins, found_plugins_mount
        if isinstance(node, dict):
            mp = node.get("mountPath") or node.get("path")
            if isinstance(mp, str) and "plugins" in mp and ("volumeMounts" in str(type(node)) or "mountPath" in node):
                if "mountPath" in node:
                    found_plugins_mount = True
                    if node.get("readOnly") is True:
                        found_readonly_plugins = True
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(values)
    # Also accept explicit advanced.mounts / persistence globalMounts shapes.
    if not found_readonly_plugins:
        raw = yaml.dump(values)
        # Last-resort structured re-parse: find list items that are mount maps.
        def walk_mounts(node: Any) -> None:
            nonlocal found_readonly_plugins, found_plugins_mount
            if isinstance(node, dict):
                if "mountPath" in node and isinstance(node.get("mountPath"), str):
                    if "plugins" in node["mountPath"]:
                        found_plugins_mount = True
                        if node.get("readOnly") is True:
                            found_readonly_plugins = True
                for v in node.values():
                    walk_mounts(v)
            elif isinstance(node, list):
                for i in node:
                    walk_mounts(i)

        walk_mounts(values)
        _ = raw  # silence unused if dump kept for debug
    require(found_plugins_mount, "headlamp values must mount a plugins path")
    require(
        found_readonly_plugins,
        "headlamp main plugins volumeMount must set readOnly: true "
        "(expected read-only declaration for the check)",
    )


def test_script_avoids_herestring_for_pod_json(script: str) -> None:
    """Regression: multi-MB pod JSON must not use <<< (breaks under RO rootfs)."""
    # Behavioral proxy: under TMPDIR unwritable, the discovery pipe path must still
    # parse a multi-MB payload. The real bug was bash writing a here-doc temp file.
    big_pods = [
        pod("media", f"app-{i}", claim=f"c-{i}", mount="/data")
        for i in range(80)
    ]
    # Pad annotations so the JSON is multi-MB-ish / large enough to stress here-strings.
    for p in big_pods:
        p["metadata"]["annotations"] = {
            f"pad-{j}": ("x" * 200) for j in range(40)
        }
    plan = {
        (p["metadata"]["namespace"], p["metadata"]["name"], "app"): {"mode": "writable"}
        for p in big_pods
    }
    with tempfile.TemporaryDirectory(prefix="pvc-writable-ro-") as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        write_mock_kubectl(bin_dir, big_pods, plan)
        # Make TMPDIR unwritable after creation so a here-string temp file fails.
        ro_tmp = tmp_path / "ro-tmp"
        ro_tmp.mkdir()
        script_path = tmp_path / "check.sh"
        script_path.write_text(
            "#!/usr/bin/env bash\n"
            f"export SKIP_ANNOTATION={SKIP_ANNOTATION!r}\n"
            "export EXCLUDED_NAMESPACES='rook-ceph database security'\n"
            + script
        )
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["TMPDIR"] = str(ro_tmp)
        # Remove write bit on TMPDIR itself for the invoking user.
        ro_tmp.chmod(0o555)
        try:
            result = subprocess.run(
                ["bash", str(script_path)],
                capture_output=True,
                text=True,
                env=env,
                timeout=120,
            )
        finally:
            ro_tmp.chmod(0o755)
    out = result.stdout + result.stderr
    require(
        result.returncode == 0,
        f"large pod JSON under unwritable TMPDIR must still succeed (no here-string temp):\n{out}",
    )
    require("cannot create temp file" not in out, f"no here-doc temp failure:\n{out}")
    require("not_writable=0" in out, f"clean large sweep:\n{out}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    failures = 0
    passed = 0

    def run(name: str, fn, *args) -> None:
        nonlocal failures, passed
        try:
            fn(*args)
            print(f"[PASS] {name}")
            passed += 1
        except Failure as e:
            print(f"[FAIL] {name}: {e}")
            failures += 1
        except Exception as e:  # noqa: BLE001 - surface unexpected test bugs
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")
            failures += 1

    objects = load_app_objects()
    cron = next(o for o in objects if o.get("kind") == "CronJob")
    script = extract_cronjob_script(cron)

    run("flags_unwritable_motivating_bugs", test_flags_unwritable_motivating_bugs, script)
    run("clean_when_all_writable", test_clean_when_all_writable, script)
    run("skips_readonly_mount_and_skip_annotation", test_skips_readonly_mount_and_skip_annotation, script)
    run("skips_excluded_namespaces_without_exec", test_skips_excluded_namespaces_without_exec, script)
    run("skips_not_running_and_shell_less", test_skips_not_running_and_shell_less, script)
    run("sh_fallback_writable", test_sh_fallback_writable, script)
    run("kubectl_errors_are_skips_not_failures", test_kubectl_errors_are_skips_not_failures, script)
    run("empty_pod_list_is_fatal", test_empty_pod_list_is_fatal, script)
    run("no_write_side_effect_in_remote_command", test_no_write_side_effect_in_remote_command, script)
    run("script_avoids_herestring_for_pod_json", test_script_avoids_herestring_for_pod_json, script)
    run("rbac_split_role_and_exclusions", test_rbac_split_role_and_exclusions, objects)
    run("prometheusrule_job_failure_signal", test_prometheusrule_job_failure_signal, objects)
    run("cronjob_safety_and_discovery_contract", test_cronjob_safety_and_discovery_contract, objects)
    run("headlamp_main_mount_is_readonly", test_headlamp_main_mount_is_readonly)

    print(f"\n{passed} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
