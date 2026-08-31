#!/usr/bin/env python3
"""Behavioral regression test for system/pvc-mover-readable-check.

Pins the backup-mover readability check (2026-08-31): a CronJob that resolves
each engine's mover uid/gid from the LIVE ReplicationSource / SnapshotPolicy
and walks every backup-covered claim from a container that already mounts it,
so an identity mismatch that would make kopia fail closed is caught while every
other status surface is still green.

This test does not grep source text as a proxy for behavior. It:

  1. Runs the REAL walk.sh against fixture trees with known-bad permissions and
     asserts the counts, including one regression case per silent-false-clean
     trap. Each of those four traps makes the walker report a clean zero while
     measuring nothing, so a test that only checks the happy path would pass
     against a completely broken instrument.
  2. Extracts the real CronJob bash body and runs it under a mock `kubectl`
     serving fixture CRs, pods and walker output, asserting the OK / FINDING /
     INCONCLUSIVE / UNMEASURED classification and the exit codes - in
     particular that VolSync counts never fail the job and kopiur counts always
     do.
  3. Asserts the RBAC split-role contract, the alert regex actually matching
     the CronJob's own Job names, and the Flux overlay wiring.

Live cluster proof (apply + create job --from=cronjob + fault injection +
delete) is documented in the app README and is out of scope for this offline
gate.
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
APP = ROOT / "kubernetes/apps/base/system/pvc-mover-readable-check/app"
WALK_PATH = APP / "walk.sh"
CRONJOB_PATH = APP / "cronjob.yaml"
RBAC_PATH = APP / "rbac.yaml"
PROMRULE_PATH = APP / "prometheusrule.yaml"
KUSTOMIZATION_PATH = APP / "kustomization.yaml"
OVERLAY_PATH = ROOT / "kubernetes/apps/main/system/pvc-mover-readable-check.yaml"
SYSTEM_MAIN = ROOT / "kubernetes/apps/main/system/kustomization.yaml"

# The five namespaces that hold a claim covered by a backup engine and are
# bound for pods/exec. `database` is deliberately absent: it is one of
# pvc-writable-check's three permanent exclusions and this check does not
# widen that boundary, so database/pgadmin is reported UNMEASURED.
BOUND_NAMESPACES = frozenset(
    {"ai", "downloads", "home-automation", "media", "selfhosted"}
)
EXCLUDED_NAMESPACES = frozenset({"database"})

WALK_MOUNT_PATH = "/walk/walk.sh"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_yaml_docs(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]


def modern_bash_available() -> bool:
    """The CronJob body uses associative arrays, so it needs bash >= 4.
    macOS ships bash 3.2; the job's own image and CI both ship bash 5."""
    try:
        out = subprocess.run(
            ["bash", "-c", "echo ${BASH_VERSINFO[0]}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip().isdigit() and int(out.stdout) >= 4


def gnu_stat_available() -> bool:
    """The walker needs `stat -c`. BSD/macOS stat does not have it."""
    try:
        out = subprocess.run(
            ["stat", "-c", "%F", str(ROOT)], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and "directory" in out.stdout


# ---------------------------------------------------------------------------
# 1. The walker, against real fixture trees
# ---------------------------------------------------------------------------

# A mover identity that owns nothing in any fixture, so "unreadable to the
# mover" is produced by permission bits alone and needs no chown (and therefore
# no root) to construct.
ALIEN_UID = 4242
ALIEN_GID = 4242


def run_walk(root: Path, vs: tuple[int, int], kp: tuple[int, int], **kw) -> dict[str, str]:
    proc = subprocess.run(
        ["sh", str(WALK_PATH), str(root), str(vs[0]), str(vs[1]), str(kp[0]), str(kp[1])],
        capture_output=True,
        text=True,
        timeout=120,
        **kw,
    )
    require(
        proc.returncode == 0,
        f"walk.sh exited {proc.returncode}: {proc.stderr[:400]}",
    )
    values: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("WALKERR_SAMPLE="):
            values.setdefault("_samples", "")
            values["_samples"] += line + "\n"
            continue
        for tok in line.split():
            if "=" in tok:
                k, _, v = tok.partition("=")
                values[k] = v
    return values


def build_tree(base: Path) -> Path:
    """A fixture volume exercising every classification branch."""
    root = base / "vol"
    root.mkdir()
    (root / "world.txt").write_text("readable by anyone")
    (root / "world.txt").chmod(0o644)
    # Trap 4: a ZERO-BYTE file. busybox/GNU `stat -c %F` calls this
    # "regular empty file", so an exact "regular file" match silently drops it
    # from BOTH totals.
    (root / "empty.txt").touch()
    (root / "empty.txt").chmod(0o600)
    # Owner-only, and the owner is not the mover -> unreadable to the mover.
    (root / "secret.txt").write_text("owner only")
    (root / "secret.txt").chmod(0o600)
    # Owner-only directory: unreadable AND untraversable to the mover, but the
    # walking process owns it, so the walk still enumerates the subtree.
    # A world-readable file behind an owner-only directory. The FILE is
    # readable by its permission bits; it is the DIRECTORY that hides it. The
    # walker keeps these in separate columns exactly as the 2026-08-31 audit
    # did (ai/hermes: 1948 unreadable files AND 310 untraversable dirs, not
    # 89304 files), which is why an untraversable directory is the more
    # consequential number - it can hide an entire volume.
    private = root / "private"
    private.mkdir()
    (private / "buried.txt").write_text("readable by bits, hidden by its parent")
    (private / "buried.txt").chmod(0o644)
    private.chmod(0o700)
    (root / "link").symlink_to("world.txt")
    # lost+found: root-owned 0700 on every ext4 volume by design. Counted
    # separately, never a finding, and pruned so it cannot raise a walk error.
    lf = root / "lost+found"
    lf.mkdir()
    (lf / "#12345").write_text("orphan")
    lf.chmod(0o700)
    return root


def test_walker_counts_every_branch() -> None:
    with tempfile.TemporaryDirectory(prefix="walk-tree-") as tmp:
        root = build_tree(Path(tmp))
        v = run_walk(root, (ALIEN_UID, ALIEN_GID), (ALIEN_UID, ALIEN_GID))

        require(v["WALK_ERRORS"] == "0", f"expected no walk errors, got {v}")
        # 3 files at the root: world.txt, empty.txt, secret.txt. lost+found's
        # own file must NOT be in this total.
        require(
            v["FILES"] == "4",
            f"expected 4 files (3 root + 1 buried), got {v['FILES']} in {v}",
        )
        require(v["DIRS"] == "2", f"expected 2 dirs (vol, private), got {v['DIRS']}")
        require(v["SYMLINKS"] == "1", f"expected 1 symlink, got {v['SYMLINKS']}")
        require(
            v["LOST_FOUND"] == "1",
            f"lost+found must be counted separately and pruned, got {v['LOST_FOUND']}",
        )
        # Trap 4 regression: the zero-byte 0600 file must be counted as
        # unreadable. If "regular empty file" is dropped, this reads 1.
        # buried.txt is NOT counted here - it is mode 0644 and readable by its
        # own bits; its parent directory is what hides it, and that is counted
        # in the directory column below.
        require(
            v["KP_UNREADABLE_FILES"] == "2",
            "expected 2 unreadable files (secret.txt, empty.txt); a count of 1 means "
            f"the zero-byte file was silently dropped by an exact %F match: {v}",
        )
        require(
            v["KP_UNTRAVERSABLE_DIRS"] == "1",
            f"expected the 0700 dir to be untraversable, got {v}",
        )


def test_walker_zero_byte_file_is_counted_exactly_once() -> None:
    """Trap 4, isolated: an empty file lands in exactly one of the two totals."""
    with tempfile.TemporaryDirectory(prefix="walk-empty-") as tmp:
        root = Path(tmp) / "vol"
        root.mkdir()
        (root / "empty-readable").touch()
        (root / "empty-readable").chmod(0o644)
        (root / "empty-unreadable").touch()
        (root / "empty-unreadable").chmod(0o600)
        v = run_walk(root, (ALIEN_UID, ALIEN_GID), (ALIEN_UID, ALIEN_GID))
        require(v["FILES"] == "2", f"both empty files must be counted: {v}")
        require(
            v["KP_UNREADABLE_FILES"] == "1",
            f"exactly one empty file is unreadable, got {v}",
        )


def test_walker_reports_clean_for_a_matching_identity() -> None:
    """The same tree is fully readable to the identity that owns it."""
    with tempfile.TemporaryDirectory(prefix="walk-match-") as tmp:
        root = build_tree(Path(tmp))
        me = (os.getuid(), os.getgid())
        v = run_walk(root, me, me)
        require(v["WALK_ERRORS"] == "0", f"expected no walk errors: {v}")
        require(
            v["KP_UNREADABLE_FILES"] == "0" and v["KP_UNTRAVERSABLE_DIRS"] == "0",
            f"owner identity must read everything, got {v}",
        )


def test_walker_group_read_grants_access() -> None:
    """Readable also means: in the owning group with the group-read bit."""
    with tempfile.TemporaryDirectory(prefix="walk-group-") as tmp:
        root = Path(tmp) / "vol"
        root.mkdir()
        group_file = root / "group.txt"
        group_file.write_text("group readable")
        group_file.chmod(0o040)
        # Use the file's actual group, not os.getgid(). Temp trees on macOS
        # (and under nix-shell /private/tmp) often land with gid 0 rather than
        # the caller's primary group, so a hard-coded getgid() would make this
        # assert the fixture rather than the group-read branch of can_read.
        file_gid = group_file.stat().st_gid
        require(
            file_gid != ALIEN_GID,
            f"fixture group {file_gid} must differ from ALIEN_GID so the deny path is real",
        )
        v = run_walk(root, (ALIEN_UID, file_gid), (ALIEN_UID, ALIEN_GID))
        require(
            v["VS_UNREADABLE_FILES"] == "0",
            f"group-read must grant access to a group member: {v}",
        )
        require(
            v["KP_UNREADABLE_FILES"] == "1",
            f"a non-member with no other-read must be denied: {v}",
        )


def test_walker_directory_needs_execute_not_just_read() -> None:
    """A dir with r but no x is unusable: the mover cannot enter it."""
    with tempfile.TemporaryDirectory(prefix="walk-dirx-") as tmp:
        root = Path(tmp) / "vol"
        root.mkdir()
        d = root / "readable-not-traversable"
        d.mkdir()
        (d / "f").write_text("x")
        d.chmod(0o644)  # r for other, but no x
        try:
            v = run_walk(root, (ALIEN_UID, ALIEN_GID), (ALIEN_UID, ALIEN_GID))
            require(
                v["KP_UNTRAVERSABLE_DIRS"] == "1",
                f"a dir with read but no execute must count as untraversable: {v}",
            )
        finally:
            d.chmod(0o755)


def test_walker_missing_root_is_inconclusive_not_a_clean_zero() -> None:
    """Trap 3: a failed walk must surface errors, never report clean zeros."""
    with tempfile.TemporaryDirectory(prefix="walk-missing-") as tmp:
        v = run_walk(
            Path(tmp) / "does-not-exist", (ALIEN_UID, ALIEN_GID), (ALIEN_UID, ALIEN_GID)
        )
        require(
            v["WALK_ERRORS"] != "0",
            "a walk over a nonexistent root must report WALK_ERRORS>0, not a clean zero",
        )
        require("_samples" in v, "walk errors must be sampled into the output")


def test_walker_needs_no_writable_tmp() -> None:
    """Trap 2: /tmp is read-only in this repo's hardened containers.

    Run the walker with TMPDIR pointed at a nonexistent path. Any mktemp or
    `2>"$ERRF"` redirect would fail and take the whole walk with it.
    """
    with tempfile.TemporaryDirectory(prefix="walk-notmp-") as tmp:
        root = build_tree(Path(tmp))
        env = os.environ.copy()
        env["TMPDIR"] = str(Path(tmp) / "no-such-tmpdir")
        v = run_walk(root, (ALIEN_UID, ALIEN_GID), (ALIEN_UID, ALIEN_GID), env=env)
        require(v["WALK_ERRORS"] == "0", f"walk must not need a writable TMPDIR: {v}")
        require(v["KP_UNREADABLE_FILES"] == "2", f"counts must survive: {v}")
        require(v["KP_UNTRAVERSABLE_DIRS"] == "1", f"counts must survive: {v}")


def test_walker_never_uses_find_ownership_predicates() -> None:
    """Deliberately source-text: trap 1 cannot be caught behaviorally here.

    busybox `find` has no -uid/-gid and fails to usage text, which the walker
    would then read as a clean zero. CI and these fixtures run GNU find, which
    DOES implement those predicates, so a regression to `find -uid`/`-gid`
    would pass every behavioral fixture and only break inside the busybox app
    container where the walker actually runs. This exact trap already produced
    a silent false-clean zero once during the 2026-08-31 audit.
    """
    text = strip_shell_comments(WALK_PATH.read_text())
    for bad in (" -uid ", " -gid ", " -user ", " -group ", " -perm "):
        require(
            bad not in text,
            f"walk.sh must not use find's {bad.strip()} predicate (busybox lacks it, "
            "and it fails to usage text which reads as a clean zero)",
        )


def test_walker_is_read_only_against_the_volume() -> None:
    """The walk must not create, modify, chmod or delete anything."""
    with tempfile.TemporaryDirectory(prefix="walk-ro-") as tmp:
        root = build_tree(Path(tmp))

        def snapshot() -> list[tuple[str, int, int, int]]:
            out = []
            for p in sorted(root.rglob("*")):
                st = p.lstat()
                out.append((str(p.relative_to(root)), st.st_mode, st.st_size, st.st_mtime_ns))
            return out

        before = snapshot()
        run_walk(root, (ALIEN_UID, ALIEN_GID), (ALIEN_UID, ALIEN_GID))
        require(snapshot() == before, "walk.sh mutated the volume it measured")


def test_walker_reports_na_for_an_engine_that_does_not_cover_the_claim() -> None:
    with tempfile.TemporaryDirectory(prefix="walk-na-") as tmp:
        root = build_tree(Path(tmp))
        v = run_walk(root, (ALIEN_UID, ALIEN_GID), (-1, -1))
        require(
            v["KP_UNREADABLE_FILES"] == "NA" and v["KP_UNTRAVERSABLE_DIRS"] == "NA",
            f"a non-covering engine must report NA, never a misleading 0: {v}",
        )
        require(
            v["VS_UNREADABLE_FILES"] == "2",
            f"the covering engine must still be measured: {v}",
        )


# ---------------------------------------------------------------------------
# 2. The CronJob script, under a mock kubectl
# ---------------------------------------------------------------------------


def extract_cronjob_script() -> str:
    docs = load_yaml_docs(CRONJOB_PATH)
    cronjobs = [d for d in docs if d.get("kind") == "CronJob"]
    require(len(cronjobs) == 1, "expected exactly one CronJob")
    containers = cronjobs[0]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    require(len(containers) == 1, "expected exactly one check container")
    command = containers[0].get("command") or []
    require(
        len(command) >= 3 and command[0].endswith("bash") and command[1] == "-c",
        f"unexpected CronJob command shape: {command[:3]!r}",
    )
    script = command[2]
    require(
        WALK_MOUNT_PATH in script,
        f"script must feed the walker from {WALK_MOUNT_PATH}",
    )
    require(
        "replicationsources" in script and "snapshotpolicies" in script,
        "script must resolve mover identities from BOTH engines' live CRs",
    )
    return script


def rs(ns: str, pvc: str, uid: int, gid: int, name: str | None = None) -> dict[str, Any]:
    return {
        "metadata": {"namespace": ns, "name": name or f"{pvc}-ceph"},
        "spec": {
            "sourcePVC": pvc,
            "restic": {"moverSecurityContext": {"runAsUser": uid, "runAsGroup": gid}},
        },
    }


def sp(ns: str, pvcs: list[str], uid: int, gid: int, name: str = "policy") -> dict[str, Any]:
    return {
        "metadata": {"namespace": ns, "name": name},
        "spec": {
            "mover": {"podSecurityContext": {"runAsUser": uid, "runAsGroup": gid}},
            "sources": [{"pvc": {"name": p}, "readOnly": True} for p in pvcs],
        },
    }


def pod_obj(
    ns: str,
    name: str,
    claim: str,
    mount: str,
    *,
    container: str = "app",
    running: bool = True,
    sub_path: str | None = None,
) -> dict[str, Any]:
    vm: dict[str, Any] = {"name": "data", "mountPath": mount}
    if sub_path:
        vm["subPath"] = sub_path
    return {
        "metadata": {"namespace": ns, "name": name},
        "spec": {
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": claim}}],
            "containers": [{"name": container, "volumeMounts": [vm]}],
        },
        "status": {
            "containerStatuses": [
                {"name": container, "state": {"running": {}} if running else {"waiting": {}}}
            ]
        },
    }


def write_mock_kubectl(
    bin_dir: Path,
    sources: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    pods: list[dict[str, Any]],
    walk_results: dict[str, str],
    *,
    fail_gets: frozenset[str] | set[str] | None = None,
) -> None:
    """kubectl shim serving fixture CRs/pods and per-claim walker output.

    walk_results maps "ns/pod/container" to the literal text the walker would
    print, or the sentinel "FORBIDDEN" / "NOSHELL" / "TIMEOUT".

    fail_gets is an optional set of resource names (`replicationsources`,
    `snapshotpolicies`, `pods`) whose `kubectl get` exits non-zero with an
    error on stderr - used to prove discovery failures never report a false
    clean.
    """
    def dump(name: str, items: list[dict[str, Any]]) -> Path:
        p = bin_dir / name
        p.write_text(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}))
        return p

    rs_path = dump("rs.json", sources)
    sp_path = dump("sp.json", policies)
    pods_path = dump("pods.json", pods)
    results_path = bin_dir / "results.json"
    results_path.write_text(json.dumps(walk_results))
    fail_path = bin_dir / "fail_gets.txt"
    fail_path.write_text("\n".join(sorted(fail_gets or ())))

    kubectl = bin_dir / "kubectl"
    kubectl.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            set -uo pipefail
            if [[ "${{1:-}}" == "get" ]]; then
              res="${{2:-}}"
              if grep -qx "$res" {fail_path!s} 2>/dev/null; then
                echo "Error from server (InternalError): mock get $res failed" >&2
                exit 1
              fi
              case "$res" in
                replicationsources) cat {rs_path!s}; exit 0 ;;
                snapshotpolicies)   cat {sp_path!s}; exit 0 ;;
                pods)               cat {pods_path!s}; exit 0 ;;
              esac
              echo "unexpected get: $*" >&2; exit 1
            fi
            if [[ "${{1:-}}" == "exec" ]]; then
              # kubectl exec -n NS POD -c CTR -i -- sh -s -- ...
              ns=$3; pod=$4; ctr=$6
              # Drain the walker on stdin exactly as the real kubectl would.
              cat >/dev/null
              out=$(jq -r --arg k "$ns/$pod/$ctr" '.[$k] // "MISSING"' {results_path!s})
              case "$out" in
                MISSING)   echo "no fixture for $ns/$pod/$ctr" >&2; exit 1 ;;
                FORBIDDEN) echo 'Error from server (Forbidden): pods/exec is forbidden'; exit 1 ;;
                NOSHELL)   echo 'OCI runtime exec failed: exec: "sh": executable file not found in $PATH'; exit 126 ;;
                TIMEOUT)   echo 'error: unable to upgrade connection: container not found'; exit 1 ;;
              esac
              printf '%s\\n' "$out"
              exit 0
            fi
            echo "unexpected kubectl invocation: $*" >&2
            exit 1
            """
        )
    )
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IXUSR)

    # The script wraps every exec in `timeout`. Linux CI and the job's own
    # image both have it; macOS does not, so shim it only when it is missing
    # rather than shadowing the real one.
    if shutil.which("timeout") is None:
        shim = bin_dir / "timeout"
        shim.write_text('#!/usr/bin/env bash\nshift\nexec "$@"\n')
        shim.chmod(shim.stat().st_mode | stat.S_IXUSR)


def walker_output(files=10, dirs=3, vs=(0, 0), kp=(0, 0), errors=0) -> str:
    kpf = "NA" if kp is None else kp[0]
    kpd = "NA" if kp is None else kp[1]
    lines = [
        f"FILES={files} DIRS={dirs} SYMLINKS=0 UNCLASSIFIED=0 LOST_FOUND=1",
        f"VS_UNREADABLE_FILES={vs[0]} VS_UNTRAVERSABLE_DIRS={vs[1]}",
        f"KP_UNREADABLE_FILES={kpf} KP_UNTRAVERSABLE_DIRS={kpd}",
        f"WALK_ERRORS={errors}",
    ]
    if errors:
        lines.append("WALKERR_SAMPLE=find: /vol/x: Permission denied")
    return "\n".join(lines)


def run_cronjob_script(
    script: str,
    sources: list[dict[str, Any]],
    policies: list[dict[str, Any]],
    pods: list[dict[str, Any]],
    walk_results: dict[str, str],
    *,
    fail_gets: frozenset[str] | set[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="pvc-mover-readable-") as tmp:
        tmp_path = Path(tmp)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        write_mock_kubectl(
            bin_dir, sources, policies, pods, walk_results, fail_gets=fail_gets
        )
        # The shipped script reads the walker from its ConfigMap mount. Point
        # that one literal at a harness copy; the literal itself is asserted in
        # extract_cronjob_script so the contract stays pinned.
        walk_copy = tmp_path / "walk.sh"
        shutil.copy(WALK_PATH, walk_copy)
        body = script.replace(WALK_MOUNT_PATH, str(walk_copy))
        script_path = tmp_path / "check.sh"
        script_path.write_text(
            "#!/usr/bin/env bash\n"
            f"export EXCLUDED_NAMESPACES={' '.join(sorted(EXCLUDED_NAMESPACES))!r}\n"
            "export EXEC_TIMEOUT=30\n" + body
        )
        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        return subprocess.run(
            ["bash", str(script_path)], capture_output=True, text=True, env=env, timeout=120
        )


def test_kopiur_finding_fails_the_job() -> None:
    proc = run_cronjob_script(
        extract_cronjob_script(),
        [rs("selfhosted", "changedetection-config", 1000, 1000)],
        [sp("selfhosted", ["changedetection-config"], 1000, 1000)],
        [pod_obj("selfhosted", "changedetection-0", "changedetection-config", "/datastore")],
        {"selfhosted/changedetection-0/app": walker_output(files=3058, kp=(2292, 0))},
    )
    require(proc.returncode == 1, f"a kopiur finding must fail the job: {proc.stdout[-800:]}")
    require("FINDING" in proc.stdout, "the finding must be logged")
    require(
        "2292" in proc.stdout and "FAILURES" in proc.stdout,
        f"the count and the FAILURES block must appear: {proc.stdout[-800:]}",
    )


def test_volsync_finding_alone_does_not_fail_the_job() -> None:
    """VolSync is report-only: its staged clone is writable, so kubelet's
    fsGroup walk rescues it before restic reads. Collected, never alerted."""
    proc = run_cronjob_script(
        extract_cronjob_script(),
        [rs("ai", "hermes", 1000, 1000)],
        [sp("ai", ["hermes"], 10000, 10000)],
        [pod_obj("ai", "hermes-0", "hermes", "/opt/data")],
        {"ai/hermes-0/app": walker_output(files=89343, vs=(1950, 310), kp=(0, 0))},
    )
    require(
        proc.returncode == 0,
        f"VolSync counts must never fail the job: {proc.stdout[-800:]}",
    )
    require("VOLSYNC REPORT-ONLY" in proc.stdout, "VolSync counts must be reported")
    require("1950" in proc.stdout, "the VolSync count must be printed")
    require("kopiur_findings=0" in proc.stdout, "kopiur must be clean here")


def test_walk_errors_are_inconclusive_and_fail() -> None:
    proc = run_cronjob_script(
        extract_cronjob_script(),
        [rs("media", "plex", 1000, 1000)],
        [sp("media", ["plex"], 2000, 2000)],
        [pod_obj("media", "plex-0", "plex", "/config")],
        {"media/plex-0/app": walker_output(kp=(0, 0), errors=2)},
    )
    require(
        proc.returncode == 1,
        f"a walk with errors must never pass: {proc.stdout[-800:]}",
    )
    require("INCONCLUSIVE" in proc.stdout, "it must be labelled INCONCLUSIVE")
    require(
        "measured=0" in proc.stdout,
        f"an inconclusive claim must not be counted as measured: {proc.stdout[-500:]}",
    )


def test_unmeasured_claims_are_never_reported_as_passing() -> None:
    """Unmounted, subPath-only and RBAC-excluded claims: reported, not passed."""
    script = extract_cronjob_script()
    proc = run_cronjob_script(
        script,
        [
            rs("downloads", "recyclarr-config", 1000, 1000),
            rs("selfhosted", "ntfy", 1000, 1000),
            rs("database", "pgadmin", 1000, 1000),
        ],
        [
            sp("downloads", ["recyclarr-config"], 2000, 2000, name="recyclarr"),
            sp("selfhosted", ["ntfy"], 1000, 1000, name="ntfy"),
            sp("database", ["pgadmin"], 5050, 5050, name="pgadmin"),
        ],
        [
            pod_obj("selfhosted", "ntfy-0", "ntfy", "/var/cache/ntfy", sub_path="cache"),
            pod_obj("database", "pgadmin-0", "pgadmin", "/var/lib/pgadmin"),
        ],
        {},
    )
    require(proc.returncode == 0, f"structural gaps must not alert: {proc.stdout[-800:]}")
    out = proc.stdout
    require(out.count("UNMEASURED") >= 4, f"all three must be UNMEASURED: {out}")
    require("clean=0" in out and "measured=0" in out, f"none may count as clean: {out}")
    for claim, marker in (
        ("downloads/recyclarr-config", "no pod mounts this claim"),
        ("selfhosted/ntfy", "subPath"),
        ("database/pgadmin", "excluded"),
    ):
        require(claim in out, f"{claim} must be listed: {out}")
        require(marker in out, f"{claim} must state its reason ({marker}): {out}")
    require(
        "unmounted=1" in out and "subpath_only=1" in out and "namespace_excluded=1" in out,
        f"each unmeasured reason needs its own counter: {out}",
    )
    # The excluded namespace must be short-circuited before any exec attempt.
    require(
        "pgadmin-0" not in out.split("UNMEASURED - these claims")[0].replace(
            "UNMEASURED (namespace excluded from pods/exec by design) database/pgadmin", ""
        ),
        "an excluded namespace must never be exec'd into",
    )


def test_exec_failures_are_unmeasured_never_findings() -> None:
    """A kubectl-level failure is inconclusive, never evidence of a bad mount."""
    for sentinel, expect in (
        ("FORBIDDEN", "rbac=1"),
        ("NOSHELL", "no_tools=1"),
        ("TIMEOUT", "transient=1"),
    ):
        proc = run_cronjob_script(
            extract_cronjob_script(),
            [rs("media", "seerr", 1000, 1000)],
            [sp("media", ["seerr"], 2000, 2000)],
            [pod_obj("media", "seerr-0", "seerr", "/app/config")],
            {"media/seerr-0/app": sentinel},
        )
        require(
            proc.returncode == 0,
            f"{sentinel} must not fail the job: {proc.stdout[-600:]}",
        )
        require(
            expect in proc.stdout,
            f"{sentinel} must land in its own counter ({expect}): {proc.stdout[-600:]}",
        )
        require(
            "FINDING" not in proc.stdout,
            f"{sentinel} must never be reported as a readability finding",
        )


def test_identity_comes_from_the_live_crs_not_a_default() -> None:
    """The per-claim identity in the log must be the CR's, not 1000:1000."""
    proc = run_cronjob_script(
        extract_cronjob_script(),
        [rs("downloads", "prowlarr-config", 1000, 1000)],
        [sp("downloads", ["prowlarr-config"], 3002, 3000)],
        [pod_obj("downloads", "prowlarr-0", "prowlarr-config", "/config")],
        {"downloads/prowlarr-0/app": walker_output()},
    )
    require(proc.returncode == 0, proc.stdout[-600:])
    require(
        "kopiur=3002:3000" in proc.stdout,
        f"the live SnapshotPolicy identity must be used: {proc.stdout[-600:]}",
    )
    require("volsync=1000:1000" in proc.stdout, "the live ReplicationSource identity must be used")


def test_disagreeing_kopiur_policies_are_a_finding() -> None:
    proc = run_cronjob_script(
        extract_cronjob_script(),
        [rs("ai", "opencode", 1000, 1000)],
        [
            sp("ai", ["opencode"], 1000, 1000, name="opencode-ceph"),
            sp("ai", ["opencode"], 2000, 2000, name="opencode-r2"),
        ],
        [pod_obj("ai", "opencode-0", "opencode", "/home/opencode")],
        {"ai/opencode-0/app": walker_output()},
    )
    require(
        proc.returncode == 1,
        f"SnapshotPolicies disagreeing on the mover identity must fail: {proc.stdout[-600:]}",
    )
    require("disagree" in proc.stdout, "the conflict must be named in the log")


def test_no_backup_crs_at_all_is_fatal() -> None:
    """An empty scope means discovery broke, not that the fleet is clean."""
    proc = run_cronjob_script(extract_cronjob_script(), [], [], [], {})
    require(
        proc.returncode == 1,
        f"an empty identity map must be fatal, not a clean sweep: {proc.stdout}",
    )
    require("FATAL" in proc.stderr or "FATAL" in proc.stdout, "it must say why")


def _happy_discovery_fixture() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, str]
]:
    """A single claim that would report OK if discovery succeeded."""
    return (
        [rs("selfhosted", "changedetection-config", 1000, 1000)],
        [sp("selfhosted", ["changedetection-config"], 1000, 1000)],
        [
            pod_obj(
                "selfhosted",
                "changedetection-0",
                "changedetection-config",
                "/datastore",
            )
        ],
        {"selfhosted/changedetection-0/app": walker_output()},
    )


def _assert_discovery_fatal(proc: subprocess.CompletedProcess[str], named: str) -> None:
    """A discovery failure must exit non-zero, name the call, and never clean."""
    combined = f"{proc.stdout}\n{proc.stderr}"
    require(
        proc.returncode == 1,
        f"{named} discovery failure must exit non-zero: {combined[-800:]}",
    )
    require("FATAL" in combined, f"{named} discovery failure must print FATAL: {combined[-800:]}")
    require(
        named in combined,
        f"FATAL must name the failed discovery call ({named}): {combined[-800:]}",
    )
    require(
        "no readability conclusion can be drawn" in combined,
        f"FATAL must state that no conclusion can be drawn: {combined[-800:]}",
    )
    # Prove it did not report a false clean - exit 1 alone is not enough if the
    # sweep already classified claims before failing.
    require(
        re.search(r"(?m)^OK\b", combined) is None,
        f"{named} discovery failure must emit no OK classification: {combined[-800:]}",
    )
    require(
        "clean=" not in combined,
        f"{named} discovery failure must emit no clean= summary: {combined[-800:]}",
    )


def test_replicationsources_discovery_failure_is_fatal_not_false_clean() -> None:
    sources, policies, pods, walks = _happy_discovery_fixture()
    proc = run_cronjob_script(
        extract_cronjob_script(),
        sources,
        policies,
        pods,
        walks,
        fail_gets={"replicationsources"},
    )
    _assert_discovery_fatal(proc, "replicationsources")


def test_snapshotpolicies_discovery_failure_is_fatal_not_false_clean() -> None:
    sources, policies, pods, walks = _happy_discovery_fixture()
    proc = run_cronjob_script(
        extract_cronjob_script(),
        sources,
        policies,
        pods,
        walks,
        fail_gets={"snapshotpolicies"},
    )
    _assert_discovery_fatal(proc, "snapshotpolicies")


def test_pods_discovery_failure_is_fatal_not_false_clean() -> None:
    sources, policies, pods, walks = _happy_discovery_fixture()
    proc = run_cronjob_script(
        extract_cronjob_script(),
        sources,
        policies,
        pods,
        walks,
        fail_gets={"pods"},
    )
    _assert_discovery_fatal(proc, "pods")


def test_empty_snapshotpolicies_map_is_fatal_not_na_clean() -> None:
    """A successful but empty kopiur query is discovery breakage, not NA."""
    sources, _policies, pods, walks = _happy_discovery_fixture()
    proc = run_cronjob_script(
        extract_cronjob_script(),
        sources,
        [],
        pods,
        walks,
    )
    _assert_discovery_fatal(proc, "snapshotpolicies")


def strip_shell_comments(script: str) -> str:
    """Drop whole-line `#` comments so a rule can be pinned in prose without
    the test matching its own explanation."""
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


# ---------------------------------------------------------------------------
# 3. Manifest contract
# ---------------------------------------------------------------------------


def test_rbac_split_role_and_narrow_exec_binding() -> None:
    docs = load_yaml_docs(RBAC_PATH)
    kinds = {(d["kind"], d["metadata"]["name"]) for d in docs}
    require(
        ("ClusterRole", "pvc-mover-readable-check-exec") in kinds,
        "the exec ClusterRole must exist",
    )
    crbs = [d for d in docs if d["kind"] == "ClusterRoleBinding"]
    for crb in crbs:
        require(
            crb["roleRef"]["name"] != "pvc-mover-readable-check-exec",
            "pods/exec must NEVER be granted by a ClusterRoleBinding - exclusion is "
            "enforced by the absence of a namespaced RoleBinding",
        )
    read_role = next(
        d for d in docs if d["kind"] == "ClusterRole" and d["metadata"]["name"].endswith("-read")
    )
    verbs = {v for r in read_role["rules"] for v in r["verbs"]}
    require(verbs <= {"get", "list"}, f"the read role must be read-only, got {verbs}")
    resources = {r for rule in read_role["rules"] for r in rule["resources"]}
    require(
        resources == {"pods", "replicationsources", "snapshotpolicies"},
        f"the read role must grant exactly discovery, got {resources}",
    )
    exec_role = next(
        d for d in docs if d["kind"] == "ClusterRole" and d["metadata"]["name"].endswith("-exec")
    )
    require(
        [(r["resources"], r["verbs"]) for r in exec_role["rules"]]
        == [(["pods/exec"], ["create"])],
        "the exec role must grant pods/exec create and nothing else",
    )
    bound = {
        d["metadata"]["namespace"]
        for d in docs
        if d["kind"] == "RoleBinding"
        and d["roleRef"]["name"] == "pvc-mover-readable-check-exec"
    }
    require(
        bound == BOUND_NAMESPACES,
        f"exec must be bound in exactly the backup-covered namespaces; got {bound}",
    )
    require(
        not (bound & EXCLUDED_NAMESPACES),
        "the check must not widen pvc-writable-check's permanent exclusions",
    )
    # Every RoleBinding keeps its own namespace, so Flux targetNamespace would
    # collapse them; the overlay test below pins that.
    for d in docs:
        if d["kind"] == "RoleBinding":
            require(d["metadata"].get("namespace"), "each RoleBinding must declare its namespace")


def test_excluded_namespaces_match_the_rbac_gap() -> None:
    """The script's short-circuit list must equal the unbound namespaces."""
    docs = load_yaml_docs(CRONJOB_PATH)
    env = {
        e["name"]: e["value"]
        for e in docs[0]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    declared = set(env["EXCLUDED_NAMESPACES"].split())
    require(
        declared == EXCLUDED_NAMESPACES,
        f"EXCLUDED_NAMESPACES must stay in lockstep with rbac.yaml; got {declared}",
    )


def test_alert_regex_matches_this_cronjobs_own_job_names() -> None:
    """A CronJob creates Jobs named <cronjob>-<unix-minutes>. The alert regex
    must match those, or the check fails silently forever."""
    rule = load_yaml_docs(PROMRULE_PATH)[0]
    alerts = rule["spec"]["groups"][0]["rules"]
    require(len(alerts) == 1, "expected exactly one alert")
    expr = alerts[0]["expr"]
    require('reason!=""' in expr, "the expr must exclude ksm's empty-reason gauge series")
    m = re.search(r'job_name=~"([^"]+)"', expr)
    require(m is not None, f"the expr must filter on job_name: {expr}")
    pattern = re.compile(m.group(1))
    cronjob_name = load_yaml_docs(CRONJOB_PATH)[0]["metadata"]["name"]
    require(
        pattern.match(f"{cronjob_name}-29802857") is not None,
        f"the alert regex {m.group(1)!r} does not match a real Job name for {cronjob_name!r}",
    )
    require(
        pattern.match("pvc-writable-check-29802857") is None,
        "the alert regex must not also match the sibling check's Jobs",
    )
    require(alerts[0]["labels"]["severity"] == "warning", "severity must match the sibling")


def test_cronjob_safety_knobs_and_hardening() -> None:
    cj = load_yaml_docs(CRONJOB_PATH)[0]
    spec = cj["spec"]
    require(spec["concurrencyPolicy"] == "Forbid", "sweeps must not overlap")
    minute = spec["schedule"].split()[0]
    require(
        spec["schedule"].endswith("*/6 * * *"),
        f"cadence must match pvc-writable-check's 6h, got {spec['schedule']}",
    )
    sibling = load_yaml_docs(
        ROOT / "kubernetes/apps/base/system/pvc-writable-check/app/cronjob.yaml"
    )[0]["spec"]["schedule"]
    require(
        minute != sibling.split()[0],
        "the two exec sweeps must not start in the same minute",
    )
    job = spec["jobTemplate"]["spec"]
    require(job["backoffLimit"] <= 1, "a real mismatch will not fix itself on retry")
    require(job["ttlSecondsAfterFinished"] >= 86400, "failed Jobs must outlive the alert window")
    pod_spec = job["template"]["spec"]
    require(pod_spec["securityContext"]["runAsNonRoot"] is True, "the sweep must not run as root")
    ctr = pod_spec["containers"][0]
    require(
        ctr["securityContext"]["readOnlyRootFilesystem"] is True,
        "the sweep container must have a read-only root filesystem",
    )
    require(ctr["securityContext"]["capabilities"]["drop"] == ["ALL"], "drop ALL capabilities")
    mounts = {m["mountPath"]: m for m in ctr["volumeMounts"]}
    require("/walk" in mounts, "the walker ConfigMap must be mounted")
    require(mounts["/walk"].get("readOnly") is True, "the walker mount must be read-only")
    require(
        "subPath" not in mounts["/walk"],
        "the walker must not be a subPath mount - kubelet never refreshes those",
    )


def test_walker_is_shipped_as_a_hashed_configmap() -> None:
    kz = yaml.safe_load(KUSTOMIZATION_PATH.read_text())
    gens = kz.get("configMapGenerator") or []
    require(len(gens) == 1, "expected exactly one configMapGenerator")
    require("walk.sh" in gens[0]["files"], "the generator must ship walk.sh")
    require(
        not (kz.get("generatorOptions") or {}).get("disableNameSuffixHash"),
        "the name-suffix hash must stay enabled so an edited walker is picked up",
    )


def test_overlay_wiring() -> None:
    overlay = load_yaml_docs(OVERLAY_PATH)[0]
    require(
        "targetNamespace" not in overlay["spec"],
        "targetNamespace would collapse every per-namespace RoleBinding onto one namespace",
    )
    require(overlay["spec"].get("wait") is False, "wait must stay false")
    require(
        overlay["spec"]["path"].endswith("system/pvc-mover-readable-check/app"),
        "the overlay must point at this app",
    )
    listed = yaml.safe_load(SYSTEM_MAIN.read_text())["resources"]
    require(
        "./pvc-mover-readable-check.yaml" in listed,
        "the overlay must be registered in kubernetes/apps/main/system/kustomization.yaml",
    )


def test_no_flux_envsubst_collision_in_the_walker() -> None:
    """Deliberately source-text: the failure mode is not observable locally.

    A literal ${...} token would fail the whole Kustomization under Flux's
    strict-mode envsubst (see AGENTS.md 'postBuild.substitute collision',
    which caused a 44-day four-app outage). Local execution of walk.sh never
    runs that envsubst pass, so a behavioral fixture cannot catch the freeze.
    """
    require(
        "${" not in WALK_PATH.read_text(),
        "walk.sh must contain no ${...} token: Flux's strict envsubst runs over the "
        "whole build output and would fail the entire Kustomization",
    )


def main() -> int:
    failures: list[str] = []
    passed = 0
    skipped = 0

    walker_tests = [
        test_walker_counts_every_branch,
        test_walker_zero_byte_file_is_counted_exactly_once,
        test_walker_reports_clean_for_a_matching_identity,
        test_walker_group_read_grants_access,
        test_walker_directory_needs_execute_not_just_read,
        test_walker_missing_root_is_inconclusive_not_a_clean_zero,
        test_walker_needs_no_writable_tmp,
        test_walker_is_read_only_against_the_volume,
        test_walker_reports_na_for_an_engine_that_does_not_cover_the_claim,
    ]
    script_tests = [
        test_kopiur_finding_fails_the_job,
        test_volsync_finding_alone_does_not_fail_the_job,
        test_walk_errors_are_inconclusive_and_fail,
        test_unmeasured_claims_are_never_reported_as_passing,
        test_exec_failures_are_unmeasured_never_findings,
        test_identity_comes_from_the_live_crs_not_a_default,
        test_disagreeing_kopiur_policies_are_a_finding,
        test_no_backup_crs_at_all_is_fatal,
        test_replicationsources_discovery_failure_is_fatal_not_false_clean,
        test_snapshotpolicies_discovery_failure_is_fatal_not_false_clean,
        test_pods_discovery_failure_is_fatal_not_false_clean,
        test_empty_snapshotpolicies_map_is_fatal_not_na_clean,
    ]
    offline_tests = [
        test_walker_never_uses_find_ownership_predicates,
        test_rbac_split_role_and_narrow_exec_binding,
        test_excluded_namespaces_match_the_rbac_gap,
        test_alert_regex_matches_this_cronjobs_own_job_names,
        test_cronjob_safety_knobs_and_hardening,
        test_walker_is_shipped_as_a_hashed_configmap,
        test_overlay_wiring,
        test_no_flux_envsubst_collision_in_the_walker,
    ]

    have_gnu_stat = gnu_stat_available()
    have_jq = shutil.which("jq") is not None
    have_bash4 = modern_bash_available()

    for fn in offline_tests + walker_tests + script_tests:
        name = fn.__name__
        # The walker shells out to `stat -c`, which BSD/macOS stat lacks.
        if fn in walker_tests and not have_gnu_stat:
            print(f"  SKIP {name} (no GNU `stat -c`; this gate runs on Linux CI)")
            skipped += 1
            continue
        # The CronJob body needs bash 4 associative arrays and jq. It runs the
        # real script under a mock kubectl, so it does not need GNU stat.
        if fn in script_tests and not (have_jq and have_bash4):
            missing = "jq" if not have_jq else "bash >= 4"
            print(f"  SKIP {name} ({missing} not available; this gate runs on Linux CI)")
            skipped += 1
            continue
        try:
            fn()
        except Failure as exc:
            failures.append(f"{name}: {exc}")
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: unexpected {type(exc).__name__}: {exc}")
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"  ok   {name}")

    print(f"\n{passed} passed, {len(failures)} failed, {skipped} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
