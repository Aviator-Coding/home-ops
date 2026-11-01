#!/usr/bin/env python3
"""Pins the tuppr upgrade healthChecks to the kopiur mover gate.

Prevents: a node upgrade rolling while a kopiur backup or restore mover is
running, or the gate erroring forever on a CRD that no longer exists.

tuppr (checker.go, 0.5.10) lists every object of the kind, evaluates `expr`
once per object, and needs all of them true. Zero objects pass vacuously, an
absent `status` becomes an empty map, and a CEL error or a missing CRD keeps
retrying until the timeout, which blocks the upgrade. So each expression must
guard the field with has() and name only a phase that means "mover running".
"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DIR = ROOT / "kubernetes/apps/base/system-upgrade/tuppr/upgrades"
FILES = ["talosupgrade.yaml", "kubernetesupgrade.yaml"]
KOPIUR = "kopiur.home-operations.com/v1alpha1"
EXPECTED = {
    "Snapshot": '!has(status.phase) || status.phase != "Running"',
    "Restore": '!has(status.phase) || status.phase != "Restoring"',
}


def checks(name):
    doc = yaml.safe_load((DIR / name).read_text())
    return doc["spec"]["healthChecks"]


def main():
    failures = []
    for name in FILES:
        raw = (DIR / name).read_text()
        if "volsync.backube" in raw:
            failures.append(f"{name}: still references volsync.backube")
        found = {}
        for c in checks(name):
            if c["apiVersion"] == KOPIUR:
                found[c["kind"]] = c["expr"].strip()
        for kind, expr in EXPECTED.items():
            if found.get(kind) != expr:
                failures.append(f"{name}: {kind} check is {found.get(kind)!r}, want {expr!r}")
        kinds = [c["kind"] for c in checks(name)]
        if "CephCluster" not in kinds:
            failures.append(f"{name}: CephCluster check missing")
    for f in failures:
        print("FAIL", f)
    print("tuppr-health-gate-test:", "FAILED" if failures else "ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
