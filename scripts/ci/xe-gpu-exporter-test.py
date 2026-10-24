#!/usr/bin/env python3
"""Behavioral contract: system/xe-gpu-exporter turns DRM fdinfo into VRAM series.

The `xe` driver publishes no VRAM used/total counters in sysfs, and the cluster
has no xpu-smi or Level Zero exporter (skill intel-gpu, references/telemetry.md).
The only live source is `/proc/<pid>/fdinfo/<fd>` of a process holding the DRM
node: `drm-total-vram0` is every buffer object that client has resident in VRAM,
weights, KV cache and compute buffers alike.

What this pins
  - fdinfo sizes are read as bytes whatever unit the kernel prints (KiB, MiB, none)
  - a client that shows up on two fds, or two PIDs, is counted once
    ((pdev, drm-client-id) is the identity), so a dup'd fd cannot double VRAM
  - non-xe fdinfo and fds that are not DRM nodes are ignored
  - the kubelet pod UID is recovered from both cgroup layouts Talos can show
    (cgroupfs `.../pod<uid>/...` and systemd `...-pod<uid_with_underscores>.slice`)
  - a PID that exits mid-scan is skipped, not an exception that fails the scrape
  - totals come only from the configured map, never guessed from a BAR size,
    because the B70 BAR is 256 MiB until Resizable BAR is on

What this does not catch
  - whether the container really has hostPID and CAP_SYS_PTRACE. Without them
    the scan sees nothing and the exporter reports zero clients while up.
    The live check in the PR is the guard for that.
"""
import importlib.util
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPORTER = ROOT / "kubernetes/apps/base/system/xe-gpu-exporter/app/resources/exporter.py"

POD_UID = "9f1c2d3e-4a5b-6c7d-8e9f-0a1b2c3d4e5f"
OTHER_UID = "11111111-2222-3333-4444-555555555555"
B70 = "0000:03:00.0"
IGPU = "0000:00:02.0"

FDINFO_B70 = f"""pos:\t0
flags:\t02000002
drm-driver:\txe
drm-client-id:\t9
drm-pdev:\t{B70}
drm-total-gtt:\t1488904 KiB
drm-resident-gtt:\t1488904 KiB
drm-total-vram0:\t20387560 KiB
drm-resident-vram0:\t20387560 KiB
"""

FDINFO_SECOND_CLIENT = FDINFO_B70.replace("client-id:\t9", "client-id:\t71").replace(
    "20387560 KiB", "4636 KiB"
)
FDINFO_EMBEDDING = FDINFO_B70.replace("client-id:\t9", "client-id:\t8").replace(
    "20387560 KiB", "1673760 KiB"
)
FDINFO_I915 = f"drm-driver:\ti915\ndrm-client-id:\t3\ndrm-pdev:\t{IGPU}\n"
FDINFO_NOT_DRM_NODE = f"drm-driver:\txe\ndrm-client-id:\t1\ndrm-pdev:\t{B70}\ndrm-total-vram0:\t999 KiB\n"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        failures.append(f"{name}: {detail}" if detail else name)


def load_exporter():
    spec = importlib.util.spec_from_file_location("xe_gpu_exporter", EXPORTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def fake_proc(root: pathlib.Path, pid: int, comm: str, cgroup: str, fds: dict) -> None:
    """fds maps fd number -> (readlink target, fdinfo text)."""
    base = root / str(pid)
    write(base / "comm", comm + "\n")
    write(base / "cgroup", cgroup)
    (base / "fd").mkdir(parents=True, exist_ok=True)
    for fd, (target, info) in fds.items():
        (base / "fd" / str(fd)).symlink_to(target)
        write(base / "fdinfo" / str(fd), info)


def build_fake_proc(proc: pathlib.Path) -> None:
    cgroupfs = f"0::/kubepods/burstable/pod{POD_UID}/abc123\n"
    # Same client twice (dup) plus a second client in one process.
    fake_proc(
        proc, 100, "llama-server", cgroupfs,
        {
            3: ("/dev/dri/renderD128", FDINFO_B70),
            4: ("/dev/dri/renderD128", FDINFO_B70),
            8: ("/dev/dri/renderD128", FDINFO_SECOND_CLIENT),
            9: ("/dev/null", FDINFO_NOT_DRM_NODE),
        },
    )
    # Same client inherited by a child PID: still one client.
    fake_proc(proc, 101, "llama-server", cgroupfs, {3: ("/dev/dri/renderD128", FDINFO_B70)})
    # Another pod, another client on the same card.
    fake_proc(
        proc, 200, "embedding", f"0::/kubepods/burstable/pod{OTHER_UID}/def\n",
        {3: ("/dev/dri/renderD129", FDINFO_EMBEDDING)},
    )
    # iGPU client must not pollute the B70 totals.
    fake_proc(proc, 300, "plex", cgroupfs, {3: ("/dev/dri/renderD128", FDINFO_I915)})
    # A PID with no fd dir at all (exited mid-scan).
    write(proc / "400" / "comm", "gone\n")


def check_parsing(exporter) -> None:
    check("KiB scales", exporter.size_to_bytes("20387560 KiB") == 20387560 * 1024)
    check("MiB scales", exporter.size_to_bytes("2 MiB") == 2 * 1024 * 1024)
    check("bare number is bytes", exporter.size_to_bytes("512") == 512)
    check("garbage is zero", exporter.size_to_bytes("lots") == 0)

    info = exporter.parse_fdinfo(FDINFO_B70)
    check("driver parsed", info.get("drm-driver") == "xe")
    check("pdev parsed", info.get("drm-pdev") == B70)

    layouts = {
        "cgroupfs": f"0::/kubepods/burstable/pod{POD_UID}/abc123\n",
        "systemd": (
            "0::/kubepods.slice/kubepods-burstable.slice/"
            f"kubepods-burstable-pod{POD_UID.replace('-', '_')}.slice/cri-containerd-abc.scope\n"
        ),
        "guaranteed": f"0::/kubepods/pod{POD_UID}/abc123\n",
    }
    for label, text in layouts.items():
        check(f"pod uid from {label}", exporter.pod_uid_from_cgroup(text) == POD_UID, label)
    check("host process has no pod uid", exporter.pod_uid_from_cgroup("0::/system.slice/foo.service\n") == "")

    parsed = exporter.parse_vram_totals(f"{B70}=32656, 0000:04:00.0=1024")
    check("totals env parsed to bytes", parsed.get(B70) == 32656 * 1024 * 1024, str(parsed))
    check("empty totals env", exporter.parse_vram_totals("") == {})
    check("malformed totals ignored", exporter.parse_vram_totals("nonsense,x=y") == {})


def check_scan(exporter) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        proc = pathlib.Path(tmp) / "proc"
        build_fake_proc(proc)
        scan = exporter.scan(str(proc))

    b70 = scan.get(B70)
    check("b70 present", b70 is not None, str(scan))
    if b70 is not None:
        expected = (20387560 + 4636 + 1673760) * 1024
        check("vram used counts each client once", b70["vram_used"] == expected, f"{b70['vram_used']} != {expected}")
        check("client count", b70["clients"] == 3, str(b70["clients"]))
        pods = {row["pod_uid"]: row["vram"] for row in b70["processes"]}
        check("per-pod split", pods.get(POD_UID) == (20387560 + 4636) * 1024, str(pods))
        check("second pod split", pods.get(OTHER_UID) == 1673760 * 1024, str(pods))
    check("non-xe client ignored", IGPU not in scan, str(scan.get(IGPU)))
    return scan


def check_render(exporter, scan: dict) -> None:
    total = 32656 * 1024 * 1024
    text = exporter.render(scan, {B70: total}, up=True)
    check("total emitted when configured", f'xe_gpu_vram_total_bytes{{pdev="{B70}"}} {total}' in text)
    check("no total for unconfigured device", text.count("xe_gpu_vram_total_bytes{") == 1, text)
    check("used gauge emitted", f'xe_gpu_vram_used_bytes{{pdev="{B70}"}}' in text)
    check("up gauge emitted", "xe_gpu_exporter_up 1" in text)
    check("per-process series carries pod uid", f'pod_uid="{POD_UID}"' in text)
    check("TYPE line present", "# TYPE xe_gpu_vram_used_bytes gauge" in text)
    check("down state rendered", "xe_gpu_exporter_up 0" in exporter.render({}, {}, up=False))


def main() -> int:
    if not EXPORTER.exists():
        print(f"FAIL: {EXPORTER.relative_to(ROOT)} does not exist")
        return 1
    exporter = load_exporter()
    check_parsing(exporter)
    scan = check_scan(exporter)
    check_render(exporter, scan)

    if failures:
        print("FAIL: system/xe-gpu-exporter contract violated:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("OK: xe-gpu-exporter parses fdinfo, dedupes clients and renders series")
    return 0


if __name__ == "__main__":
    sys.exit(main())
