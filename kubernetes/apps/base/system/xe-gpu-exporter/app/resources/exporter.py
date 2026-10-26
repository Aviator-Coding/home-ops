#!/usr/bin/env python3
"""Prometheus exporter for Intel xe GPU VRAM, read from DRM fdinfo.

The xe driver exposes no VRAM counters in sysfs. Each process holding a DRM
node does publish /proc/<pid>/fdinfo/<fd> with `drm-total-vram0` (everything
that client has resident in VRAM: weights, KV cache, compute buffers). This
sums those per card and per pod. Needs hostPID and CAP_SYS_PTRACE to read other
processes' fd links. Contract: scripts/ci/xe-gpu-exporter-test.py.
"""
import logging
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

LOG = logging.getLogger("xe-gpu-exporter")

DEFAULT_PORT = 9105
DEFAULT_PROC_ROOT = "/proc"
PCI_ROOT = "/sys/bus/pci/devices"
SIZE_UNITS = {"": 1, "B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}
SIZE_PATTERN = re.compile(r"^\s*(\d+)\s*([KMG]iB|B)?\s*$")
# cgroupfs: .../pod<uid>/...   systemd: ...-pod<uid_with_underscores>.slice
POD_PATTERN = re.compile(r"pod([0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{12})")
DRM_NODE_PATTERN = re.compile(r"dri/(renderD|card)\d+")
XE_DRIVER = "xe"


def size_to_bytes(value: str) -> int:
    match = SIZE_PATTERN.match(value)
    if not match:
        return 0
    return int(match.group(1)) * SIZE_UNITS[match.group(2) or ""]


def parse_fdinfo(text: str) -> dict[str, str]:
    pairs = (line.split(":", 1) for line in text.splitlines() if ":" in line)
    return {key.strip(): value.strip() for key, value in pairs}


def pod_uid_from_cgroup(text: str) -> str:
    match = POD_PATTERN.search(text)
    return match.group(1).replace("_", "-") if match else ""


def parse_vram_totals(spec: str) -> dict[str, int]:
    """`0000:03:00.0=32656,...` (MiB per PCI address) to bytes."""
    totals: dict[str, int] = {}
    for entry in filter(None, (part.strip() for part in spec.split(","))):
        pdev, _, mib = entry.partition("=")
        if pdev and mib.strip().isdigit():
            totals[pdev.strip()] = int(mib) * 1024**2
    return totals


def installed_totals(totals: dict[str, int], pci_root: str = PCI_ROOT) -> dict[str, int]:
    """Configured capacities for cards on this node. Every DaemonSet pod gets the same config."""
    return {pdev: total for pdev, total in totals.items() if os.path.exists(os.path.join(pci_root, pdev))}


def with_idle_cards(cards: dict[str, dict[str, Any]], installed: dict[str, int]) -> dict[str, dict[str, Any]]:
    """An installed card with no clients is 0 bytes used, not a missing series."""
    idle = {
        pdev: {"vram_used": 0, "gtt_used": 0, "clients": 0, "processes": []}
        for pdev in installed
        if pdev not in cards
    }
    return {**cards, **idle}


def read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def drm_fdinfos(proc_root: str, pid: str) -> list[dict[str, str]]:
    """fdinfo of every DRM-node fd a process holds. A vanished PID yields nothing."""
    fd_dir = os.path.join(proc_root, pid, "fd")
    try:
        fds = os.listdir(fd_dir)
    except OSError:
        return []
    infos = []
    for fd in fds:
        try:
            if not DRM_NODE_PATTERN.search(os.readlink(os.path.join(fd_dir, fd))):
                continue
            infos.append(parse_fdinfo(read_text(os.path.join(proc_root, pid, "fdinfo", fd))))
        except OSError:
            continue
    return infos


def process_identity(proc_root: str, pid: str) -> tuple[str, str]:
    try:
        pod_uid = pod_uid_from_cgroup(read_text(os.path.join(proc_root, pid, "cgroup")))
        comm = read_text(os.path.join(proc_root, pid, "comm")).strip()
    except OSError:
        return "", "unknown"
    return pod_uid, comm


def scan(proc_root: str = DEFAULT_PROC_ROOT) -> dict[str, dict[str, Any]]:
    """Per-card VRAM and GTT use. A (pdev, drm-client-id) pair counts once."""
    seen: set[tuple[str, str]] = set()
    cards: dict[str, dict[str, Any]] = {}
    groups: dict[tuple[str, str, str], dict[str, int]] = {}
    pids = sorted((entry for entry in os.listdir(proc_root) if entry.isdigit()), key=int)
    for pid in pids:
        pod_uid, comm = process_identity(proc_root, pid)
        for info in drm_fdinfos(proc_root, pid):
            pdev = info.get("drm-pdev", "")
            client = (pdev, info.get("drm-client-id", ""))
            if info.get("drm-driver") != XE_DRIVER or not pdev or client in seen:
                continue
            seen.add(client)
            vram = size_to_bytes(info.get("drm-total-vram0", ""))
            gtt = size_to_bytes(info.get("drm-total-gtt", ""))
            card = cards.setdefault(pdev, {"vram_used": 0, "gtt_used": 0, "clients": 0, "processes": []})
            card["vram_used"] += vram
            card["gtt_used"] += gtt
            card["clients"] += 1
            group = groups.setdefault((pdev, pod_uid, comm), {"vram": 0, "gtt": 0})
            group["vram"] += vram
            group["gtt"] += gtt
    for (pdev, pod_uid, comm), usage in groups.items():
        cards[pdev]["processes"].append({"pod_uid": pod_uid, "comm": comm, **usage})
    return cards


def escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def labels(**pairs: str) -> str:
    return "{" + ",".join(f'{key}="{escape_label(value)}"' for key, value in pairs.items()) + "}"


def render(cards: dict[str, dict[str, Any]], totals: dict[str, int], up: bool) -> str:
    lines: list[str] = []

    def header(name: str, help_text: str) -> None:
        lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} gauge"])

    header("xe_gpu_exporter_up", "1 when the last fdinfo scan completed.")
    lines.append(f"xe_gpu_exporter_up {int(up)}")
    header("xe_gpu_vram_total_bytes", "Configured VRAM capacity of the card.")
    lines.extend(f"xe_gpu_vram_total_bytes{labels(pdev=pdev)} {total}" for pdev, total in sorted(totals.items()))
    for name, key, help_text in (
        ("xe_gpu_vram_used_bytes", "vram_used", "VRAM held by all DRM clients on the card."),
        ("xe_gpu_gtt_used_bytes", "gtt_used", "Host memory mapped through the GTT by all DRM clients."),
        ("xe_gpu_clients", "clients", "Distinct DRM clients on the card."),
    ):
        header(name, help_text)
        lines.extend(f"{name}{labels(pdev=pdev)} {card[key]}" for pdev, card in sorted(cards.items()))
    for name, key, help_text in (
        ("xe_gpu_process_vram_bytes", "vram", "VRAM held by a pod's process on the card."),
        ("xe_gpu_process_gtt_bytes", "gtt", "GTT memory held by a pod's process on the card."),
    ):
        header(name, help_text)
        lines.extend(
            f"{name}{labels(pdev=pdev, pod_uid=row['pod_uid'], comm=row['comm'])} {row[key]}"
            for pdev, card in sorted(cards.items())
            for row in sorted(card["processes"], key=lambda r: (r["pod_uid"], r["comm"]))
        )
    return "\n".join(lines) + "\n"


def make_handler(proc_root: str, totals: dict[str, int]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                body, status = b"ok\n", 200
            elif self.path == "/metrics":
                installed = installed_totals(totals)
                try:
                    cards = with_idle_cards(scan(proc_root), installed)
                    body, status = render(cards, installed, up=True).encode(), 200
                except OSError:
                    LOG.exception("fdinfo scan failed")
                    body, status = render({}, installed, up=False).encode(), 200
            else:
                body, status = b"not found\n", 404
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            LOG.debug(format, *args)

    return Handler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    proc_root = os.environ.get("PROC_ROOT", DEFAULT_PROC_ROOT)
    totals = parse_vram_totals(os.environ.get("XE_VRAM_TOTALS_MIB", ""))
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    LOG.info("serving on :%d proc=%s totals=%s", port, proc_root, totals)
    ThreadingHTTPServer(("0.0.0.0", port), make_handler(proc_root, totals)).serve_forever()


if __name__ == "__main__":
    main()
