#!/usr/bin/env python3
"""Semantic regression test for the corrupt-claim recreation contract.

Pins the 2026-08-31 ai/opencode volume recreation findings that live in:

  - docs/backups/corrupt-claim-recreation-runbook.md  (durable procedure)
  - docs/backups/opencode-volume-recreation-2026-08-31.md  (measured evidence)
  - AGENTS.md  (two fleet-wide findings)

THE CENTRAL CONTRACT: deleting a PVC and letting Flux recreate it from
dataSourceRef does NOT restore from the restic repository. The populator
clones ${APP}-dst.status.latestImage, and ${APP}-dst is trigger manual
restore-once + ssa IfNotPresent, so it runs exactly once at first deploy.
On a newly-onboarded app that one run can leave latestImage as a snapshot
of an EMPTY volume forever. The fix is to delete the ReplicationDestination
TOGETHER WITH the PVC so restore-once fires against the populated repo.

This test does NOT grep implementation source as its evidence. It:

  1. Renders the real volsync Component kustomize build Flux would apply for
     ai/opencode (postBuild.substitute taken from the live overlay), then
     parses the resulting objects into a typed structure.
  2. Asserts the load-bearing component shape that makes the empty-
     latestImage trap real: PVC dataSourceRef -> ReplicationDestination
     ${APP}-dst (not restic), RD trigger restore-once + ssa IfNotPresent,
     writable-stage moverSecurityContext.fsGroup (the mode-relaxation
     fingerprint), enableFileDeletion (lost+found removal), and ceph-block
     reclaimPolicy Delete (RBD image is genuinely destroyed).
  3. Parses the operator-facing result artifacts (runbook + evidence +
     AGENTS.md) as owned text contracts - the same class as the Stage 2
     drill pin in kopiur-stage2-test.py - and asserts the measured gates,
     the two findings as first-class facts, and the safety constraints
     (no credential contents, never delete a kopiur Snapshot CR, delete
     RD with the PVC not patch trigger.manual).

Live cluster confirmation (destroy + recreate + five verification gates)
was already executed 2026-08-31 and is recorded in the evidence doc.
Fresh worktrees never carry kubeconfig (AGENTS.md); this CI gate therefore
pins the GitOps + documentary contract that must hold before merge.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
VOLSYNC_COMPONENT = ROOT / "kubernetes/components/volsync"
VOLSYNC_PVC = VOLSYNC_COMPONENT / "pvc.yaml"
VOLSYNC_RD = VOLSYNC_COMPONENT / "ceph" / "replicationdestination.yaml"
OPENCODE_OVERLAY = ROOT / "kubernetes/apps/main/ai/opencode.yaml"
ROOK_CLUSTER_HR = (
    ROOT / "kubernetes/apps/base/rook-ceph/rook-ceph/cluster/helmrelease.yaml"
)
RUNBOOK = ROOT / "docs/backups/corrupt-claim-recreation-runbook.md"
EVIDENCE = ROOT / "docs/backups/opencode-volume-recreation-2026-08-31.md"
AGENTS = ROOT / "AGENTS.md"
VOLSYNC_DRILL = ROOT / "docs/backups/restore-drill-2026-08-23.md"
KOPIUR_DRILL = ROOT / "docs/backups/kopiur-restore-drill-2026-08-30.md"

# Live opencode overlay pins (source of truth at render time).
OPENCODE_APP = "opencode"
OPENCODE_NS = "ai"
OPENCODE_CAPACITY = "20Gi"
OPENCODE_CACHE = "5Gi"
OPENCODE_VOLSYNC_CEPH = "45 */4 * * *"
OPENCODE_VOLSYNC_MINIO = "45 */6 * * *"
OPENCODE_VOLSYNC_R2 = "30 2 * * *"
OPENCODE_KOPIUR_R2 = "H 19 * * *"

# Measured evidence numbers from the 2026-08-31 run (public result contract).
LIVE_FILE_COUNT = 4749
LIVE_DIR_COUNT = 950
LIVE_BYTE_COUNT = 161_617_941
LIVE_MANIFEST_DIGEST = (
    "9f400f6d6b99f25c039b763d5458b8ec4fb0347e9149baa3e88ba92a28fafc55"
)
PRECHECK_IDENTICAL = 4748
POST_IDENTICAL = 4744
POST_DIFFERING = 5
VOLSYNC_CEPH_SNAPSHOT = "5d72f28a"
VOLSYNC_MINIO_SNAPSHOT = "81f18d92"
KOPIUR_CEPH_SNAPSHOT = "b2fdf535020b18f89572e819d297d436"
KOPIUR_FILES_NEW = 4749
KOPIUR_SIZE_BYTES = 161_589_393
RESTIC_FALLBACK_SNAPSHOT = "4f8214f8"
EMPTY_DST_LAST_SYNC = "2026-08-27T10:20:07Z"
EMPTY_DST_IMAGE = "volsync-opencode-dst-dest-20260827062006"
NEW_RBD_DEVICE = "/dev/rbd15"
PROCESSED_SIZE_MIB = "154.131"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def _which(name: str) -> str | None:
    return shutil.which(name)


def load_multi(path: Path) -> list[dict[str, Any]]:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    require(bool(docs), f"{path} produced no YAML documents")
    return docs


def flux_envsubst(text: str, env: dict[str, str]) -> str:
    """Flux-shaped envsubst including ${VAR:-default} and nested ${A:-${B}}."""

    def lookup(key: str) -> str | None:
        return env.get(key)

    def expand(s: str) -> str:
        out: list[str] = []
        i = 0
        while i < len(s):
            if s[i : i + 2] != "${":
                out.append(s[i])
                i += 1
                continue
            # Find matching closing brace, allowing nesting.
            depth = 0
            j = i
            while j < len(s):
                if s[j : j + 2] == "${":
                    depth += 1
                    j += 2
                    continue
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            else:
                out.append(s[i])
                i += 1
                continue
            body = s[i + 2 : j - 1]
            if ":-" in body:
                key, default = body.split(":-", 1)
            else:
                key, default = body, None
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key or ""):
                out.append(s[i:j])
                i = j
                continue
            val = lookup(key)
            if val is not None:
                out.append(val)
            elif default is not None:
                out.append(expand(default))
            else:
                out.append(s[i:j])
            i = j
        return "".join(out)

    return expand(text)


def overlay_substitute(path: Path) -> dict[str, str]:
    docs = load_multi(path)
    require(len(docs) == 1, f"{path.name} must be one document, got {len(docs)}")
    ks = docs[0]
    require(ks.get("kind") == "Kustomization", f"{path.name} must be a Flux Kustomization")
    sub = (((ks.get("spec") or {}).get("postBuild") or {}).get("substitute")) or {}
    require(isinstance(sub, dict) and sub, f"{path.name} missing postBuild.substitute")
    return {str(k): str(v) for k, v in sub.items()}


def render_with_substitute(path: Path, env: dict[str, str]) -> list[dict[str, Any]]:
    kustomize = _which("kustomize")
    cmd: list[str]
    if kustomize:
        cmd = [kustomize, "build", "--enable-alpha-plugins", "--enable-exec", str(path)]
        # Component builds need plain `kustomize build` of a kustomization that
        # includes the component. For a Component directory itself, build via
        # a throwaway wrapper is unnecessary - volsync is a Component, so we
        # assemble from its resources the same way Flux does when the parent
        # Component is included. Prefer building the component's own resources
        # list by walking it: parent Component resources are ./backup + pvc.
        # kustomize cannot `build` a Component alone; use the backup bundle +
        # pvc.yaml concatenated, which is what the Component composes.
        if (path / "kustomization.yaml").exists():
            # Detect Component vs Kustomization.
            meta = yaml.safe_load((path / "kustomization.yaml").read_text()) or {}
            if meta.get("kind") == "Component":
                return _render_volsync_component(env)
        cmd = [kustomize, "build", str(path)]
    else:
        kubectl = _which("kubectl")
        if not kubectl:
            raise Failure("neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(path)]
        meta = yaml.safe_load((path / "kustomization.yaml").read_text()) or {}
        if meta.get("kind") == "Component":
            return _render_volsync_component(env)
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Failure(
            f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    rendered = flux_envsubst(proc.stdout, env)
    unresolved = sorted(set(re.findall(r"\$\{[A-Za-z_][^}]*\}", rendered)))
    require(
        not unresolved,
        f"unresolved substitution tokens after envsubst of {path}: {unresolved}",
    )
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    if not docs:
        raise Failure(f"substituted build of {path} produced no documents")
    return docs


def _render_volsync_component(env: dict[str, str]) -> list[dict[str, Any]]:
    """Render the volsync Component the way Flux composes it: backup + pvc.

    kustomize cannot `build` a Component directory alone. The Component's
    kustomization.yaml lists ./backup and ./pvc.yaml; backup is a normal
    Kustomization that pulls ceph/minio/r2. Build backup, then append the
    substituted pvc.yaml document - identical object set to what Flux gets
    when apps/main/ai/opencode.yaml includes components/volsync.
    """
    kustomize = _which("kustomize")
    backup = VOLSYNC_COMPONENT / "backup"
    if kustomize:
        cmd = [kustomize, "build", str(backup)]
    else:
        kubectl = _which("kubectl")
        if not kubectl:
            raise Failure("neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(backup)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Failure(
            f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    pvc_raw = VOLSYNC_PVC.read_text()
    combined = proc.stdout + "\n---\n" + pvc_raw
    rendered = flux_envsubst(combined, env)
    unresolved = sorted(set(re.findall(r"\$\{[A-Za-z_][^}]*\}", rendered)))
    require(
        not unresolved,
        f"unresolved substitution tokens after envsubst of volsync component: {unresolved}",
    )
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    require(bool(docs), "volsync component render produced no documents")
    return docs


def by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == kind]


def test_opencode_overlay_pins() -> dict[str, str]:
    """ai/opencode still includes volsync + the capacity that was recreated."""
    docs = load_multi(OPENCODE_OVERLAY)
    ks = docs[0]
    require(ks.get("metadata", {}).get("name") == OPENCODE_APP, "overlay name is opencode")
    require(ks.get("metadata", {}).get("namespace") == OPENCODE_NS, "overlay ns is ai")
    components = ks.get("spec", {}).get("components") or []
    require(
        any(str(c).rstrip("/").endswith("components/volsync") for c in components),
        "opencode must still include components/volsync (claim is component-owned)",
    )
    require(
        any(str(c).rstrip("/").endswith("components/kopiur") for c in components),
        "opencode must still include components/kopiur (dual-engine)",
    )
    sub = overlay_substitute(OPENCODE_OVERLAY)
    require(sub.get("APP") == OPENCODE_APP, f"APP must be opencode, got {sub.get('APP')!r}")
    require(
        sub.get("VOLSYNC_CAPACITY") == OPENCODE_CAPACITY,
        f"VOLSYNC_CAPACITY must be {OPENCODE_CAPACITY}, got {sub.get('VOLSYNC_CAPACITY')!r}",
    )
    require(
        sub.get("VOLSYNC_CACHE_CAPACITY") == OPENCODE_CACHE,
        f"VOLSYNC_CACHE_CAPACITY must be {OPENCODE_CACHE}",
    )
    require(sub.get("VOLSYNC_SCHEDULE_CEPH") == OPENCODE_VOLSYNC_CEPH, "ceph schedule pin")
    require(sub.get("VOLSYNC_SCHEDULE_MINIO") == OPENCODE_VOLSYNC_MINIO, "minio schedule pin")
    require(sub.get("VOLSYNC_SCHEDULE_R2") == OPENCODE_VOLSYNC_R2, "r2 schedule pin")
    require(sub.get("KOPIUR_SCHEDULE_R2") == OPENCODE_KOPIUR_R2, "kopiur r2 hour 19 pin")
    # No identity override - measured 1000:1000, component default.
    require(
        "KOPIUR_PUID" not in sub and "VOLSYNC_PUID" not in sub,
        "opencode must keep the default 1000:1000 mover identity (measured)",
    )
    # cluster-secrets keys are substituteFrom, not inline. Provide a stand-in so
    # the ExternalSecret RESTIC_REPOSITORY template can resolve for the render.
    sub = dict(sub)
    sub.setdefault("SECRET_DOMAIN", "example.test")
    return sub


def test_rendered_empty_latestimage_trap(sub: dict[str, str]) -> list[dict[str, Any]]:
    """The component shape that makes 'delete PVC alone' restore NOTHING.

    Asserted on the rendered objects Flux would apply for opencode, not on
    template source text:
      - PVC dataSourceRef -> ReplicationDestination ${APP}-dst (apiGroup
        volsync.backube). There is NO restic field on the PVC. The populator
        therefore clones latestImage, not the repository.
      - RD named ${APP}-dst carries trigger.manual == restore-once AND label
        kustomize.toolkit.fluxcd.io/ssa == IfNotPresent. Together: runs once
        at first deploy, Flux never re-runs it.
      - Three ReplicationSources (ceph/minio/r2) still exist - dual-engine
        recreation must not retire VolSync.
    """
    docs = render_with_substitute(VOLSYNC_COMPONENT, sub)

    pvcs = by_kind(docs, "PersistentVolumeClaim")
    require(len(pvcs) == 1, f"expected exactly 1 PVC from component, got {len(pvcs)}")
    pvc = pvcs[0]
    require(pvc.get("metadata", {}).get("name") == OPENCODE_APP, "PVC named opencode")
    dsr = (pvc.get("spec") or {}).get("dataSourceRef") or {}
    require(
        dsr.get("kind") == "ReplicationDestination",
        f"PVC dataSourceRef.kind must be ReplicationDestination, got {dsr.get('kind')!r}",
    )
    require(
        dsr.get("apiGroup") == "volsync.backube",
        f"PVC dataSourceRef.apiGroup must be volsync.backube, got {dsr.get('apiGroup')!r}",
    )
    require(
        dsr.get("name") == f"{OPENCODE_APP}-dst",
        f"PVC dataSourceRef.name must be {OPENCODE_APP}-dst, got {dsr.get('name')!r}",
    )
    # The PVC itself has no restic configuration - restore path is only via RD.
    require(
        "restic" not in (pvc.get("spec") or {}),
        "PVC must not carry restic config; populator reads latestImage only",
    )
    storage = ((pvc.get("spec") or {}).get("resources") or {}).get("requests") or {}
    require(
        storage.get("storage") == OPENCODE_CAPACITY,
        f"PVC capacity must be {OPENCODE_CAPACITY}, got {storage.get('storage')!r}",
    )
    require(
        (pvc.get("spec") or {}).get("storageClassName") == "ceph-block",
        "PVC storageClassName must be ceph-block",
    )

    rds = by_kind(docs, "ReplicationDestination")
    require(len(rds) == 1, f"expected exactly 1 ReplicationDestination, got {len(rds)}")
    rd = rds[0]
    require(rd.get("metadata", {}).get("name") == f"{OPENCODE_APP}-dst", "RD name")
    labels = (rd.get("metadata") or {}).get("labels") or {}
    require(
        labels.get("kustomize.toolkit.fluxcd.io/ssa") == "IfNotPresent",
        f"RD must carry ssa IfNotPresent, got {labels.get('kustomize.toolkit.fluxcd.io/ssa')!r}",
    )
    trigger = (rd.get("spec") or {}).get("trigger") or {}
    require(
        trigger.get("manual") == "restore-once",
        f"RD trigger.manual must be restore-once, got {trigger.get('manual')!r}",
    )
    # No schedule on the RD - it is not a recurring restore.
    require(
        "schedule" not in trigger,
        "RD must not carry a schedule (one-shot restore-once only)",
    )
    restic = (rd.get("spec") or {}).get("restic") or {}
    require(
        restic.get("repository") == f"{OPENCODE_APP}-volsync-ceph-secret",
        f"RD restic.repository must be the ceph secret, got {restic.get('repository')!r}",
    )
    require(
        restic.get("enableFileDeletion") is True,
        "RD must enableFileDeletion (restic --delete; drops lost+found on restore)",
    )
    # Mode-relaxation fingerprint: mover stages writable with fsGroup set, so
    # kubelet's recursive walk runs before restic writes.
    msc = restic.get("moverSecurityContext") or {}
    require(
        msc.get("fsGroup") == 1000,
        f"RD moverSecurityContext.fsGroup must be 1000 (default), got {msc.get('fsGroup')!r}",
    )
    require(
        msc.get("runAsUser") == 1000 and msc.get("runAsGroup") == 1000,
        f"RD mover identity must be 1000:1000, got {msc}",
    )
    require(
        restic.get("capacity") == OPENCODE_CAPACITY,
        f"RD capacity must match claim {OPENCODE_CAPACITY}",
    )

    sources = by_kind(docs, "ReplicationSource")
    src_names = sorted(s.get("metadata", {}).get("name") for s in sources)
    require(
        src_names
        == [
            f"{OPENCODE_APP}-ceph",
            f"{OPENCODE_APP}-minio",
            f"{OPENCODE_APP}-r2",
        ],
        f"expected triple-dest ReplicationSources, got {src_names}",
    )
    return docs


def test_ceph_block_reclaim_delete() -> None:
    """ceph-block is reclaimPolicy Delete - deleting the PVC destroys the RBD image.

    Parsed from the live rook-ceph cluster HelmRelease values, not assumed.
    """
    docs = load_multi(ROOK_CLUSTER_HR)
    hr = docs[0]
    values = (hr.get("spec") or {}).get("values") or {}
    # storageClass.reclaimPolicy under cephBlockPools / storageClass
    # Rook chart: cephClusterSpec is separate; block pool storageClass lives at
    # cephBlockPools[].storageClass.reclaimPolicy or top-level.
    text = yaml.safe_dump(values)
    # Walk structured values for a storageClass named ceph-block with Delete.
    found = _find_ceph_block_reclaim(values)
    require(
        found == "Delete",
        f"ceph-block reclaimPolicy must be Delete (RBD image destroyed with PVC), got {found!r}",
    )
    # Keep a textual anchor so a chart restructure that drops the name still fails.
    require("ceph-block" in text, "helm values must still declare ceph-block")


def _find_ceph_block_reclaim(obj: Any) -> str | None:
    """Depth-first search for a mapping that names ceph-block and has reclaimPolicy."""
    if isinstance(obj, dict):
        name = obj.get("name") or obj.get("storageClassName")
        # Chart shape: storageClass: { name: ceph-block, reclaimPolicy: Delete }
        sc = obj.get("storageClass")
        if isinstance(sc, dict) and sc.get("name") == "ceph-block":
            return sc.get("reclaimPolicy")
        if name == "ceph-block" and "reclaimPolicy" in obj:
            return obj.get("reclaimPolicy")
        for v in obj.values():
            got = _find_ceph_block_reclaim(v)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for item in obj:
            got = _find_ceph_block_reclaim(item)
            if got is not None:
                return got
    return None


def test_runbook_procedure_contract() -> None:
    """Runbook is the durable operator procedure; pin its safety contract.

    The markdown file is the operator-facing deliverable (owned text contract),
    not a proxy for unrelated code. Assert the procedure an operator must
    follow cannot silently restore nothing.
    """
    require(RUNBOOK.is_file(), f"missing runbook {RUNBOOK.relative_to(ROOT)}")
    require(VOLSYNC_DRILL.is_file(), "VolSync sibling drill must remain")
    require(KOPIUR_DRILL.is_file(), "kopiur sibling drill must remain")
    text = RUNBOOK.read_text()
    lowered = text.lower()

    # Must lead with the empty-latestImage trap (before any delete step).
    # Find the first heading-level warning / "read this before" vs the delete PVC step.
    before_idx = None
    for pat in (
        r"read this before you delete anything",
        r"does not restore from your\s+latest backup",
        r"may restore nothing while every signal",
    ):
        m = re.search(pat, lowered)
        if m:
            before_idx = m.start() if before_idx is None else min(before_idx, m.start())
    require(
        before_idx is not None,
        "runbook must lead with the empty-latestImage / restore-nothing warning",
    )
    delete_pvc_idx = None
    for pat in (r"delete the pvc", r"delete.*persistentvolumeclaim"):
        m = re.search(pat, lowered)
        if m:
            delete_pvc_idx = m.start()
            break
    require(delete_pvc_idx is not None, "runbook must include a Delete the PVC step")
    require(
        before_idx < delete_pvc_idx,
        "empty-latestImage warning must appear BEFORE the PVC delete step",
    )

    # Central correction: populator reads latestImage, not restic.
    require(
        re.search(r"latestimage", lowered),
        "runbook must name latestImage as the populator source",
    )
    require(
        re.search(r"dataSourceRef", text),
        "runbook must name dataSourceRef",
    )
    require(
        re.search(r"restore-once", text) or re.search(r"restore.once", lowered),
        "runbook must name the restore-once trigger",
    )
    require(
        re.search(r"IfNotPresent", text),
        "runbook must name ssa IfNotPresent",
    )
    require(
        re.search(r"no eligible snapshots found", lowered)
        or re.search(r"no data will be restored", lowered),
        "runbook must quote the empty-repo mover log shape",
    )

    # The fix: delete RD together with PVC; never patch trigger.manual.
    require(
        re.search(
            r"delete.{0,80}replicationdestination.{0,40}(together|along).{0,20}(with|the).{0,20}pvc"
            r"|delete.{0,40}pvc.{0,80}replicationdestination"
            r"|replicationdestination.{0,40}together with.{0,20}the pvc",
            lowered,
            re.S,
        ),
        "runbook must require deleting the ReplicationDestination together with the PVC",
    )
    require(
        re.search(
            r"(do\s+\*\*not\*\*|do not|never).{0,60}patch.{0,40}(trigger\.manual|spec\.trigger)",
            lowered,
            re.S,
        )
        or re.search(r"never patch.*trigger", lowered, re.S),
        "runbook must forbid patching trigger.manual on the app's own destination",
    )

    # Pre-check restore before destroy.
    require(
        re.search(r"prove the restore before you destroy", lowered)
        or re.search(r"pre-check", lowered),
        "runbook must require a pre-check restore before destroying the live claim",
    )
    require(
        re.search(r"scratch", lowered),
        "runbook must use a scratch ReplicationDestination (never the app's own)",
    )

    # Ordering: suspend Flux, scale to 0, clear movers, delete RS (not fight
    # restage loop), never delete kopiur Snapshot CR.
    require(re.search(r"flux suspend", lowered), "runbook must suspend Flux ks")
    require(
        re.search(r"scale.{0,40}0", lowered) or re.search(r"replicas.?=.?0", lowered),
        "runbook must scale the app to 0 before PVC delete",
    )
    require(
        re.search(r"replicationsource", lowered)
        and re.search(r"re-stage|restage|reloop", lowered),
        "runbook must require deleting ReplicationSources (VolSync restage loop)",
    )
    require(
        re.search(r"never delete a kopiur `?snapshot`? cr", lowered)
        or re.search(r"never delete.{0,40}kopiur.{0,40}snapshot", lowered),
        "runbook must forbid deleting a kopiur Snapshot CR (finalizer owns data)",
    )

    # Mode-relaxation finding is first-class in verification, not a footnote.
    require(
        re.search(r"644\s*→\s*664|644->664", text)
        or re.search(r"644.?664", text),
        "runbook must document the 644→664 mode-relaxation fingerprint",
    )
    require(
        re.search(r"600\s*→\s*660|600->660|0600.{0,20}0660", text)
        or re.search(r"\.git-credentials", text),
        "runbook must call out credential mode widening (0600→0660 / .git-credentials)",
    )
    require(
        re.search(r"fsgroup", lowered),
        "runbook must attribute mode relaxation to kubelet fsGroup walk",
    )

    # Five verification gates.
    for gate in (
        r"data is back",
        r"kopiur backs up",
        r"volsync backs up",
        r"clone of .{0,40}(new|those new) backup",
        r"alerts? cleared",
    ):
        require(
            re.search(gate, lowered),
            f"runbook verification must include gate matching /{gate}/",
        )
    require(
        re.search(r"status\.stats", text) or re.search(r"\.status\.stats", text),
        "runbook must require non-zero .status.stats (Succeeded alone proves nothing)",
    )

    # Credential hygiene.
    require(
        re.search(r"never cat|never let one reach|only.{0,20}presence, mode", lowered),
        "runbook must forbid reading credential file contents",
    )

    # Scope: replacement not diagnosis; no repairing fsck.
    require(
        re.search(r"does not repair|not repair", lowered),
        "runbook replaces the volume; it does not repair it",
    )
    require(
        re.search(r"do not run a repairing\s+`?fsck", lowered)
        or re.search(r"not run.{0,20}repairing.{0,20}fsck", lowered),
        "runbook must forbid repairing fsck against live storage",
    )

    # Links to the measured evidence appendix.
    require(
        "opencode-volume-recreation-2026-08-31.md" in text,
        "runbook must point at the measured evidence record",
    )


def test_evidence_result_contract() -> None:
    """Evidence doc is the measured public result artifact of the recreation."""
    require(EVIDENCE.is_file(), f"missing evidence {EVIDENCE.relative_to(ROOT)}")
    text = EVIDENCE.read_text()
    lowered = text.lower()

    # Overall PASS for ai/opencode on 2026-08-31.
    require(re.search(r"\bpass\b", lowered), "evidence must declare PASS")
    require("opencode" in lowered, "evidence subject is opencode")
    require("2026-08-31" in text, "evidence date is 2026-08-31")

    # The finding that changed the plan (empty latestImage) is first-class.
    require(
        re.search(r"finding that changed the plan|empty", lowered)
        and re.search(r"latestimage", lowered),
        "evidence must lead with the empty-latestImage finding",
    )
    require(
        EMPTY_DST_LAST_SYNC in text,
        f"evidence must record empty-dst lastSyncTime {EMPTY_DST_LAST_SYNC}",
    )
    require(
        EMPTY_DST_IMAGE in text,
        f"evidence must record empty latestImage name {EMPTY_DST_IMAGE}",
    )
    require(
        re.search(r"no eligible snapshots found", lowered),
        "evidence must quote the empty-repo mover log",
    )
    require(
        re.search(r"no data will be restored", lowered),
        "evidence must quote 'No data will be restored'",
    )
    require(
        re.search(r"delete.{0,60}opencode-dst.{0,40}together", lowered, re.S)
        or re.search(r"together with.{0,20}the pvc", lowered),
        "evidence must state the RD-was-deleted-with-PVC fix",
    )

    # Step 1 inventory numbers.
    require(str(LIVE_FILE_COUNT) in text, f"inventory file count {LIVE_FILE_COUNT}")
    require(str(LIVE_DIR_COUNT) in text, f"inventory dir count {LIVE_DIR_COUNT}")
    # Accept space-grouped or plain byte counts.
    byte_plain = str(LIVE_BYTE_COUNT)
    byte_grouped = "161 617 941"
    require(
        byte_plain in text.replace(",", "") or byte_grouped in text,
        f"inventory byte count {LIVE_BYTE_COUNT}",
    )
    require(
        LIVE_MANIFEST_DIGEST in text,
        f"sha256 manifest digest {LIVE_MANIFEST_DIGEST}",
    )

    # Credential metadata only - presence/mode/size, never contents.
    require(
        re.search(r"\.git-credentials", text),
        "evidence must record .git-credentials presence",
    )
    require(
        re.search(r"mode\s*=?\s*600|mode=600|`600`", text)
        or re.search(r"mode=600", text),
        "evidence must record pre-restore mode 600 for .git-credentials",
    )
    require(
        re.search(
            r"contents were never read|never read, printed|never.{0,20}committed",
            lowered,
        ),
        "evidence must state credential contents were never read",
    )
    # No private token-looking material: reject lines that look like leaked secrets.
    # Allow the word "credentials" and hashes; reject obvious key=value secrets.
    for line in text.splitlines():
        if re.search(r"(api[_-]?key|token|password|secret)\s*[:=]\s*\S{8,}", line, re.I):
            # Allow references to Secret *names* and 1Password item names.
            if re.search(r"(secretname|secret ref|credential secret|1password)", line, re.I):
                continue
            if "volsync" in line.lower() or "repository" in line.lower():
                continue
            raise Failure(f"evidence appears to embed a credential value: {line!r}")

    # Pre-check: mount-verified restore while live volume still existed.
    require(
        str(PRECHECK_IDENTICAL) in text,
        f"pre-check must record {PRECHECK_IDENTICAL}/4749 byte-identical",
    )
    require(
        RESTIC_FALLBACK_SNAPSHOT in text,
        f"pre-check / fallback restic snapshot {RESTIC_FALLBACK_SNAPSHOT}",
    )
    require(
        re.search(r"mounted cleanly", lowered),
        "pre-check must record clean mount of the restored clone",
    )

    # Post-recreation data gate.
    require(
        str(POST_IDENTICAL) in text,
        f"post-recreation must record {POST_IDENTICAL} byte-identical files",
    )
    require(
        re.search(rf"{POST_DIFFERING}\s+files differ|{POST_DIFFERING}\s+differ", lowered)
        or f"{POST_DIFFERING} differ" in lowered,
        f"post-recreation must account for {POST_DIFFERING} app-startup diffs",
    )
    for startup_file in (
        "models.json",
        ".gitconfig",
        "opencode.log",
        "opencode.db",
        ".db-shm",
    ):
        require(
            startup_file in text,
            f"post-recreation must name startup-written file {startup_file}",
        )
    require(
        re.search(r"lost\+found", lowered) and re.search(r"absent", lowered),
        "post-recreation must note lost+found absent (restic never stores it)",
    )

    # Mode relaxation measured, including .git-credentials 0600→0660.
    require(
        re.search(r"644\s*→\s*664|644->664", text),
        "evidence must measure 644→664 mode relaxation",
    )
    require(
        re.search(r"0600.{0,20}0660|600\s*→\s*660|600->660", text),
        "evidence must measure .git-credentials 0600→0660",
    )

    # Backup engines both green with non-zero stats.
    require(
        VOLSYNC_CEPH_SNAPSHOT in text,
        f"VolSync ceph snapshot {VOLSYNC_CEPH_SNAPSHOT}",
    )
    require(
        VOLSYNC_MINIO_SNAPSHOT in text,
        f"VolSync minio snapshot {VOLSYNC_MINIO_SNAPSHOT}",
    )
    require(
        PROCESSED_SIZE_MIB in text,
        f"VolSync processed size {PROCESSED_SIZE_MIB} MiB",
    )
    require(
        KOPIUR_CEPH_SNAPSHOT in text,
        f"kopiur ceph snapshot id {KOPIUR_CEPH_SNAPSHOT}",
    )
    require(
        re.search(rf"filesNew\D*{KOPIUR_FILES_NEW}", text)
        or re.search(rf"filesnew\D*{KOPIUR_FILES_NEW}", lowered),
        f"kopiur status.stats.filesNew {KOPIUR_FILES_NEW}",
    )
    require(
        str(KOPIUR_SIZE_BYTES) in text.replace(",", ""),
        f"kopiur status.stats.sizeBytes {KOPIUR_SIZE_BYTES}",
    )

    # Clone of NEW backup mounts cleanly - the direct disproof.
    require(
        NEW_RBD_DEVICE in text,
        f"new-backup clone device {NEW_RBD_DEVICE}",
    )
    require(
        re.search(r"zero\s+fsck|fsck-error-lines:\s*0|fsck errors", lowered),
        "new-backup clone must report zero fsck errors",
    )
    require(
        re.search(r"direct disproof|disproof of the original failure", lowered),
        "evidence must state the new clone is the direct disproof of the original failure",
    )

    # Alerts cleared; previously firing set named.
    require(
        re.search(r"0\s+active alerts|zero active alerts|0.*alerts matching", lowered),
        "evidence must record zero active opencode alerts",
    )
    for alert in (
        "VolSyncSyncStalledCeph",
        "VolSyncSyncStalledMinio",
        "VolSyncVolumeOutOfSync",
        "KubeJobNotCompleted",
        "KubeContainerWaiting",
    ):
        require(alert in text, f"evidence must name previously-firing alert {alert}")

    # Constraints honoured.
    require(
        re.search(r"never delete|not deleted|left.*failed", lowered)
        and re.search(r"kopiur", lowered)
        and re.search(r"snapshot", lowered),
        "evidence must record that the wedged kopiur Snapshot CR was not deleted",
    )
    require(
        re.search(r"final-pre-recreation|final.{0,20}volumesnapshot", lowered)
        and re.search(r"decoy|released during cleanup", lowered),
        "evidence must explain releasing the final pre-deletion VolumeSnapshot (decoy)",
    )
    require(
        re.search(r"cause.{0,40}unknown|still unknown", lowered),
        "evidence must leave root cause out of scope / unknown",
    )


def test_agents_findings() -> None:
    """Both fleet-wide findings must be first-class AGENTS.md NOTES entries."""
    require(AGENTS.is_file(), "AGENTS.md must exist")
    text = AGENTS.read_text()

    # Finding 1: empty latestImage / silent restore-nothing.
    require(
        re.search(
            r"\$\{APP\}-dst\.status\.latestImage.*frozen at first-deploy"
            r"|latestImage.*frozen at first-deploy",
            text,
            re.S,
        ),
        "AGENTS.md must document latestImage frozen at first-deploy",
    )
    require(
        "corrupt-claim-recreation-runbook.md" in text,
        "AGENTS.md must point at the recreation runbook",
    )
    require(
        "opencode-volume-recreation-2026-08-31.md" in text,
        "AGENTS.md must point at the measured evidence",
    )
    require(
        re.search(r"No eligible snapshots found", text)
        and re.search(r"No data will be restored", text),
        "AGENTS.md must quote the empty-repo mover log",
    )
    require(
        re.search(
            r"delete the `?ReplicationDestination`? \*together with\* the PVC"
            r"|delete the ReplicationDestination \*together with\* the PVC",
            text,
        ),
        "AGENTS.md must state the RD+PVC delete fix",
    )
    require(
        re.search(r"never to patch `?spec\.trigger\.manual`?", text, re.I)
        or re.search(r"never to patch spec.trigger.manual", text),
        "AGENTS.md must forbid patching trigger.manual",
    )
    require(
        re.search(r"re-stages a new clone within seconds", text),
        "AGENTS.md must warn that VolSync restages clones within seconds of Job delete",
    )
    require(
        re.search(r"concurrencyPolicy:\s*Forbid", text)
        or re.search(r"concurrencyPolicy.*Forbid", text),
        "AGENTS.md must warn that a Running kopiur Snapshot blocks later backups",
    )

    # Finding 2: VolSync restore widens permissions.
    require(
        re.search(
            r"VolSync restore permanently relaxes every file mode"
            r"|permanently relaxes every file mode by one group-write bit",
            text,
        ),
        "AGENTS.md must document VolSync mode relaxation as its own finding",
    )
    require(
        re.search(r"644→664|644->664", text) and re.search(r"600→660|600->660", text),
        "AGENTS.md must list the mode-relaxation mapping including 600→660",
    )
    require(
        re.search(r"\.git-credentials", text),
        "AGENTS.md mode-relaxation finding must name .git-credentials",
    )
    require(
        re.search(r"kopiur restores.*read-only|stage read-only", text, re.I)
        or re.search(r"kopiur restores, which stage read-only", text),
        "AGENTS.md must contrast kopiur read-only restores preserving modes",
    )


def test_no_credential_contents_in_diff_paths() -> None:
    """No credential file contents in any committed recreation artifact."""
    for path in (RUNBOOK, EVIDENCE, AGENTS):
        text = path.read_text()
        # Reject base64-ish long tokens next to git-credentials context.
        for m in re.finditer(r".{0,80}git-credentials.{0,120}", text, re.I):
            window = m.group(0)
            require(
                not re.search(r"https?://[^:\s]+:[^@\s]+@", window),
                f"{path.name} appears to embed a git-credentials URL with userinfo",
            )
            require(
                not re.search(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}", window),
                f"{path.name} appears to embed a GitHub token near git-credentials",
            )


def main() -> int:
    tests = [
        ("opencode_overlay_pins", test_opencode_overlay_pins),
        ("rendered_empty_latestimage_trap", None),  # filled below with sub
        ("ceph_block_reclaim_delete", test_ceph_block_reclaim_delete),
        ("runbook_procedure_contract", test_runbook_procedure_contract),
        ("evidence_result_contract", test_evidence_result_contract),
        ("agents_findings", test_agents_findings),
        ("no_credential_contents", test_no_credential_contents_in_diff_paths),
    ]
    failed = 0
    passed = 0
    sub: dict[str, str] | None = None
    for name, fn in tests:
        try:
            if name == "opencode_overlay_pins":
                sub = fn()  # type: ignore[misc]
                print(f"[PASS] {name}")
                passed += 1
            elif name == "rendered_empty_latestimage_trap":
                require(sub is not None, "overlay pins must run first")
                test_rendered_empty_latestimage_trap(sub)
                print(f"[PASS] {name}")
                passed += 1
            else:
                assert fn is not None
                fn()
                print(f"[PASS] {name}")
                passed += 1
        except Failure as e:
            print(f"[FAIL] {name}: {e}")
            failed += 1
        except Exception:
            print(f"[FAIL] {name}: unhandled error:")
            import traceback

            traceback.print_exc()
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
