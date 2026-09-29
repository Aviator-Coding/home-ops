#!/usr/bin/env python3
"""Semantic regression test for the corrupt-claim recreation contract.

Pins the 2026-08-31 ai/opencode volume recreation findings that live in:

  - docs/backups/corrupt-claim-recreation-runbook.md  (durable procedure)
  - docs/backups/corrupt-claim-recreation-runbook.md  (measured evidence)
  - AGENTS.md  (two fleet-wide findings)

THE CENTRAL CONTRACT: deleting a PVC and letting Flux recreate it from
dataSourceRef does NOT restore from the restic repository. The populator
clones ${APP}-dst.status.latestImage, and ${APP}-dst is trigger manual
restore-once + ssa IfNotPresent, so it runs exactly once at first deploy.
On a newly-onboarded app that one run can leave latestImage as a snapshot
of an EMPTY volume forever. The fix is to delete the ReplicationDestination
TOGETHER WITH the PVC so restore-once fires against the populated repo.

VolSync was RETIRED from ai/opencode on 2026-09-04 (Stage 5 wave three, tier B
- docs/backups/kopiur-wave-three-retirement-2026-09-04.md), so that overlay is
no longer a live example of the trap. NOTHING here was dropped, and the trap is
NOT obsolete: it is a property of `components/volsync`, which still protects
three claims (selfhosted/paperless-ngx, paperless-ngx-media, syncthing-data),
and the runbook still applies to every one of them. Two things changed:

  - The component render moved onto `selfhosted/paperless-ngx`, the permanent
    dual-engine carve-out, so the shape is asserted against a claim that is
    actually still exposed to it rather than against a historical one.
  - The opencode overlay pin INVERTED: it now asserts the retirement, and that
    the capacity the 2026-08-31 recreation provisioned survived the engine swap
    into KOPIUR_CAPACITY. Retirement is what finally removes this trap from
    opencode - a rebuilt claim is now populated from a kopiur `Restore`, which
    resolves a snapshot at restore time, instead of from a
    ReplicationDestination whose `latestImage` was pinned at first deploy.

The measured evidence (documentary contract, below) is historical fact and is
asserted unchanged.

This test does NOT grep implementation source as its evidence. It:

  1. Renders the real volsync Component kustomize build Flux would apply for
     selfhosted/paperless-ngx (postBuild.substitute taken from the live
     overlay), then parses the resulting objects into a typed structure.
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
EVIDENCE = ROOT / "docs/backups/corrupt-claim-recreation-runbook.md"
# The one file that must carry both fleet-wide findings. Retargeting them to a
# skill (planned: volsync-carveouts) is a one-line change here.
FINDINGS_DOC = ROOT / ".agents/skills/volsync-carveouts/SKILL.md"
VOLSYNC_DRILL = ROOT / "docs/backups/restore-drill-2026-08-23.md"
KOPIUR_DRILL = ROOT / "docs/backups/kopiur-restore-drill-2026-08-30.md"

# Live opencode overlay pins (source of truth at render time). VolSync retired
# 2026-09-04, so the VOLSYNC_* schedule pins went with the Component that read
# them; the capacity the 2026-08-31 recreation actually provisioned lives on
# KOPIUR_CAPACITY now and is still pinned, because that is the number a future
# rebuild would use.
OPENCODE_APP = "opencode"
OPENCODE_NS = "ai"
OPENCODE_CAPACITY = "20Gi"
OPENCODE_CACHE = "5Gi"
OPENCODE_KOPIUR_R2 = "H 19 * * *"

# The claim the volsync-Component shape is rendered against. It must be one
# that STILL runs VolSync, or this test would be asserting the trap on a claim
# that can no longer hit it. selfhosted/paperless-ngx is the deliberate
# permanent dual-engine carve-out (irreplaceable scanned documents), so it is
# the most stable choice in the fleet; the other two survivors
# (paperless-ngx-media, syncthing-data) are "not ready yet" rather than "never",
# and could be retired by a later decision.
TRAP_APP = "paperless-ngx"
TRAP_NS = "selfhosted"
TRAP_OVERLAY = ROOT / "kubernetes/apps/main/selfhosted/paperless-ngx.yaml"
TRAP_CAPACITY = "10Gi"

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


def overlay_substitute(path: Path, name: str | None = None) -> dict[str, str]:
    """postBuild.substitute of one Flux Kustomization in an overlay file.

    `name` selects it when the file carries several - which paperless-ngx does,
    because its second claim (`paperless-ngx-media`) and that claim's kopiur
    half each need their own substitute map. Omitting `name` keeps the original
    single-document requirement, so a file that unexpectedly grows a second
    Kustomization still fails loudly instead of silently picking the first.
    """
    docs = load_multi(path)
    flux = [d for d in docs if d.get("kind") == "Kustomization"]
    require(flux, f"{path.name} has no Flux Kustomization")
    if name is None:
        require(len(docs) == 1, f"{path.name} must be one document, got {len(docs)}")
        ks = docs[0]
        require(ks.get("kind") == "Kustomization", f"{path.name} must be a Flux Kustomization")
    else:
        matches = [d for d in flux if (d.get("metadata") or {}).get("name") == name]
        require(
            len(matches) == 1,
            f"{path.name}: expected exactly 1 Kustomization named {name!r}, got {len(matches)}",
        )
        ks = matches[0]
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


def test_opencode_overlay_pins() -> None:
    """ai/opencode is now kopiur-only, and kept the capacity it was recreated at.

    Inverted 2026-09-04 (Stage 5 wave three). The original assertion was "still
    includes components/volsync (claim is component-owned)" - the claim IS still
    component-owned, just by the other component now, and getting that swap
    wrong is the one mistake that deletes this volume outright. So the pin is
    strengthened rather than dropped: both halves of the swap are required.
    """
    docs = load_multi(OPENCODE_OVERLAY)
    ks = docs[0]
    require(ks.get("metadata", {}).get("name") == OPENCODE_APP, "overlay name is opencode")
    require(ks.get("metadata", {}).get("namespace") == OPENCODE_NS, "overlay ns is ai")
    components = [str(c).rstrip("/") for c in (ks.get("spec", {}).get("components") or [])]
    require(
        not any(c.endswith("components/volsync") for c in components),
        "opencode must NOT include components/volsync - VolSync was retired from this claim "
        "on 2026-09-04 and kopiur is its only engine",
    )
    require(
        any(c.endswith("components/kopiur") for c in components),
        "opencode must include components/kopiur",
    )
    require(
        any(c.endswith("components/kopiur/pvc") for c in components),
        "opencode must include components/kopiur/pvc: components/volsync/pvc.yaml was the only "
        "manifest emitting this claim and the overlay runs prune: true, so dropping it without "
        "this replacement makes Flux DELETE the volume that the 2026-08-31 runbook recreated",
    )
    sub = overlay_substitute(OPENCODE_OVERLAY)
    require(sub.get("APP") == OPENCODE_APP, f"APP must be opencode, got {sub.get('APP')!r}")
    leftover = sorted(k for k in sub if k.startswith("VOLSYNC_"))
    require(not leftover, f"VOLSYNC_* keys survived opencode's retirement: {leftover}")
    # The capacity the recreation actually provisioned. It moved from
    # VOLSYNC_CAPACITY to KOPIUR_CAPACITY with the engine, and it is still the
    # number a future rebuild would use - under ssa: IfNotPresent it is
    # create-time-only, which is exactly what makes a wrong value dangerous
    # rather than merely untidy.
    require(
        sub.get("KOPIUR_CAPACITY") == OPENCODE_CAPACITY,
        f"KOPIUR_CAPACITY must be {OPENCODE_CAPACITY} (the recreated claim), got "
        f"{sub.get('KOPIUR_CAPACITY')!r}",
    )
    require(
        sub.get("KOPIUR_CACHE_CAPACITY") == OPENCODE_CACHE,
        f"KOPIUR_CACHE_CAPACITY must be {OPENCODE_CACHE}, got "
        f"{sub.get('KOPIUR_CACHE_CAPACITY')!r}",
    )
    require(sub.get("KOPIUR_SCHEDULE_R2") == OPENCODE_KOPIUR_R2, "kopiur r2 hour 19 pin")
    # No identity override - measured 1000:1000, component default.
    require(
        "KOPIUR_PUID" not in sub and "VOLSYNC_PUID" not in sub,
        "opencode must keep the default 1000:1000 mover identity (measured)",
    )


def trap_subject_substitute() -> dict[str, str]:
    """The still-dual-engine claim the volsync-Component shape is asserted on."""
    docs = load_multi(TRAP_OVERLAY)
    ks = next(
        d
        for d in docs
        if d.get("kind") == "Kustomization" and d.get("metadata", {}).get("name") == TRAP_APP
    )
    require(ks.get("metadata", {}).get("namespace") == TRAP_NS, f"{TRAP_APP} ns is {TRAP_NS}")
    components = [str(c).rstrip("/") for c in (ks.get("spec", {}).get("components") or [])]
    require(
        any(c.endswith("components/volsync") for c in components),
        f"{TRAP_NS}/{TRAP_APP} must still include components/volsync - this test renders the "
        f"empty-latestImage trap against it, and a subject that has been retired cannot hit it. "
        f"If this claim is ever retired, repoint TRAP_APP at another live dual-engine claim "
        f"(RETIRED_CLAIMS in kopiur-stage3-test.py names the ones that are not).",
    )
    sub = dict(overlay_substitute(TRAP_OVERLAY, TRAP_APP))
    require(sub.get("APP") == TRAP_APP, f"APP must be {TRAP_APP}, got {sub.get('APP')!r}")
    require(
        sub.get("VOLSYNC_CAPACITY") == TRAP_CAPACITY,
        f"VOLSYNC_CAPACITY must be {TRAP_CAPACITY}, got {sub.get('VOLSYNC_CAPACITY')!r}",
    )
    # cluster-secrets keys are substituteFrom, not inline. Provide a stand-in so
    # the ExternalSecret RESTIC_REPOSITORY template can resolve for the render.
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
      - Three ReplicationSources (ceph/minio/r2) still exist - the subject is
        a dual-engine claim and recreation must not retire VolSync from it.
    """
    docs = render_with_substitute(VOLSYNC_COMPONENT, sub)

    pvcs = by_kind(docs, "PersistentVolumeClaim")
    require(len(pvcs) == 1, f"expected exactly 1 PVC from component, got {len(pvcs)}")
    pvc = pvcs[0]
    require(pvc.get("metadata", {}).get("name") == TRAP_APP, f"PVC named {TRAP_APP}")
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
        dsr.get("name") == f"{TRAP_APP}-dst",
        f"PVC dataSourceRef.name must be {TRAP_APP}-dst, got {dsr.get('name')!r}",
    )
    # The PVC itself has no restic configuration - restore path is only via RD.
    require(
        "restic" not in (pvc.get("spec") or {}),
        "PVC must not carry restic config; populator reads latestImage only",
    )
    storage = ((pvc.get("spec") or {}).get("resources") or {}).get("requests") or {}
    require(
        storage.get("storage") == TRAP_CAPACITY,
        f"PVC capacity must be {TRAP_CAPACITY}, got {storage.get('storage')!r}",
    )
    require(
        (pvc.get("spec") or {}).get("storageClassName") == "ceph-block",
        "PVC storageClassName must be ceph-block",
    )

    rds = by_kind(docs, "ReplicationDestination")
    require(len(rds) == 1, f"expected exactly 1 ReplicationDestination, got {len(rds)}")
    rd = rds[0]
    require(rd.get("metadata", {}).get("name") == f"{TRAP_APP}-dst", "RD name")
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
        restic.get("repository") == f"{TRAP_APP}-volsync-ceph-secret",
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
        restic.get("capacity") == TRAP_CAPACITY,
        f"RD capacity must match claim {TRAP_CAPACITY}",
    )

    sources = by_kind(docs, "ReplicationSource")
    src_names = sorted(s.get("metadata", {}).get("name") for s in sources)
    require(
        src_names
        == [
            f"{TRAP_APP}-ceph",
            f"{TRAP_APP}-minio",
            f"{TRAP_APP}-r2",
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


def test_recreation_docs_exist() -> None:
    """The recreation runbook, its sibling drills and the VolSync skill stay in the tree."""
    for path in (RUNBOOK, VOLSYNC_DRILL, KOPIUR_DRILL, FINDINGS_DOC):
        require(path.is_file(), f"missing {path.relative_to(ROOT)}")


def test_no_credential_contents_in_diff_paths() -> None:
    """No credential file contents in any committed recreation artifact."""
    for path in (RUNBOOK, EVIDENCE, FINDINGS_DOC):
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
        ("rendered_empty_latestimage_trap", None),  # filled below with the trap subject
        ("ceph_block_reclaim_delete", test_ceph_block_reclaim_delete),
        ("recreation_docs_exist", test_recreation_docs_exist),
        ("no_credential_contents", test_no_credential_contents_in_diff_paths),
    ]
    failed = 0
    passed = 0
    sub: dict[str, str] | None = None
    for name, fn in tests:
        try:
            if name == "rendered_empty_latestimage_trap":
                sub = trap_subject_substitute()
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
