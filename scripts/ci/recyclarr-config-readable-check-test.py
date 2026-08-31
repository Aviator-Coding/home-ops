#!/usr/bin/env python3
"""Behavioral regression test for the recyclarr-config readable-check procedure.

Pins the 2026-08-31 deliverable that made downloads/recyclarr-config measurable
for kopiur mover read access. The claim is a CronJob PVC (~18 s/day pod life),
so Stage 3 could not exec-probe it like the other mountable claims. The
accepted approach is a CSI VolumeSnapshot restored into a scratch PVC, walked
read-only by a root pod that classifies mode/owner bits against the live mover
identity 2000:2000 - never mounting or modifying the live RWO claim.

This test does NOT re-run the live cluster probe (fresh worktrees have no
kubeconfig; AGENTS.md). It pins the public result + procedure contract the
same way kopiur-stage2-test.py pins the sabnzbd restore drill, and it
*executes* the measure script shipped inside the procedure doc against
synthetic fixtures so the classification logic is proven behaviorally:

  1. Extract measure.sh from the procedure document (the durable operator
     interface - not a reimplementation).
  2. Run it under a PATH that supplies a busybox-compatible `stat -c` shim
     (macOS/BSD stat is not GNU/busybox), against fixture trees covering:
       - owner/group/other readability and directory execute bits
       - busybox "regular empty file" zero-byte trap
       - lost+found exclusion (root-owned 0700 by design, not a finding)
       - walk-error fail-closed (non-zero -> inconclusive, not a clean pass)
       - symlink counting without mode checks
  3. Parse the procedure doc's recorded live verdict and the GitOps pointers
     (recyclarr overlay KOPIUR_PUID/PGID + Readme SecurityContextCompatible
     row) as structured fields and assert the acceptance criteria.

Live numbers recorded 2026-08-31 (public result contract, not re-measured
here): 2913/2913 files, 607/607 dirs readable by mover 2000:2000, 0 walk
errors, cross-checked against kopiur snapshot stats filesNew=2913.
"""

from __future__ import annotations

import os
import re
import shutil
import stat as statmod
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs/backups/recyclarr-config-readable-check-2026-08-31.md"
OVERLAY = ROOT / "kubernetes/apps/main/downloads/recyclarr.yaml"
COMPONENT_README = ROOT / "kubernetes/components/kopiur/Readme.md"
HELMRELEASE = (
    ROOT / "kubernetes/apps/base/downloads/recyclarr/app/helmrelease.yaml"
)

# Public result contract from the live 2026-08-31 probe.
LIVE_FILES = 2913
LIVE_DIRS = 607
MOVER_UID = "2000"
MOVER_GID = "2000"
KOPIUR_SNAPSHOT_HINT = "recyclarr-ceph-20260831090426"

passed = 0
failed = 0


def require(cond: bool, msg: str) -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"[PASS] {msg}")
    else:
        failed += 1
        print(f"[FAIL] {msg}")


def extract_measure_script(doc_text: str) -> str:
    """Pull the measure.sh body from the procedure doc's install heredoc.

    The doc is the owned operator interface: the script lives inside a
    `<<'EOF' ... EOF` block after `cat > /tmp/measure.sh`. Extracting it is
    loading the public procedure artifact, not grepping incidental source.
    """
    marker = "cat > /tmp/measure.sh && chmod 0755 /tmp/measure.sh"
    idx = doc_text.find(marker)
    if idx < 0:
        raise AssertionError("procedure doc missing measure.sh install heredoc")
    # Find the opening <<'EOF' after the marker, then the matching closing EOF.
    heredoc_start = doc_text.find("<<'EOF'", idx)
    if heredoc_start < 0:
        raise AssertionError("measure.sh heredoc opener not found")
    body_start = doc_text.find("\n", heredoc_start) + 1
    # Closing fence is a line that is exactly EOF (possibly indented in the md
    # fence, but inside the bash block it is bare EOF).
    rest = doc_text[body_start:]
    # The install block ends at a line containing only EOF before the next
    # kubectl exec that runs the script.
    m = re.search(r"^EOF\s*$", rest, re.M)
    if not m:
        raise AssertionError("measure.sh heredoc closer not found")
    body = rest[: m.start()]
    if not body.lstrip().startswith("#!/bin/sh"):
        raise AssertionError("extracted measure script missing shebang")
    return body


def write_stat_shim(bin_dir: Path) -> None:
    """Install a busybox-compatible `stat -c '%F|%a|%u|%g|%n'` shim.

    The measure script is written for alpine/busybox. CI hosts may only have
    BSD stat. The shim reads real mode bits from the filesystem (so chmod in
    the fixture is load-bearing) and synthesizes uid/gid from a sidecar map
    so we can model mover 2000:2000 ownership without root chown.
    """
    shim = bin_dir / "stat"
    shim.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import os, stat, sys
            from pathlib import Path

            # Args: -c 'FORMAT' file...
            args = sys.argv[1:]
            if not args or args[0] != "-c" or len(args) < 3:
                print("stat shim: only supports -c FORMAT file...", file=sys.stderr)
                sys.exit(1)
            fmt = args[1]
            if fmt != "%F|%a|%u|%g|%n":
                print(f"stat shim: unsupported format {fmt!r}", file=sys.stderr)
                sys.exit(1)
            map_path = os.environ.get("READABLE_CHECK_OWNER_MAP", "")
            owners: dict[str, tuple[str, str]] = {}
            if map_path and Path(map_path).is_file():
                for line in Path(map_path).read_text().splitlines():
                    if not line.strip() or line.startswith("#"):
                        continue
                    path, uid, gid = line.split("|", 2)
                    owners[path] = (uid, gid)

            rc = 0
            for path in args[2:]:
                try:
                    st = os.lstat(path)
                except OSError as e:
                    print(f"stat: {path}: {e}", file=sys.stderr)
                    rc = 1
                    continue
                mode = st.st_mode
                if stat.S_ISLNK(mode):
                    ftype = "symbolic link"
                elif stat.S_ISDIR(mode):
                    ftype = "directory"
                elif stat.S_ISREG(mode):
                    # busybox reports zero-byte files distinctly - the trap the
                    # procedure classifier must handle via prefix match.
                    ftype = (
                        "regular empty file" if st.st_size == 0 else "regular file"
                    )
                else:
                    ftype = "unknown"
                perm = stat.S_IMODE(mode)
                # Prefer the fixture owner map so we can model 2000:2000 without root.
                uid, gid = owners.get(path, (str(st.st_uid), str(st.st_gid)))
                print(f"{ftype}|{perm:o}|{uid}|{gid}|{path}")
            sys.exit(rc)
            """
        )
    )
    shim.chmod(0o755)


def run_measure(
    root_dir: Path,
    owner_map: dict[str, tuple[str, str]],
    mover_uid: str = MOVER_UID,
    mover_gid: str = MOVER_GID,
    measure_script: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Execute the extracted measure.sh against root_dir; return (rc, out, err)."""
    assert measure_script is not None
    with tempfile.TemporaryDirectory(prefix="recyclarr-readable-") as td:
        tdp = Path(td)
        script_path = tdp / "measure.sh"
        script_path.write_text(measure_script)
        script_path.chmod(0o755)

        bin_dir = tdp / "bin"
        bin_dir.mkdir()
        write_stat_shim(bin_dir)

        map_path = tdp / "owners.map"
        map_path.write_text(
            "".join(f"{path}|{uid}|{gid}\n" for path, (uid, gid) in owner_map.items())
        )

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
        env["ROOT"] = str(root_dir)
        env["MOVER_UID"] = mover_uid
        env["MOVER_GID"] = mover_gid
        env["READABLE_CHECK_OWNER_MAP"] = str(map_path)
        if extra_env:
            env.update(extra_env)

        proc = subprocess.run(
            ["sh", str(script_path)],
            capture_output=True,
            text=True,
            env=env,
            cwd=td,
        )
        return proc.returncode, proc.stdout, proc.stderr


def parse_verdict(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def build_owner_map(root: Path, default_uid: str, default_gid: str) -> dict[str, tuple[str, str]]:
    """Walk root and assign default ownership; caller overrides specific paths."""
    owners: dict[str, tuple[str, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # include the directory itself
        owners[dirpath] = (default_uid, default_gid)
        for name in dirnames + filenames:
            owners[str(Path(dirpath) / name)] = (default_uid, default_gid)
    # os.walk misses nothing under root if root itself is seeded:
    owners[str(root)] = (default_uid, default_gid)
    return owners


def test_classifier_all_readable(measure_script: str) -> None:
    """Fixture owned 2000:2000 with open modes -> full clean pass for mover 2000."""
    with tempfile.TemporaryDirectory(prefix="rc-fix-") as td:
        root = Path(td) / "check"
        root.mkdir()
        (root / "recyclarr.yml").write_text("")  # zero-byte -> "regular empty file"
        (root / "config.toml").write_text("x" * 10)
        sub = root / "logs"
        sub.mkdir()
        (sub / "run.log").write_text("ok")
        # mode bits: owner-readable files + traversable dirs
        for p in [root, sub]:
            os.chmod(p, 0o755)
        for p in [root / "recyclarr.yml", root / "config.toml", sub / "run.log"]:
            os.chmod(p, 0o644)

        owners = build_owner_map(root, MOVER_UID, MOVER_GID)
        rc, out, err = run_measure(root, owners, measure_script=measure_script)
        v = parse_verdict(out)
        require(rc == 0, f"all-readable fixture exits 0 (rc={rc}, err={err!r})")
        require(v.get("FILES_TOTAL") == "3", f"FILES_TOTAL=3 got {v.get('FILES_TOTAL')}")
        require(
            v.get("FILES_READABLE") == "3",
            f"FILES_READABLE=3 (empty-file trap) got {v.get('FILES_READABLE')}",
        )
        require(v.get("FILES_UNREADABLE") == "0", "FILES_UNREADABLE=0")
        require(v.get("DIRS_TOTAL") == "2", f"DIRS_TOTAL=2 got {v.get('DIRS_TOTAL')}")
        require(v.get("DIRS_TRAVERSABLE") == "2", "DIRS_TRAVERSABLE=2")
        require(v.get("UNCLASSIFIED_TOTAL") == "0", "UNCLASSIFIED_TOTAL=0")
        require(v.get("WALK_ERRORS") == "0", "WALK_ERRORS=0")
        require(v.get("LOST_FOUND_PRESENT", "").startswith("no"), "no lost+found")


def extract_awk_program(measure_script: str) -> str:
    """Pull the awk classifier body out of measure.sh (between awk ... ' and ')."""
    start = measure_script.find("function oct2dec")
    if start < 0:
        raise AssertionError("awk classifier not found in measure.sh")
    # Walk back to the opening single quote that begins the awk program.
    open_q = measure_script.rfind("'", 0, start)
    # Closing is a newline + single quote before "$STATF" or $STATF.
    m = re.search(r"\n'\s+\"\$STATF\"", measure_script[start:])
    if not m:
        m = re.search(r"\n'\s+\$STATF", measure_script[start:])
    if open_q < 0 or not m:
        raise AssertionError("could not bound awk program in measure.sh")
    end_marker = start + m.start() + 1  # position of the closing '
    return measure_script[open_q + 1 : end_marker]


def run_awk_classifier(
    measure_script: str,
    stat_lines: list[str],
    mover_uid: str = MOVER_UID,
    mover_gid: str = MOVER_GID,
    walk_errors: int = 0,
) -> dict[str, str]:
    """Run the procedure's awk classifier on synthetic busybox stat lines.

    The live walker always mounts at /check, and the shipped awk hardcodes
    that path for lost+found detection. Feeding /check/... lines exercises
    that branch without needing root to create a real /check tree.
    """
    awk_prog = extract_awk_program(measure_script)
    with tempfile.TemporaryDirectory(prefix="rc-awk-") as td:
        statf = Path(td) / "statf"
        statf.write_text("".join(line if line.endswith("\n") else line + "\n" for line in stat_lines))
        proc = subprocess.run(
            [
                "awk",
                "-F|",
                "-v",
                f"muid={mover_uid}",
                "-v",
                f"mgid={mover_gid}",
                "-v",
                f"walk_errors={walk_errors}",
                awk_prog,
                str(statf),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"awk classifier failed rc={proc.returncode} err={proc.stderr!r} out={proc.stdout!r}"
            )
        return parse_verdict(proc.stdout)


def test_classifier_unreadable_and_lost_found(measure_script: str) -> None:
    """Root-owned 0600 file is unreadable; lost+found is excluded, not a finding.

    Two layers:
      1. Real filesystem fixture for the unreadable-file path (chmod + owner map).
      2. Synthetic /check/... stat lines for lost+found, matching the live mount
         path the procedure hardcodes in is_lost_found.
    """
    with tempfile.TemporaryDirectory(prefix="rc-fix-") as td:
        root = Path(td) / "check"
        root.mkdir()
        secret = root / "root-only.secret"
        secret.write_text("secret")
        os.chmod(secret, 0o600)
        open_file = root / "open.txt"
        open_file.write_text("hi")
        os.chmod(open_file, 0o644)
        os.chmod(root, 0o755)

        owners = build_owner_map(root, MOVER_UID, MOVER_GID)
        owners[str(secret)] = ("0", "0")

        rc, out, err = run_measure(root, owners, measure_script=measure_script)
        v = parse_verdict(out)
        require(rc == 0, f"mixed fixture exits 0 (walk still complete); rc={rc} err={err!r}")
        require(v.get("FILES_TOTAL") == "2", f"FILES_TOTAL=2 got {v}")
        require(v.get("FILES_READABLE") == "1", f"only open.txt readable, got {v}")
        require(v.get("FILES_UNREADABLE") == "1", f"root-only.secret unreadable, got {v}")

    # lost+found branch: paths must be under /check as in the live walker mount.
    v2 = run_awk_classifier(
        measure_script,
        [
            "directory|755|2000|2000|/check",
            "regular file|644|2000|2000|/check/open.txt",
            "regular file|600|0|0|/check/root-only.secret",
            "directory|700|0|0|/check/lost+found",
            "regular file|600|0|0|/check/lost+found/orphan",
        ],
    )
    require(
        v2.get("LOST_FOUND_PRESENT", "").startswith("yes"),
        f"lost+found present flagged, got {v2.get('LOST_FOUND_PRESENT')}",
    )
    require(
        v2.get("LOST_FOUND_ENTRIES") == "2",
        f"lost+found dir+file counted separately, got {v2.get('LOST_FOUND_ENTRIES')}",
    )
    require(
        v2.get("FILES_TOTAL") == "2",
        f"FILES_TOTAL excludes lost+found orphan, got {v2}",
    )
    require(
        v2.get("FILES_UNREADABLE") == "1",
        f"only root-only.secret counts as unreadable, got {v2}",
    )
    require(
        v2.get("DIRS_TOTAL") == "1",
        f"DIRS_TOTAL excludes lost+found dir, got {v2}",
    )


def test_classifier_group_and_other_bits(measure_script: str) -> None:
    """Group-read and other-read paths both count as readable for the mover."""
    with tempfile.TemporaryDirectory(prefix="rc-fix-") as td:
        root = Path(td) / "check"
        root.mkdir()
        os.chmod(root, 0o755)
        by_group = root / "group-read.bin"
        by_group.write_text("g")
        os.chmod(by_group, 0o640)  # owner rw, group r
        by_other = root / "other-read.bin"
        by_other.write_text("o")
        os.chmod(by_other, 0o604)  # owner rw, other r
        # Directory traversable via group exec for a non-owner mover.
        shared = root / "shared-dir"
        shared.mkdir()
        os.chmod(shared, 0o750)  # owner rwx, group rx

        owners = {
            str(root): ("0", "0"),
            str(by_group): ("0", MOVER_GID),  # group match
            str(by_other): ("1", "1"),  # only other-read
            str(shared): ("0", MOVER_GID),
        }
        rc, out, err = run_measure(root, owners, measure_script=measure_script)
        v = parse_verdict(out)
        require(rc == 0, f"group/other fixture rc=0 got {rc} {err!r}")
        require(v.get("FILES_READABLE") == "2", f"both files readable via g/o bits: {v}")
        require(v.get("FILES_UNREADABLE") == "0", f"no unreadable files: {v}")
        require(
            v.get("DIRS_TRAVERSABLE") == "2",
            f"root (other rx via 755) + shared (group rx) traversable: {v}",
        )


def test_classifier_dir_needs_exec(measure_script: str) -> None:
    """Directory with read but no execute is untraversable."""
    with tempfile.TemporaryDirectory(prefix="rc-fix-") as td:
        root = Path(td) / "check"
        root.mkdir()
        os.chmod(root, 0o755)
        stuck = root / "no-exec"
        stuck.mkdir()
        os.chmod(stuck, 0o644)  # read bits, no exec for anyone

        owners = build_owner_map(root, MOVER_UID, MOVER_GID)
        rc, out, _ = run_measure(root, owners, measure_script=measure_script)
        v = parse_verdict(out)
        require(rc == 0, "dir-no-exec fixture exits 0")
        require(v.get("DIRS_TOTAL") == "2", f"DIRS_TOTAL=2 got {v}")
        require(v.get("DIRS_TRAVERSABLE") == "1", f"only root traversable got {v}")
        require(v.get("DIRS_UNTRAVERSABLE") == "1", f"no-exec dir untraversable got {v}")


def test_classifier_symlink(measure_script: str) -> None:
    """Symlinks are counted, not mode-checked."""
    with tempfile.TemporaryDirectory(prefix="rc-fix-") as td:
        root = Path(td) / "check"
        root.mkdir()
        os.chmod(root, 0o755)
        target = root / "target.txt"
        target.write_text("t")
        os.chmod(target, 0o644)
        link = root / "alias"
        link.symlink_to("target.txt")

        owners = build_owner_map(root, MOVER_UID, MOVER_GID)
        rc, out, _ = run_measure(root, owners, measure_script=measure_script)
        v = parse_verdict(out)
        require(rc == 0, "symlink fixture exits 0")
        require(v.get("SYMLINKS_TOTAL") == "1", f"SYMLINKS_TOTAL=1 got {v}")
        require(v.get("FILES_TOTAL") == "1", f"only the real file counted as file: {v}")


def test_walk_errors_fail_closed(measure_script: str) -> None:
    """Non-zero walk errors must exit inconclusive (2), never a silent clean zero."""
    # Point ROOT at a path that cannot be enumerated.
    missing = Path("/no/such/recyclarr-readable-check-root-does-not-exist")
    rc, out, err = run_measure(
        missing,
        owner_map={},
        measure_script=measure_script,
    )
    v = parse_verdict(out)
    require(rc == 2, f"missing ROOT exits 2 (inconclusive), got rc={rc} err={err!r}")
    require(
        "INCONCLUSIVE" in err or "INCONCLUSIVE" in out,
        "walk-error path must print INCONCLUSIVE",
    )
    # Counts may be zero, but WALK_ERRORS must be non-zero so callers cannot
    # mistake this for a clean empty volume.
    we = v.get("WALK_ERRORS", "0")
    require(we != "0", f"WALK_ERRORS must be non-zero on failure, got {we!r} full={v}")


def test_requires_mover_identity(measure_script: str) -> None:
    """MOVER_UID/GID are mandatory - empty identity must not silently default."""
    with tempfile.TemporaryDirectory(prefix="rc-fix-") as td:
        root = Path(td) / "check"
        root.mkdir()
        script_path = Path(td) / "measure.sh"
        script_path.write_text(measure_script)
        script_path.chmod(0o755)
        env = os.environ.copy()
        env["ROOT"] = str(root)
        # deliberately omit MOVER_UID / MOVER_GID
        env.pop("MOVER_UID", None)
        env.pop("MOVER_GID", None)
        proc = subprocess.run(
            ["sh", str(script_path)],
            capture_output=True,
            text=True,
            env=env,
        )
        require(
            proc.returncode != 0,
            f"missing MOVER_UID must fail closed, rc={proc.returncode}",
        )


def _load_overlay_substitute() -> dict[str, Any]:
    docs = list(yaml.safe_load_all(OVERLAY.read_text()))
    # recyclarr.yaml is one or more Flux Kustomizations; find the one with KOPIUR_CLAIM
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        sub = (doc.get("spec") or {}).get("postBuild", {}).get("substitute") or {}
        if sub.get("KOPIUR_CLAIM") == "recyclarr-config" or "KOPIUR_PUID" in sub:
            return sub
        # Some overlays put substitute on the kopiur half only - accept either.
    # Fallback: merge all substitute maps
    merged: dict[str, Any] = {}
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        sub = (doc.get("spec") or {}).get("postBuild", {}).get("substitute") or {}
        merged.update(sub)
    return merged


def test_overlay_identity_and_doc_pointer() -> None:
    """GitOps overlay keeps mover 2000:2000 and points at the procedure doc."""
    require(OVERLAY.is_file(), "recyclarr overlay exists")
    sub = _load_overlay_substitute()
    require(
        sub.get("KOPIUR_CLAIM") == "recyclarr-config",
        f"KOPIUR_CLAIM=recyclarr-config got {sub.get('KOPIUR_CLAIM')}",
    )
    require(
        str(sub.get("KOPIUR_PUID")) == MOVER_UID,
        f"KOPIUR_PUID={MOVER_UID} got {sub.get('KOPIUR_PUID')}",
    )
    require(
        str(sub.get("KOPIUR_PGID")) == MOVER_GID,
        f"KOPIUR_PGID={MOVER_GID} got {sub.get('KOPIUR_PGID')}",
    )
    # Comment contract: overlay names the procedure doc (operator breadcrumb).
    raw = OVERLAY.read_text()
    require(
        "recyclarr-config-readable-check-2026-08-31.md" in raw,
        "overlay comments must point at the readable-check procedure doc",
    )
    require(
        "2913/2913" in raw,
        "overlay comments must record the live file readability verdict",
    )


def test_workload_declares_2000() -> None:
    """Declared CronJob securityContext is the source of the 2000:2000 pair."""
    require(HELMRELEASE.is_file(), "recyclarr helmrelease exists")
    # app-template values: look for runAsUser: 2000 in rendered-ish values
    text = HELMRELEASE.read_text()
    # Parse as multi-doc yaml and walk for securityContext numbers.
    found_user = False
    found_group = False
    for doc in yaml.safe_load_all(text):
        if not isinstance(doc, dict):
            continue
        blob = yaml.dump(doc)

        def walk(obj: Any) -> None:
            nonlocal found_user, found_group
            if isinstance(obj, dict):
                if obj.get("runAsUser") == 2000:
                    found_user = True
                if obj.get("runAsGroup") == 2000 or obj.get("fsGroup") == 2000:
                    found_group = True
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    walk(v)

        walk(doc)
    require(found_user, "helmrelease declares runAsUser: 2000")
    require(found_group, "helmrelease declares runAsGroup/fsGroup: 2000")


def test_procedure_document_contract() -> None:
    """The procedure doc is the public result artifact - pin its acceptance gate."""
    require(DOC.is_file(), f"missing procedure doc {DOC.relative_to(ROOT)}")
    text = DOC.read_text()
    lowered = text.lower()

    # Subject + verdict.
    require("recyclarr-config" in lowered, "doc subject is recyclarr-config")
    require(
        re.search(r"fully readable|is fully readable", lowered),
        "doc declares fully-readable verdict",
    )
    require(
        re.search(rf"\b{LIVE_FILES}\s*/\s*{LIVE_FILES}\b", text)
        or f"FILES_TOTAL={LIVE_FILES}" in text,
        f"doc records {LIVE_FILES}/{LIVE_FILES} files",
    )
    require(
        re.search(rf"\b{LIVE_DIRS}\s*/\s*{LIVE_DIRS}\b", text)
        or f"DIRS_TOTAL={LIVE_DIRS}" in text,
        f"doc records {LIVE_DIRS}/{LIVE_DIRS} directories",
    )
    require(
        "FILES_UNREADABLE=0" in text or re.search(r"files_unreadable=0", lowered),
        "doc records zero unreadable files",
    )
    require(
        "WALK_ERRORS=0" in text or re.search(r"walk errors\s*\|\s*0", lowered),
        "doc records zero walk errors",
    )
    require(
        re.search(r"mover.*2000|uid/gid 2000|identity \(uid/gid 2000\)", lowered),
        "doc names mover identity 2000",
    )

    # Scope: readability only, not full Stage 5 restore-fidelity.
    require(
        re.search(r"stage\s*5", lowered)
        and re.search(r"readability", lowered)
        and (
            "not" in lowered
            and (
                "restore-fidelity" in lowered
                or "restore fidelity" in lowered
                or "per-volume restore" in lowered
            )
        ),
        "doc must scope itself as readability, not full Stage 5 restore proof",
    )

    # Approach selection: three candidates, (c) chosen, (a)/(b) rejected.
    require(
        re.search(r"cronjob'?s? own run|during the cronjob", lowered),
        "doc weighs candidate (a): measure during CronJob run",
    )
    require(
        re.search(r"short-lived pod|mount the claim read-only", lowered),
        "doc weighs candidate (b): short-lived pod on live claim",
    )
    require(
        re.search(r"restored copy|volumesnapshot|measure a restored", lowered),
        "doc weighs/chooses candidate (c): restored copy / VolumeSnapshot",
    )
    require(
        re.search(r"readwriteonce|accessmodes", lowered.replace(" ", "")),
        "doc verifies RWO access mode before rejecting live mount",
    )
    require(
        re.search(r"rejected", lowered),
        "doc states rejected alternatives",
    )

    # Four false-clean traps called out.
    require(
        "-uid" in text or "no -uid" in lowered or "has no `-uid`" in lowered or "has no -uid" in lowered,
        "doc/script avoid busybox find -uid/-gid trap",
    )
    require(
        "regular empty file" in lowered,
        "doc/script handle busybox zero-byte 'regular empty file' trap",
    )
    require(
        "lost+found" in lowered,
        "doc counts lost+found separately",
    )
    require(
        "walk_errors" in lowered or "walk errors" in lowered,
        "doc counts walk errors explicitly (never suppress stderr)",
    )
    require(
        "/tmp" in lowered and ("read-only" in lowered or "readonly" in lowered.replace("-", "")),
        "doc warns about /tmp on hardened read-only rootfs containers",
    )

    # Mover identity from live SnapshotPolicy, not component defaults.
    require(
        "snapshotpolicy" in lowered and "podsecuritycontext" in lowered.replace(" ", ""),
        "doc resolves mover identity from live SnapshotPolicy.spec.mover.podSecurityContext",
    )

    # Job-history trap: successfulJobsHistoryLimit 0.
    require(
        "successfuljobshistorylimit" in lowered.replace(" ", "")
        or "successfulJobsHistoryLimit" in text,
        "doc warns successfulJobsHistoryLimit:0 makes Job history untrustworthy",
    )
    require(
        "lastsuccessfultime" in lowered.replace(" ", "")
        or "lastSuccessfulTime" in text,
        "doc uses lastSuccessfulTime, not Job objects",
    )

    # Read-only against the claim; scratch cleanup; CronJob untouched.
    require(
        re.search(r"read[\s-]*only", lowered) and "live" in lowered,
        "doc keeps the live claim read-only / untouched",
    )
    require(
        "delete pod" in lowered or "cleanup" in lowered,
        "doc cleans up scratch objects",
    )

    # Cross-check against kopiur production snapshot stats.
    require(
        "filesNew" in text or "filesnew" in lowered,
        "doc cross-checks against kopiur snapshot stats filesNew",
    )
    require(
        str(LIVE_FILES) in text and ("filesNew" in text or "filesnew" in lowered),
        "doc ties filesNew to the live file count",
    )

    # Procedure embeds the runnable measure script.
    require("#!/bin/sh" in text, "doc embeds measure.sh")
    require("MOVER_UID" in text and "MOVER_GID" in text, "script requires mover identity")

    # Must NOT claim to have built the fleet-wide CronJob.
    require(
        "mover-readable-check" in lowered
        and (
            "open" in lowered
            or "captain decision" in lowered
            or "not addressed" in lowered
            or "separate" in lowered
        ),
        "doc leaves fleet-wide pvc-mover-readable-check as open captain decision",
    )


def test_component_readme_pointer() -> None:
    """SecurityContextCompatible table + Stage-4 summary point at the proof."""
    require(COMPONENT_README.is_file(), "kopiur Readme exists")
    text = COMPONENT_README.read_text()
    require(
        "recyclarr-config-readable-check-2026-08-31.md" in text,
        "Readme links the readable-check procedure doc",
    )
    require(
        "2913/2913" in text,
        "Readme records the 2913/2913 file verdict",
    )
    require(
        re.search(
            r"recyclarr-config.*2000:2000|2000:2000.*recyclarr-config",
            text,
            re.S,
        )
        or ("recyclarr-config" in text and "2000:2000" in text),
        "Readme names recyclarr-config mover identity 2000:2000 in the proof row",
    )
    # Still flags that full Stage 5 restore-fidelity remains outstanding.
    lowered = text.lower()
    require(
        "recyclarr" in lowered
        and (
            "restore-fidelity" in lowered
            or "stage 5" in lowered
            or "restore proof" in lowered
        ),
        "Readme keeps Stage 5 restore-fidelity as still required for recyclarr",
    )


def test_empty_file_trap_regression(measure_script: str) -> None:
    """Exact-match on 'regular file' would drop zero-byte files into unclassified.

    Reproduce the trap the procedure caught live (recyclarr.yml + .verbose.log)
    and prove the shipped classifier keeps them in FILES_*.
    """
    with tempfile.TemporaryDirectory(prefix="rc-fix-") as td:
        root = Path(td) / "check"
        root.mkdir()
        os.chmod(root, 0o755)
        empty = root / "recyclarr.yml"
        empty.write_text("")
        os.chmod(empty, 0o644)
        empty_log = root / ".verbose.log"
        empty_log.write_text("")
        os.chmod(empty_log, 0o644)
        owners = build_owner_map(root, MOVER_UID, MOVER_GID)
        rc, out, _ = run_measure(root, owners, measure_script=measure_script)
        v = parse_verdict(out)
        require(rc == 0, "empty-file trap fixture exits 0")
        require(
            v.get("FILES_TOTAL") == "2" and v.get("FILES_READABLE") == "2",
            f"both zero-byte files classified readable, not unclassified: {v}",
        )
        require(
            v.get("UNCLASSIFIED_TOTAL") == "0",
            f"zero-byte files must not fall into unclassified: {v}",
        )


def main() -> int:
    require(DOC.is_file(), f"procedure doc present at {DOC.relative_to(ROOT)}")
    doc_text = DOC.read_text()
    try:
        measure_script = extract_measure_script(doc_text)
    except AssertionError as e:
        require(False, f"extract measure.sh: {e}")
        print(f"Summary: {passed} passed, {failed} failed")
        return 1

    require(
        "can_read" in measure_script or "bit(mode" in measure_script,
        "extracted script carries mode-bit readability classifier",
    )
    require(
        "lost+found" in measure_script or "lost_found" in measure_script,
        "extracted script special-cases lost+found",
    )
    require(
        'substr(type, 1, 7) == "regular"' in measure_script
        or "regular empty" in measure_script,
        "extracted script prefix-matches regular files (empty-file trap)",
    )

    test_classifier_all_readable(measure_script)
    test_classifier_unreadable_and_lost_found(measure_script)
    test_classifier_group_and_other_bits(measure_script)
    test_classifier_dir_needs_exec(measure_script)
    test_classifier_symlink(measure_script)
    test_empty_file_trap_regression(measure_script)
    test_walk_errors_fail_closed(measure_script)
    test_requires_mover_identity(measure_script)
    test_procedure_document_contract()
    test_overlay_identity_and_doc_pointer()
    test_workload_declares_2000()
    test_component_readme_pointer()

    print(f"Summary: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
