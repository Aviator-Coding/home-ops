#!/usr/bin/env python3
# ci: gate
"""Behavioral test for the PR risk-tier classifier (scripts/ci/risk_tier.py, goal §5).

Prevents: a change to cluster-core paths (Talos, bootstrap, Flux, Cilium, Ceph,
network, security, backups, CI, Renovate config, the agent guard) being labelled
risk:low and merging itself without the owner.

The highest tier of any changed path wins. Renovate digest and patch updates
outside the risk:high paths are risk:low. Unknown paths default to risk:normal.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("risk_tier", HERE / "risk_tier.py")
if spec is None or spec.loader is None or not (HERE / "risk_tier.py").is_file():
    print("FAIL: scripts/ci/risk_tier.py not found")
    sys.exit(1)
rt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rt)

failures: list[str] = []


def expect(paths: list[str], tier: str, update: str | None = None) -> None:
    got = rt.tier(paths, update)
    ok = got == tier
    print(("[PASS] " if ok else "[FAIL] ") + f"{paths} update={update} -> {got} (want {tier})")
    if not ok:
        failures.append(f"{paths}: {got} != {tier}")


HIGH = [
    "talos/machineconfig.yaml.j2",
    "bootstrap/helmfile/apps.yaml",
    "terraform/authentik/main.tofu",
    "kubernetes/clusters/main/apps.yaml",
    "kubernetes/apps/base/flux-system/flux-instance/app/helmrelease.yaml",
    "kubernetes/apps/main/kube-system/cilium.yaml",
    "kubernetes/apps/base/rook-ceph/rook-ceph/cluster/helmrelease.yaml",
    "kubernetes/apps/base/network/envoy-gateway/app/helmrelease.yaml",
    "kubernetes/apps/base/security/authentik/app/helmrelease.yaml",
    "kubernetes/apps/main/cert-manager/cert-manager.yaml",
    "kubernetes/apps/base/system-upgrade/tuppr/app/helmrelease.yaml",
    "kubernetes/apps/base/renovate/renovate/app/helmrelease.yaml",
    "kubernetes/components/kopiur/pvc/kustomization.yaml",
    "kubernetes/components/volsync/kustomization.yaml",
    "kubernetes/apps/base/system/kopiur/app/helmrelease.yaml",
    ".github/workflows/ci.yaml",
    ".renovaterc.json5",
    ".renovate/groups.json5",
    ".claude/hooks/guard.py",
    ".claude/settings.json",
    "talos/AGENTS.md",
    "talos/mod.just",
]
NORMAL = [
    "kubernetes/apps/base/media/plex/app/helmrelease.yaml",
    "kubernetes/apps/main/ai/litellm.yaml",
    "kubernetes/apps/base/system/openebs-hostpath/app/helmrelease.yaml",
    "scripts/ci/docs-budget-test.py",
    ".taskfiles/rook/Taskfile.yaml",
    ".justfile",
    "kubernetes/mod.just",
    ".mise.toml",
    "kubernetes/apps/base/ai/AGENTS.md",
    ".github/labels.yaml",
    "Taskfile.yaml",
]
LOW = [
    "docs/goals/knowledge-ledger.md",
    ".agents/skills/github-ci/SKILL.md",
    "AGENTS.md",
    "README.md",
]

for p in HIGH:
    expect([p], "risk:high")
for p in NORMAL:
    expect([p], "risk:normal")
for p in LOW:
    expect([p], "risk:low")
expect([], "risk:normal")
# Highest tier wins.
expect(["docs/README.md", "kubernetes/apps/base/media/plex/app/helmrelease.yaml"], "risk:normal")
expect(["README.md", ".github/workflows/flate.yaml"], "risk:high")
# Renovate: digest and patch outside high paths are low; minor/major keep the path tier.
expect(["kubernetes/apps/base/media/plex/app/helmrelease.yaml"], "risk:low", "patch")
expect(["kubernetes/apps/base/media/plex/app/helmrelease.yaml"], "risk:low", "digest")
expect(["kubernetes/apps/base/media/plex/app/helmrelease.yaml"], "risk:normal", "minor")
expect(["kubernetes/apps/base/media/plex/app/helmrelease.yaml"], "risk:normal", "major")
expect(["kubernetes/apps/base/kube-system/cilium/app/ocirepository.yaml"], "risk:high", "patch")
expect([".github/workflows/ci.yaml"], "risk:high", "digest")
# No path tricks: a high directory name deeper in a normal path is not high.
expect(["kubernetes/apps/base/media/network-tools/app/helmrelease.yaml"], "risk:normal")
expect(["docs/" + "talos/notes.md"], "risk:low")  # synthetic path; built so doc-links skips it

print(f"Summary: {len(failures)} failed")
sys.exit(1 if failures else 0)
