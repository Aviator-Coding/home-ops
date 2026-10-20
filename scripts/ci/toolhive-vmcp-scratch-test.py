#!/usr/bin/env python3
"""vmcp's tool optimizer (find_tool) builds a SQLite FTS5 index per session.

The vmcp container has a read-only root filesystem, so SQLite fails with
"disk I/O error" unless a writable emptyDir backs its temp directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("bc", Path(__file__).with_name("_books_common.py"))
bc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bc
_spec.loader.exec_module(bc)  # type: ignore[union-attr]

VMCP = bc.ROOT / "kubernetes/apps/base/ai/toolhive/config/virtualmcpserver.yaml"
SCRATCH = "/tmp"


def check_tmp_is_writable_emptydir() -> None:
    spec = bc.load_kind(VMCP, "VirtualMCPServer")["spec"]["podTemplateSpec"]["spec"]
    container = next(c for c in spec["containers"] if c["name"] == "vmcp")
    mounts = {m["mountPath"]: m for m in container.get("volumeMounts", [])}
    bc.assert_true(SCRATCH in mounts, f"vmcp must mount {SCRATCH} for the optimizer's SQLite FTS5 index")
    bc.assert_true(not mounts[SCRATCH].get("readOnly"), f"{SCRATCH} mount must be writable")
    volumes = {v["name"]: v for v in spec.get("volumes", [])}
    volume = volumes.get(mounts[SCRATCH]["name"], {})
    bc.assert_true("emptyDir" in volume, f"{SCRATCH} must be backed by an emptyDir")


if __name__ == "__main__":
    sys.exit(bc.run([("vmcp has a writable /tmp for find_tool", check_tmp_is_writable_emptydir)]))
