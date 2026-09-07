#!/usr/bin/env python3
"""Behavioral regression test for system/fstrim PVC discard path (2026-09-06).

Background: the inherited node-fstrim recipe filtered with `grep -v kubelet`,
which excluded 100% of Ceph PV mounts (every one lives under /var/lib/kubelet)
and left the cluster with no working reclaim path. downloads/sabnzbd-incomplete
grew to ~949 GiB of freed-but-never-discarded blocks behind a 36 KB directory.

This test does not treat source greps as proof. It:

  1. Extracts the real awk selector and the two-step discovery shell from the
     shipped HelmRelease and runs them against fixture mountinfo shaped like the
     live kubelet-namespace captures (CSI globalmount + pod bind of the same
     major:minor, Talos EPHEMERAL as a non-root bind, read-only /var/mnt, a
     non-/dev/ source, and a regular-file-shaped entry).
  2. Asserts observable selection: each block device appears exactly once, PVC
     globalmounts win over pod binds, the node xfs bind is kept, RO and non-dev
     sources are dropped.
  3. Runs the pre-fix filter (`grep -v kubelet`) on the same fixture and
     asserts it yields zero PVC targets - the motivating failure mode must fail
     before the fix and pass after.
  4. Exercises the discovery pipeline split under `set -eu`: a failing awk must
     abort before `sort` can invent an empty success list (the pipefail-class
     defect fixed by writing /tmp/fstrim-targets.raw then sorting).
  5. Parses the HelmRelease object model for schedule / Forbid / backoffLimit 0
     / fail-closed trim loop, and the rook-ceph cluster HelmRelease for the nfs
     mgr module being off and mountOptions:[discard] deliberately absent.

Live cluster proof (one-off Jobs on talos-1/2/3 selecting 49 RBD + 1 xfs each
once, scoped trims on radarr-config/tempo-0/loki-0, sabnzbd left for Monday
schedule, crash prune 2447->0) lives in docs/ceph-cluster-changelog.md and is
out of scope for this offline gate.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
FSTRIM_HR = ROOT / "kubernetes/apps/base/system/fstrim/app/helmrelease.yaml"
ROOK_HR = (
    ROOT / "kubernetes/apps/base/rook-ceph/rook-ceph/cluster/helmrelease.yaml"
)
FSTRIM_OVERLAY = ROOT / "kubernetes/apps/main/system/fstrim.yaml"
SYSTEM_MAIN = ROOT / "kubernetes/apps/main/system/kustomization.yaml"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        doc = yaml.safe_load(f)
    require(isinstance(doc, dict), f"{path}: expected a mapping document")
    return doc


def fstrim_values(hr: dict[str, Any]) -> dict[str, Any]:
    values = hr.get("spec", {}).get("values") or {}
    require(isinstance(values, dict), "fstrim HelmRelease values must be a map")
    return values


def extract_script(hr: dict[str, Any]) -> str:
    containers = (
        (hr.get("spec") or {})
        .get("values", {})
        .get("controllers", {})
        .get("fstrim", {})
        .get("containers", {})
        .get("app", {})
    )
    command = containers.get("command") or []
    args = containers.get("args") or []
    require(
        command == ["/bin/sh", "-c"] and len(args) == 1 and isinstance(args[0], str),
        f"unexpected fstrim command/args shape: command={command!r} args={args!r}",
    )
    script = args[0]
    require("fstrim -v" in script, "script must invoke fstrim -v")
    require("/proc/self/mountinfo" in script, "script must read mountinfo")
    return script


def extract_awk_program(script: str) -> str:
    """Pull the single-quoted awk body that selects trim targets."""
    marker = "nsenter $NS -- awk '"
    start = script.find(marker)
    require(start >= 0, "script must invoke nsenter … awk '…'")
    body_start = start + len(marker)
    # The awk program ends at the closing quote before ` /proc/self/mountinfo`.
    end_token = "' /proc/self/mountinfo"
    end = script.find(end_token, body_start)
    require(end > body_start, "could not locate end of awk program")
    program = script[body_start:end]
    require("$3 in keep" in program or "keep[$3]" in program, "awk must de-dupe on major:minor")
    require("globalmount" in program, "awk must prefer CSI globalmount paths")
    return program


# ---------------------------------------------------------------------------
# Fixture mountinfo
#
# Shaped after the live kubelet-namespace captures described in the 2026-09-06
# changelog entry: multiple bind mounts share a major:minor, CSI staging paths
# contain /globalmount/, the Talos EPHEMERAL xfs only appears as a non-"/" root
# bind inside the kubelet namespace, and /var/mnt is read-only.
# Field layout (util-linux mountinfo):
#   id parent major:minor root mountpoint opts [optfields] - fstype source super
# ---------------------------------------------------------------------------

FIXTURE_MOUNTINFO = textwrap.dedent(
    """\
    100 1 259:2 / /var/mnt ro,relatime shared:1 - xfs /dev/nvme0n1p2 ro
    101 1 0:47 / /run rw,nosuid,nodev shared:2 - tmpfs tmpfs rw
    102 1 0:48 / /etc/nfsmount.conf rw,relatime shared:3 - tmpfs tmpfs rw
    200 1 259:3 /var /var rw,relatime shared:10 - xfs /dev/nvme0n1p3 rw
    201 200 259:3 /var/log /var/log rw,relatime shared:11 - xfs /dev/nvme0n1p3 rw
    300 1 8:16 / /var/lib/kubelet/plugins/kubernetes.io/csi/pv/pvc-sab-incomplete/globalmount rw,relatime shared:20 - ext4 /dev/rbd0 rw
    301 1 8:16 / /var/lib/kubelet/pods/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/volumes/kubernetes.io~csi/pvc-sab-incomplete/mount rw,relatime shared:21 - ext4 /dev/rbd0 rw
    302 1 8:16 /incomplete /var/lib/kubelet/pods/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/volume-subpaths/pvc-sab-incomplete/app/0 rw,relatime shared:22 - ext4 /dev/rbd0 rw
    310 1 8:32 / /var/lib/kubelet/plugins/kubernetes.io/csi/pv/pvc-radarr-config/globalmount rw,relatime shared:30 - ext4 /dev/rbd1 rw
    311 1 8:32 / /var/lib/kubelet/pods/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb/volumes/kubernetes.io~csi/pvc-radarr-config/mount rw,relatime shared:31 - ext4 /dev/rbd1 rw
    320 1 8:48 / /var/lib/kubelet/plugins/kubernetes.io/csi/pv/pvc-loki-0/globalmount rw,relatime shared:40 - ext4 /dev/rbd2 rw
    321 1 8:48 / /var/lib/kubelet/pods/cccccccc-cccc-cccc-cccc-cccccccccccc/volumes/kubernetes.io~csi/pvc-loki-0/mount rw,relatime shared:41 - ext4 /dev/rbd2 rw
    330 1 8:64 / /var/lib/kubelet/plugins/kubernetes.io/csi/pv/pvc-tempo-0/globalmount rw,relatime shared:50 - ext4 /dev/rbd3 rw
    331 1 8:64 / /var/lib/kubelet/pods/dddddddd-dddd-dddd-dddd-dddddddddddd/volumes/kubernetes.io~csi/pvc-tempo-0/mount rw,relatime shared:51 - ext4 /dev/rbd3 rw
    400 1 253:0 / / rw,relatime shared:60 - xfs /dev/sda1 rw
    """
)

# Expected: one path per major:minor that is a trim candidate.
EXPECTED_TARGETS = {
    # EPHEMERAL xfs: only non-root binds exist; keep the one closest to fs root.
    "/var",
    # Four CSI globalmounts win over their pod binds / subpaths.
    "/var/lib/kubelet/plugins/kubernetes.io/csi/pv/pvc-sab-incomplete/globalmount",
    "/var/lib/kubelet/plugins/kubernetes.io/csi/pv/pvc-radarr-config/globalmount",
    "/var/lib/kubelet/plugins/kubernetes.io/csi/pv/pvc-loki-0/globalmount",
    "/var/lib/kubelet/plugins/kubernetes.io/csi/pv/pvc-tempo-0/globalmount",
    # Whole-fs root mount of /dev/sda1 (rank 1).
    "/",
}


def run_awk_selector(program: str, mountinfo: str) -> list[str]:
    """Execute the shipped awk program against fixture mountinfo; return paths."""
    with tempfile.TemporaryDirectory(prefix="fstrim-awk-") as td:
        mi = Path(td) / "mountinfo"
        mi.write_text(mountinfo)
        # gawk and busybox awk both accept the program; prefer the system awk.
        result = subprocess.run(
            ["awk", program, str(mi)],
            check=True,
            capture_output=True,
            text=True,
        )
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        return sorted(lines)


def run_old_kubelet_filter(mountinfo: str) -> list[str]:
    """Reproduce the pre-fix filter: drop any line containing 'kubelet'.

    Upstream node-fstrim then walked the surviving mountpoints. On this cluster
    that left zero PVCs because every Ceph mount path contains 'kubelet'.
    """
    survivors: list[str] = []
    for raw in mountinfo.splitlines():
        if not raw.strip() or "kubelet" in raw:
            continue
        # mountinfo mountpoint is field 5.
        parts = raw.split()
        if len(parts) < 5:
            continue
        # Only count /dev/ block sources the way a real trim walk would.
        if " - " not in raw:
            continue
        after = raw.split(" - ", 1)[1].split()
        if len(after) < 2 or not after[1].startswith("/dev/"):
            continue
        opts = parts[5] if len(parts) > 5 else ""
        if re.search(r"(^|,)ro(,|$)", opts):
            continue
        survivors.append(parts[4])
    return sorted(set(survivors))


def run_discovery_pipeline(program: str, mountinfo_path: Path, *, fail_awk: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the two-step discovery under set -eu the way the Job does.

    When fail_awk is True, point awk at a missing file so the first step must
    abort and never leave sort free to invent an empty success list.
    """
    with tempfile.TemporaryDirectory(prefix="fstrim-pipe-") as td:
        td_path = Path(td)
        targets_raw = td_path / "fstrim-targets.raw"
        targets = td_path / "fstrim-targets"
        awk_input = "/no/such/mountinfo" if fail_awk else str(mountinfo_path)
        script = textwrap.dedent(
            f"""\
            set -eu
            awk '{program}' {awk_input} > {targets_raw}
            sort {targets_raw} > {targets}
            echo "trimming $(wc -l < {targets}) device(s)"
            cat {targets}
            """
        )
        return subprocess.run(
            ["/bin/sh", "-c", script],
            capture_output=True,
            text=True,
        )


def test_awk_selects_each_block_device_once() -> None:
    hr = load_yaml(FSTRIM_HR)
    script = extract_script(hr)
    program = extract_awk_program(script)
    selected = run_awk_selector(program, FIXTURE_MOUNTINFO)
    require(
        selected == sorted(EXPECTED_TARGETS),
        f"selector mismatch:\n  got:      {selected}\n  expected: {sorted(EXPECTED_TARGETS)}",
    )
    # Explicit de-dupe: four rbd devices, never their pod-bind twins.
    rbd_hits = [p for p in selected if "pvc-" in p]
    require(len(rbd_hits) == 4, f"expected 4 PVC targets, got {rbd_hits}")
    require(
        all("/globalmount" in p for p in rbd_hits),
        f"PVC targets must be globalmount paths, got {rbd_hits}",
    )
    require(
        not any("/pods/" in p for p in selected),
        f"pod bind mounts must lose the rank contest, got {selected}",
    )
    require(
        "/var/mnt" not in selected,
        "read-only /var/mnt must be skipped (FITRIM rejects ro mounts)",
    )
    require(
        "/run" not in selected and "/etc/nfsmount.conf" not in selected,
        "non-/dev/ sources must be skipped",
    )
    require("/var" in selected, "Talos EPHEMERAL xfs bind (/var) must still be kept")
    print("[PASS] awk selects each block device once (PVC globalmount + node xfs)")


def test_old_filter_selects_zero_pvcs() -> None:
    old = run_old_kubelet_filter(FIXTURE_MOUNTINFO)
    pvc_old = [p for p in old if "kubelet" in p or "pvc-" in p or "rbd" in p]
    require(
        pvc_old == [],
        f"pre-fix grep -v kubelet must yield zero PVC targets, got {old}",
    )
    # Sanity: the old filter still sees non-kubelet block mounts (node fs).
    require(
        any(p in old for p in ("/var", "/", "/var/log")),
        f"old filter should still see node filesystems, got {old}",
    )
    print("[PASS] pre-fix grep -v kubelet yields zero PVC targets on same fixture")


def test_discovery_pipeline_split_fails_closed() -> None:
    hr = load_yaml(FSTRIM_HR)
    script = extract_script(hr)
    program = extract_awk_program(script)
    require(
        "fstrim-targets.raw" in script,
        "discovery must write an intermediate .raw file (pipeline split)",
    )
    # The sort step must be a separate command, not `awk … | sort`.
    require(
        re.search(r"sort\s+/tmp/fstrim-targets\.raw\s*>\s*/tmp/fstrim-targets", script),
        "sort must read the .raw file, not an awk|sort pipeline",
    )
    require(
        "| sort" not in script,
        "discovery must not use an awk|sort pipeline (busybox ash has no pipefail)",
    )

    with tempfile.TemporaryDirectory(prefix="fstrim-mi-") as td:
        mi = Path(td) / "mountinfo"
        mi.write_text(FIXTURE_MOUNTINFO)

        ok = run_discovery_pipeline(program, mi, fail_awk=False)
        require(ok.returncode == 0, f"happy-path discovery failed: {ok.stderr}")
        lines = [ln for ln in ok.stdout.splitlines() if ln.startswith("/")]
        require(
            sorted(lines) == sorted(EXPECTED_TARGETS),
            f"pipeline output mismatch: {lines}",
        )
        require(
            re.search(r"trimming\s+6\s+device", ok.stdout),
            f"expected 'trimming 6 device(s)', got: {ok.stdout!r}",
        )

        bad = run_discovery_pipeline(program, mi, fail_awk=True)
        require(
            bad.returncode != 0,
            "awk failure must fail the discovery script under set -e "
            f"(got rc=0, stdout={bad.stdout!r})",
        )
        require(
            "trimming 0 device" not in bad.stdout,
            "awk failure must not report a successful empty target list",
        )
    print("[PASS] discovery pipeline split fails closed on awk error; happy path lists 6")


def test_cronjob_safety_knobs() -> None:
    hr = load_yaml(FSTRIM_HR)
    values = fstrim_values(hr)
    cron = (
        values.get("controllers", {})
        .get("fstrim", {})
        .get("cronjob", {})
    )
    require(cron.get("schedule") == "0 0 * * 1", "must stay on Monday 00:00 schedule")
    require(cron.get("concurrencyPolicy") == "Forbid", "first pass can outlast its window")
    require(
        cron.get("backoffLimit") == 0,
        f"backoffLimit must be 0 (no multi-retry of a ~949 GiB walk), got {cron.get('backoffLimit')!r}",
    )
    require(cron.get("parallelism") == 3, "parallelism must match the 3-node fleet")

    script = extract_script(hr)
    require("rc=0" in script and 'exit "$rc"' in script, "trim loop must fail closed")
    require(
        "|| rc=1" in script or "|| rc=1" in script.replace(" ", ""),
        "each fstrim failure must flip rc",
    )
    # Privileged hostPID nsenter shape the live probe depended on.
    pod = values.get("controllers", {}).get("fstrim", {}).get("pod", {})
    require(pod.get("hostPID") is True, "hostPID required to nsenter kubelet mount ns")
    container = values.get("controllers", {}).get("fstrim", {}).get("containers", {}).get("app", {})
    require(
        (container.get("securityContext") or {}).get("privileged") is True,
        "privileged required for nsenter/fstrim against host mounts",
    )
    print("[PASS] cronjob safety knobs: Monday schedule, Forbid, backoffLimit 0, fail-closed")


def test_nfs_module_disabled_and_discard_left_out() -> None:
    hr = load_yaml(ROOK_HR)
    values = hr.get("spec", {}).get("values") or {}
    modules = (
        (values.get("cephClusterSpec") or {})
        .get("mgr", {})
        .get("modules")
        or []
    )
    require(isinstance(modules, list) and modules, "cephClusterSpec.mgr.modules missing")
    by_name = {m.get("name"): m for m in modules if isinstance(m, dict)}
    require("nfs" in by_name, "nfs module entry must still be declared (explicitly off)")
    require(
        by_name["nfs"].get("enabled") is False,
        f"nfs module must be enabled: false, got {by_name['nfs']!r}",
    )
    # Orchestrator/rook backend stays off - the reason nfs crashed.
    require(
        by_name.get("rook", {}).get("enabled") is False,
        "rook orchestrator backend must stay disabled",
    )

    # mountOptions: [discard] was evaluated and deliberately left out of every
    # ceph-block StorageClass values block.
    blob = yaml.dump(values)
    require(
        "mountOptions" not in blob
        or "discard" not in blob,
        "mountOptions:[discard] must stay out of this change (deliberate non-apply)",
    )
    block_pools = values.get("cephBlockPools") or []
    for pool in block_pools:
        sc = (pool or {}).get("storageClass") or {}
        opts = sc.get("mountOptions")
        require(
            not opts,
            f"cephBlockPool {pool.get('name')!r} must not set mountOptions, got {opts!r}",
        )
    print("[PASS] nfs mgr module enabled:false; mountOptions discard left out")


def test_overlay_wired() -> None:
    require(FSTRIM_OVERLAY.is_file(), f"missing overlay {FSTRIM_OVERLAY}")
    main = load_yaml(SYSTEM_MAIN)
    resources = main.get("resources") or []
    require(
        "./fstrim.yaml" in resources,
        f"system main kustomization must list ./fstrim.yaml, got {resources}",
    )
    print("[PASS] fstrim overlay wired into system main kustomization")


def main() -> int:
    tests = [
        test_awk_selects_each_block_device_once,
        test_old_filter_selects_zero_pvcs,
        test_discovery_pipeline_split_fails_closed,
        test_cronjob_safety_knobs,
        test_nfs_module_disabled_and_discard_left_out,
        test_overlay_wired,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Failure as exc:
            failed += 1
            print(f"[FAIL] {test.__name__}: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors per test
            failed += 1
            print(f"[FAIL] {test.__name__}: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
    total = len(tests)
    passed = total - failed
    print(f"\n{passed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
