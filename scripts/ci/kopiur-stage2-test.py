#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 2 (fidelity subject + restore drill).

Stage 2 is the migration acceptance gate: prove a restore from BOTH kopiur
destinations (ceph and r2) on a volume that actually holds data, and record the
drill. Captain decision `kopiur-stage2-empty-pilot` authorised ONE additional
volume - downloads/sabnzbd-config - because the Stage 1 pilot (autobrr) holds
zero files. Stage 3 (2026-08-30) has since onboarded the rest of the fleet;
this file still pins the Stage 2 fidelity contract, and the fleet-wide
coverage set now lives in kopiur-stage3-test.py.

UPDATED 2026-09-01 for Stage 5. `downloads/sabnzbd-config` was one of the four
pilot volumes VolSync was RETIRED from, so the assertions that used to demand a
live VolSync sibling here now demand its absence, plus the thing that replaced
it: the kopiur pvc Component. The Stage 2 fidelity contract itself is unchanged
- this claim being the most-proven volume in the fleet is exactly why it led
the pilot. Retired-set coverage lives in kopiur-stage5-test.py.

This test does not grep source text as its evidence. It:
  1. Renders the real kustomize builds Flux would apply for the kopiur backup
     and kopiur pvc components under the PRODUCTION sabnzbd substitute map
     parsed from downloads/sabnzbd.yaml (KOPIUR_CLAIM=sabnzbd-config,
     KOPIUR_PUID/PGID=2000, KOPIUR_SCHEDULE_R2='H 11 * * *'). A separate
     negative-control render pins the component defaults (uid/gid 1000,
     r2 'H 4 * * *') when those overrides are omitted.
  2. Parses the sabnzbd Flux overlay, every apps/main overlay's components
     list, the sabnzbd workload securityContext, and the Stage 2 drill
     document into structured objects / measured fields.
  3. Asserts the Stage 2 safety contract on those objects:
       - both Stage 2 volumes stay onboarded (autobrr + sabnzbd); the exact
         fleet set is pinned by kopiur-stage3-test.py
       - sabnzbd components are kopiur THEN kopiur/pvc, no volsync, no
         VOLSYNC_* substitute key, and no volsync/system dependency
       - the claim SURVIVES retirement: the kopiur pvc Component still emits
         `sabnzbd-config`, carrying `ssa: IfNotPresent` (dataSourceRef is
         immutable on a bound claim) and never the `force` label (which would
         resolve that conflict by deleting the data volume)
       - rendered SnapshotPolicy PVC name is sabnzbd-config (claim override)
       - rendered mover podSecurityContext is uid/gid/fsGroup 2000 (finding 2)
       - sabnzbd workload securityContext is 2000 (the reason the override
         is load-bearing); production autobrr also renders at 2000 from its
         live overlay (Stage 3 identity correction)
       - ceph schedule stays at the component default; production r2 is the
         downloads free hour 'H 11 * * *' (no hand-assigned minute)
       - KOPIUR_CACHE_CAPACITY is raised to 10Gi (restore-proof finding 2: an
         r2 restore needs materially more kopia cache than a ceph one, and a
         failed Restore is terminal)
       - dependsOn includes kopiur-repository, and NOT volsync (retired) nor
         kopiur-credentials (credential-scope 2026-08-30 replaced the standing
         per-namespace copies with operator-minted per-run projection; the
         three projection legs are pinned by kopiur-stage1-test.py)
       - drill document records: Stage 2 PASS, both-destination identical
         sha256 digest, finding 1 (empty pilot / .status.stats), finding 2
         (mover identity), proved VolSync simultaneity with the observed
         post-kopiur lastSync times, and the hard-constraint language

Live Snapshot/Restore Succeeded and cluster byte compares remain post-merge /
operator gates already executed in the drill; this pins the GitOps + drill
contract that must hold before merge.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
KOPIUR_BACKUP = ROOT / "kubernetes/components/kopiur/backup"
KOPIUR_PVC = ROOT / "kubernetes/components/kopiur/pvc"
SABNZBD_OVERLAY = ROOT / "kubernetes/apps/main/downloads/sabnzbd.yaml"
AUTOBRR_OVERLAY = ROOT / "kubernetes/apps/main/downloads/autobrr.yaml"
SABNZBD_HR = ROOT / "kubernetes/apps/base/downloads/sabnzbd/app/helmrelease.yaml"
AUTOBRR_HR = ROOT / "kubernetes/apps/base/downloads/autobrr/app/helmrelease.yaml"
APPS_MAIN = ROOT / "kubernetes/apps/main"
DRILL_DOC = ROOT / "docs/backups/kopiur-restore-drill-2026-08-30.md"
VOLSYNC_DRILL = ROOT / "docs/backups/restore-drill-2026-08-23.md"
COMPONENT_README = ROOT / "kubernetes/components/kopiur/Readme.md"
STAGE0_README = ROOT / "kubernetes/apps/base/system/kopiur/README.md"
# The Stage 2 documentary facts moved out of AGENTS.md on 2026-09-01 into the
# JIT-loaded kopiur skill, which is now the operator-facing owner of that depth.
# AGENTS.md keeps only the data-loss tripwires plus a pointer, so pinning this
# contract there would re-grow exactly what the relocation removed.
KOPIUR_SKILL = ROOT / ".claude" / "skills" / "kopiur-backups" / "SKILL.md"

STAGE2_APP = "sabnzbd"
STAGE2_NS = "downloads"
STAGE2_CLAIM = "sabnzbd-config"
STAGE2_PUID = 2000
STAGE2_PGID = 2000

STAGE1_APP = "autobrr"
STAGE1_NS = "downloads"

# Live claim size, now carried by KOPIUR_CAPACITY (the kopiur pvc Component
# emits the PVC since VolSync was retired from this volume on 2026-09-01).
STAGE2_CAPACITY = "5Gi"
# Raised from the 2Gi component default on retirement - restore-proof
# finding 2 (docs/backups/kopiur-restore-proof-2026-09-01.md).
STAGE2_CACHE_CAPACITY = "10Gi"

# Production ceph stays on the component default (structurally offset).
# Production r2 is the downloads free hour from Stage 3.
PRODUCTION_KOPIUR_CEPH_CRON = "H 1-23/4 * * *"
PRODUCTION_KOPIUR_R2_CRON = "H 11 * * *"

# VolSync component defaults (kubernetes/components/volsync/*/replicationsource.yaml).
# The engine stagger is measured against these now that this claim runs no
# VolSync of its own - same constants kopiur-timezone-test.py uses.
COMPONENT_DEFAULT_VOLSYNC_CEPH_CRON = "0 */4 * * *"
COMPONENT_DEFAULT_VOLSYNC_R2_CRON = "0 2 * * *"

# Component defaults - pinned only by the explicit negative-control renders.
COMPONENT_DEFAULT_KOPIUR_CEPH_CRON = "H 1-23/4 * * *"
COMPONENT_DEFAULT_KOPIUR_R2_CRON = "H 4 * * *"
COMPONENT_DEFAULT_PUID = 1000
COMPONENT_DEFAULT_PGID = 1000

# Production autobrr identity (Stage 3 correction; matches measured ownership).
STAGE1_PUID = 2000
STAGE1_PGID = 2000

# Measured Stage 2 fidelity digest from the live drill (public result contract).
STAGE2_MANIFEST_DIGEST = (
    "5f748bb724937dabd5c5030135c772d50a6056b38221fcc3dd04356fdb5b4e6f"
)
STAGE2_FILE_COUNT = 2062
STAGE2_BYTE_COUNT = 2208506538

# Observed VolSync lastSyncTimes after kopiur snapshots (simultaneity proved).
OBSERVED_SAB_CEPH_LASTSYNC = "2026-08-30T20:31:24Z"
OBSERVED_AUTOBRR_CEPH_LASTSYNC = "2026-08-30T20:46:07Z"
KOPIUR_SAB_CEPH_SNAPSHOT = "2026-08-30T19:45:46Z"
KOPIUR_AUTOBRR_CEPH_SNAPSHOT = "2026-08-30T18:53:22Z"

STAGE2_ONBOARDED = frozenset(
    {
        (STAGE1_NS, STAGE1_APP),
        (STAGE2_NS, STAGE2_APP),
    }
)


class Failure(Exception):
    pass


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_multi(path: Path) -> list[dict[str, Any]]:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    if not docs:
        raise Failure(f"{path} produced no documents")
    return docs


def by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == kind]


def flux_envsubst(text: str, env: dict[str, str]) -> str:
    """Flux-shaped ${VAR} / ${VAR:-default} substitution, including nesting."""

    def lookup(key: str) -> str | None:
        if key in env and env[key] not in (None, ""):
            return env[key]
        return None

    def expand(s: str) -> str:
        out: list[str] = []
        i = 0
        n = len(s)
        while i < n:
            if s[i] != "$" or i + 1 >= n or s[i + 1] != "{":
                out.append(s[i])
                i += 1
                continue
            depth = 1
            j = i + 2
            while j < n and depth:
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                j += 1
            if depth != 0:
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
    """Parse postBuild.substitute from a live Flux Kustomization overlay."""
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
        cmd = [kustomize, "build", str(path)]
    else:
        kubectl = _which("kubectl")
        if not kubectl:
            raise Failure("neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(path)]
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


def cron_hours(expr: str) -> set[int]:
    parts = expr.split()
    require(len(parts) == 5, f"expected 5-field cron, got {expr!r}")
    hour = parts[1]
    out: set[int] = set()
    if hour == "*":
        return set(range(24))
    for piece in hour.split(","):
        step = 1
        body = piece
        if "/" in piece:
            body, step_s = piece.split("/", 1)
            step = int(step_s)
        if body == "*":
            start, end = 0, 23
        elif "-" in body:
            a, b = body.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(body)
        out.update(range(start, end + 1, step))
    return out


def flux_overlays_with_kopiur() -> list[tuple[str, str, Path]]:
    """Return (ns, name, path) for every Flux Kustomization that includes components/kopiur."""
    found: list[tuple[str, str, Path]] = []
    for path in sorted(APPS_MAIN.rglob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        try:
            docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
        except yaml.YAMLError:
            continue
        for d in docs:
            if d.get("kind") != "Kustomization":
                continue
            if d.get("apiVersion", "").startswith("kustomize.config.k8s.io"):
                continue
            components = d.get("spec", {}).get("components") or []
            for c in components:
                if isinstance(c, str) and c.rstrip("/").endswith("components/kopiur"):
                    name = d.get("metadata", {}).get("name", path.name)
                    ns = d.get("metadata", {}).get("namespace", "?")
                    found.append((ns, name, path))
    return found


def workload_security_context(hr_path: Path) -> dict[str, Any]:
    """Extract the app pod/container securityContext numbers from a HelmRelease values tree."""
    docs = load_multi(hr_path)
    hr = next(d for d in docs if d.get("kind") == "HelmRelease")
    values = hr.get("spec", {}).get("values") or {}

    # app-template chart: controllers.*.pod.securityContext and containers.*.securityContext
    controllers = values.get("controllers") or {}
    pod_sc: dict[str, Any] = {}
    ctr_sc: dict[str, Any] = {}
    for ctrl in controllers.values():
        if not isinstance(ctrl, dict):
            continue
        pod = ctrl.get("pod") or {}
        if isinstance(pod, dict) and pod.get("securityContext"):
            pod_sc = pod["securityContext"]
        containers = ctrl.get("containers") or {}
        if isinstance(containers, dict):
            for c in containers.values():
                if isinstance(c, dict) and c.get("securityContext"):
                    ctr_sc = c["securityContext"]
                    break
        # Some charts nest default container under controllers.main.containers.app
        if pod_sc or ctr_sc:
            break

    # Fallback: top-level pod/container securityContext (older shapes).
    if not pod_sc:
        pod_sc = (values.get("pod") or {}).get("securityContext") or values.get(
            "podSecurityContext"
        ) or {}
    if not ctr_sc:
        ctr_sc = (values.get("securityContext") or {}) if isinstance(
            values.get("securityContext"), dict
        ) else {}

    return {"pod": pod_sc or {}, "container": ctr_sc or {}}


def test_stage2_volumes_still_onboarded() -> None:
    """Both Stage 2 pilots remain on kopiur.

    This asserted an EXACT two-volume set until Stage 3 (2026-08-30), which
    deliberately onboarded the rest of the fleet. Freezing the fleet at two is
    no longer the invariant; what Stage 2 still owns is that its own two
    volumes - the restore-proof subject `sabnzbd-config` and the original pilot
    `autobrr` - are never quietly dropped. The exact Stage 3 coverage set,
    including the two volumes deliberately left off, is pinned by
    kopiur-stage3-test.py.
    """
    onboarded = flux_overlays_with_kopiur()
    keys = {(ns, name) for ns, name, _ in onboarded}
    missing = STAGE2_ONBOARDED - keys
    require(
        not missing,
        f"Stage 2 volumes must stay onboarded; missing {sorted(f'{n}/{a}' for n, a in missing)}",
    )
    paths = {p.resolve() for _, _, p in onboarded}
    for required in (AUTOBRR_OVERLAY.resolve(), SABNZBD_OVERLAY.resolve()):
        require(
            required in paths,
            f"{required.name} must still be a kopiur-onboarded overlay",
        )


def test_sabnzbd_overlay_wiring() -> None:
    docs = load_multi(SABNZBD_OVERLAY)
    require(len(docs) == 1, f"sabnzbd overlay must be one Kustomization, got {len(docs)}")
    ks = docs[0]
    require(ks.get("kind") == "Kustomization", "sabnzbd overlay kind")
    require(ks["metadata"]["name"] == STAGE2_APP, "sabnzbd metadata.name")
    require(ks["metadata"]["namespace"] == STAGE2_NS, "sabnzbd metadata.namespace")
    spec = ks["spec"]
    require(spec.get("targetNamespace") == STAGE2_NS, "targetNamespace")
    require(spec.get("prune") is True, "prune must stay true")
    require(spec.get("wait") is False, "wait must be false")

    components = spec.get("components") or []
    require(
        components
        == [
            "../../../../../components/kopiur",
            "../../../../../components/kopiur/pvc",
        ],
        f"components must be kopiur THEN kopiur/pvc (volsync retired 2026-09-01, "
        f"Stage 5 pilot), got {components}",
    )

    deps = {(d.get("name"), d.get("namespace")) for d in (spec.get("dependsOn") or [])}
    # The volsync operator dependency went with the volsync Component. Nothing
    # in this Kustomization renders a VolSync object any more, so a dependency
    # on it would be a wait on an unrelated app - and a reappearing one is the
    # signature of a half-reverted retirement.
    require(
        ("volsync", "system") not in deps,
        f"must NOT dependOn volsync/system after retirement, got {deps}",
    )
    require(
        ("kopiur-repository", "system") in deps,
        f"must dependOn kopiur-repository/system, got {deps}",
    )
    # Credentials are projected per run - no credential dependency, and a
    # reappearing one means the retired standing-copy shape is back.
    require(
        not any(n == "kopiur-credentials" for n, _ in deps),
        f"must NOT dependOn kopiur-credentials (projection replaced it), got {deps}",
    )

    sub = ((spec.get("postBuild") or {}).get("substitute")) or {}
    require(sub.get("APP") == STAGE2_APP, f"APP must be {STAGE2_APP}, got {sub.get('APP')!r}")
    require(
        sub.get("KOPIUR_CLAIM") == STAGE2_CLAIM,
        f"KOPIUR_CLAIM must override to {STAGE2_CLAIM!r} (claim != app name), "
        f"got {sub.get('KOPIUR_CLAIM')!r}",
    )
    # Every VOLSYNC_* key went with the Component. A leftover key is dead
    # weight at best (nothing reads it) and, on the claim variables, an active
    # lie about what a rebuild would provision - the exact dead-substitute
    # shape the selfhosted APP_UID/APP_GID cleanup removed fleet-wide.
    leftover = sorted(k for k in sub if k.startswith("VOLSYNC_"))
    require(
        not leftover,
        f"no VOLSYNC_* substitute key may survive retirement, got {leftover}",
    )
    # The claim's size is now carried by KOPIUR_CAPACITY, because the kopiur
    # pvc Component is what emits the PVC. It must match the live claim: under
    # `ssa: IfNotPresent` it is create-time-only, so it is silently untested
    # until the day someone rebuilds the claim.
    require(
        sub.get("KOPIUR_CAPACITY") == STAGE2_CAPACITY,
        f"KOPIUR_CAPACITY must be {STAGE2_CAPACITY!r} (live claim size), "
        f"got {sub.get('KOPIUR_CAPACITY')!r}",
    )
    # Restore-proof finding 2: an r2 restore needs materially more kopia cache
    # than the same restore from ceph, and a failed Restore is terminal. This
    # is the only pilot volume near the measured danger zone (2.06 GiB of data
    # against the 2Gi component default).
    require(
        sub.get("KOPIUR_CACHE_CAPACITY") == STAGE2_CACHE_CAPACITY,
        f"KOPIUR_CACHE_CAPACITY must be raised to {STAGE2_CACHE_CAPACITY!r} "
        f"(restore-proof finding 2), got {sub.get('KOPIUR_CACHE_CAPACITY')!r}",
    )
    # Finding 2: load-bearing mover identity.
    require(
        str(sub.get("KOPIUR_PUID")) == str(STAGE2_PUID),
        f"KOPIUR_PUID must be {STAGE2_PUID} (finding 2), got {sub.get('KOPIUR_PUID')!r}",
    )
    require(
        str(sub.get("KOPIUR_PGID")) == str(STAGE2_PGID),
        f"KOPIUR_PGID must be {STAGE2_PGID} (finding 2), got {sub.get('KOPIUR_PGID')!r}",
    )
    # Ceph stays at the component default (structurally offset). r2 carries the
    # downloads free hour Stage 3 assigns. A hand-assigned MINUTE stays forbidden.
    require(
        "KOPIUR_SCHEDULE_CEPH" not in sub,
        f"ceph schedule must stay at the component default; got {sorted(sub)}",
    )
    r2 = sub.get("KOPIUR_SCHEDULE_R2")
    require(
        r2 == PRODUCTION_KOPIUR_R2_CRON,
        f"KOPIUR_SCHEDULE_R2 must be {PRODUCTION_KOPIUR_R2_CRON!r}, got {r2!r}",
    )


def test_rendered_claim_override_and_puid(
    kopiur_docs: list[dict[str, Any]],
) -> None:
    policies = by_kind(kopiur_docs, "SnapshotPolicy")
    require(len(policies) == 2, f"expected 2 SnapshotPolicy, got {len(policies)}")
    pnames = sorted(p["metadata"]["name"] for p in policies)
    require(
        pnames == [f"{STAGE2_APP}-ceph", f"{STAGE2_APP}-r2"],
        f"policy names unexpected: {pnames}",
    )
    for p in policies:
        sources = p["spec"].get("sources") or []
        require(len(sources) == 1, f"{p['metadata']['name']}: one source required")
        claim = (sources[0].get("pvc") or {}).get("name")
        require(
            claim == STAGE2_CLAIM,
            f"{p['metadata']['name']}: pvc name must be {STAGE2_CLAIM!r} "
            f"(KOPIUR_CLAIM override), got {claim!r}",
        )
        psc = ((p["spec"].get("mover") or {}).get("podSecurityContext")) or {}
        require(
            psc.get("runAsUser") == STAGE2_PUID
            and psc.get("runAsGroup") == STAGE2_PGID
            and psc.get("fsGroup") == STAGE2_PGID,
            f"{p['metadata']['name']}: mover identity must be "
            f"{STAGE2_PUID}:{STAGE2_PGID} (finding 2), got {psc}",
        )
        # Defaults still pinned.
        require(p["spec"].get("copyMethod") == "Snapshot", "copyMethod Snapshot")
        deletion = p["spec"].get("deletion") or {}
        require(
            deletion.get("onPolicyDelete") == "Retain",
            f"{p['metadata']['name']}: onPolicyDelete Retain required, got {deletion}",
        )
        cache = ((p["spec"].get("mover") or {}).get("cache")) or {}
        require(cache.get("mode") == "Ephemeral", f"cache must stay Ephemeral, got {cache}")

    schedules = by_kind(kopiur_docs, "SnapshotSchedule")
    require(len(schedules) == 2, f"expected 2 SnapshotSchedule, got {len(schedules)}")
    by_name = {s["metadata"]["name"]: s for s in schedules}
    for dest, expected in (
        ("ceph", PRODUCTION_KOPIUR_CEPH_CRON),
        ("r2", PRODUCTION_KOPIUR_R2_CRON),
    ):
        cron = ((by_name[f"{STAGE2_APP}-{dest}"]["spec"].get("schedule")) or {}).get("cron")
        require(cron == expected, f"{dest}: cron must be {expected!r}, got {cron!r}")
        minute = cron.split()[0]
        require(minute == "H", f"{dest}: minute must be bare H, got {minute!r}")

    # Hour non-overlap with VolSync's cadence. sabnzbd itself no longer runs
    # VolSync (Stage 5 retirement), so this is checked against the VOLSYNC
    # COMPONENT DEFAULTS rather than this claim's retired per-app schedules:
    # 26 of the fleet's 30 claims still run both engines, and sabnzbd's kopiur
    # hours are drawn from that same fleet-wide stagger. Asserting against a
    # schedule this overlay no longer contains would be asserting nothing.
    k_ceph = cron_hours(PRODUCTION_KOPIUR_CEPH_CRON)
    v_ceph = cron_hours(COMPONENT_DEFAULT_VOLSYNC_CEPH_CRON)
    require(
        k_ceph.isdisjoint(v_ceph),
        f"kopiur ceph hours {sorted(k_ceph)} overlap volsync {sorted(v_ceph)}",
    )
    k_r2 = cron_hours(PRODUCTION_KOPIUR_R2_CRON)
    v_r2 = cron_hours(COMPONENT_DEFAULT_VOLSYNC_R2_CRON)
    require(
        k_r2.isdisjoint(v_r2),
        f"kopiur r2 hours {sorted(k_r2)} overlap volsync {sorted(v_r2)}",
    )
    require(k_r2 == {11}, f"production sabnzbd r2 hour must be 11, got {sorted(k_r2)}")

    restores = by_kind(kopiur_docs, "Restore")
    require(len(restores) == 1, f"expected 1 standing Restore, got {len(restores)}")
    r = restores[0]
    require(
        r["metadata"]["name"] == f"{STAGE2_APP}-kopiur-dst",
        f"standing Restore must be {STAGE2_APP}-kopiur-dst, got {r['metadata']['name']}",
    )
    # Standing restore stays passive; drills use target.pvc separately.
    target = r["spec"].get("target") or {}
    require(
        "populator" in target and target.get("pvc") is None,
        f"standing Restore must remain passive populator, got {target}",
    )


def test_volsync_retired_and_claim_survives(pvc_docs: list[dict[str, Any]]) -> None:
    """VolSync is gone from sabnzbd, and the claim did NOT go with it.

    This is the whole hazard of Stage 5 in one assertion. The volsync Component
    was the only manifest emitting `sabnzbd-config`, and the overlay runs
    `prune: true` - so dropping that Component without a replacement would have
    Flux delete the app's 2.06 GiB data volume as an ordinary garbage-collect,
    for a change that was only ever meant to remove a backup engine.
    """
    claims = by_kind(pvc_docs, "PersistentVolumeClaim")
    require(len(claims) == 1, f"kopiur pvc Component must emit exactly one PVC, got {len(claims)}")
    pvc = claims[0]
    require(
        pvc["metadata"]["name"] == STAGE2_CLAIM,
        f"PVC name must follow KOPIUR_CLAIM to {STAGE2_CLAIM!r}, "
        f"got {pvc['metadata']['name']!r}",
    )

    # ssa: IfNotPresent is load-bearing, not stylistic. `dataSourceRef` is
    # immutable on a bound claim (measured 2026-09-01: a server-side dry-run of
    # the swap returns `spec: Forbidden: spec is immutable after creation
    # except resources.requests and volumeAttributesClassName for bound
    # claims`). Without the label Flux would try the forbidden change on every
    # reconcile and the Kustomization would never go Ready again.
    labels = (pvc["metadata"].get("labels") or {})
    require(
        labels.get("kustomize.toolkit.fluxcd.io/ssa") == "IfNotPresent",
        "retired claim must carry ssa: IfNotPresent - dataSourceRef is immutable "
        f"on a bound PVC; got labels {labels}",
    )
    # `force: enabled` resolves an immutable-field conflict by DELETING and
    # recreating the object. On a PVC that is the data volume.
    require(
        labels.get("kustomize.toolkit.fluxcd.io/force") is None,
        f"retired claim must NEVER carry the force label (it would delete the "
        f"volume to resolve the immutable dataSourceRef), got labels {labels}",
    )

    dsr = pvc["spec"].get("dataSourceRef") or {}
    require(
        dsr.get("kind") == "Restore"
        and dsr.get("apiGroup") == "kopiur.home-operations.com"
        and dsr.get("name") == f"{STAGE2_APP}-kopiur-dst",
        f"rebuilt claim must be populated from the kopiur Restore, got {dsr}",
    )
    require(
        pvc["spec"]["resources"]["requests"]["storage"] == STAGE2_CAPACITY,
        "rendered claim capacity must match the live claim",
    )

    # And nothing VolSync-shaped may survive the render.
    stray = sorted(
        d["kind"] for d in pvc_docs if d.get("apiVersion", "").startswith("volsync.backube")
    )
    require(not stray, f"no VolSync object may render for sabnzbd after retirement, got {stray}")


def test_workload_identity_matches_override() -> None:
    """sabnzbd runs as 2000; that is why KOPIUR_PUID/PGID=2000 is required.

    Production autobrr is also 2000 since Stage 3 (measured file ownership).
    Both are rendered from their LIVE overlay substitute maps.
    """
    sab = workload_security_context(SABNZBD_HR)
    # Prefer pod-level fsGroup/runAsUser; fall back to container.
    sab_uid = sab["pod"].get("runAsUser") or sab["container"].get("runAsUser")
    sab_gid = (
        sab["pod"].get("fsGroup")
        or sab["pod"].get("runAsGroup")
        or sab["container"].get("runAsGroup")
    )
    require(
        sab_uid == STAGE2_PUID,
        f"sabnzbd workload runAsUser must be {STAGE2_PUID}, got pod={sab['pod']} "
        f"container={sab['container']}",
    )
    require(
        sab_gid == STAGE2_PGID,
        f"sabnzbd workload fsGroup/runAsGroup must be {STAGE2_PGID}, got pod={sab['pod']} "
        f"container={sab['container']}",
    )

    # Production autobrr: render with the REAL overlay substitute map.
    auto_env = {
        **overlay_substitute(AUTOBRR_OVERLAY),
        "SECRET_DOMAIN": "example.test",
    }
    auto_docs = render_with_substitute(KOPIUR_BACKUP, auto_env)
    for p in by_kind(auto_docs, "SnapshotPolicy"):
        psc = ((p["spec"].get("mover") or {}).get("podSecurityContext")) or {}
        require(
            psc.get("runAsUser") == STAGE1_PUID
            and psc.get("runAsGroup") == STAGE1_PGID
            and psc.get("fsGroup") == STAGE1_PGID,
            f"production autobrr mover must be {STAGE1_PUID}:{STAGE1_PGID} "
            f"from the live overlay, got {psc}",
        )
        claim = ((p["spec"].get("sources") or [{}])[0].get("pvc") or {}).get("name")
        require(
            claim == STAGE1_APP,
            f"autobrr claim must default to app name, got {claim!r}",
        )
    auto_schedules = {
        s["metadata"]["name"]: s for s in by_kind(auto_docs, "SnapshotSchedule")
    }
    auto_r2 = ((auto_schedules[f"{STAGE1_APP}-r2"]["spec"].get("schedule")) or {}).get(
        "cron"
    )
    require(
        auto_r2 == PRODUCTION_KOPIUR_R2_CRON,
        f"production autobrr r2 cron must be {PRODUCTION_KOPIUR_R2_CRON!r}, got {auto_r2!r}",
    )


def test_default_puid_without_override_is_1000() -> None:
    """Negative control: component default identity/schedules (NOT production).

    Synthetic env deliberately omits KOPIUR_PUID/PGID and KOPIUR_SCHEDULE_* so
    the component defaults stay pinned: mover 1000:1000, ceph
    'H 1-23/4 * * *', r2 'H 4 * * *'. Production sabnzbd/autobrr overlays
    override identity and r2; this proves those overrides do real work.
    """
    docs = render_with_substitute(
        KOPIUR_BACKUP,
        {
            # Synthetic env only - do not read a live overlay here.
            "APP": STAGE2_APP,
            "KOPIUR_CLAIM": STAGE2_CLAIM,
            # deliberately omit KOPIUR_PUID/PGID and KOPIUR_SCHEDULE_*
            "SECRET_DOMAIN": "example.test",
        },
    )
    for p in by_kind(docs, "SnapshotPolicy"):
        psc = ((p["spec"].get("mover") or {}).get("podSecurityContext")) or {}
        require(
            psc.get("runAsUser") == COMPONENT_DEFAULT_PUID
            and psc.get("runAsGroup") == COMPONENT_DEFAULT_PGID
            and psc.get("fsGroup") == COMPONENT_DEFAULT_PGID,
            f"without override, mover must default to "
            f"{COMPONENT_DEFAULT_PUID}:{COMPONENT_DEFAULT_PGID}, got {psc}",
        )
        claim = ((p["spec"].get("sources") or [{}])[0].get("pvc") or {}).get("name")
        require(claim == STAGE2_CLAIM, f"claim override must still apply, got {claim!r}")
    schedules = {s["metadata"]["name"]: s for s in by_kind(docs, "SnapshotSchedule")}
    for dest, expected in (
        ("ceph", COMPONENT_DEFAULT_KOPIUR_CEPH_CRON),
        ("r2", COMPONENT_DEFAULT_KOPIUR_R2_CRON),
    ):
        cron = ((schedules[f"{STAGE2_APP}-{dest}"]["spec"].get("schedule")) or {}).get("cron")
        require(
            cron == expected,
            f"component default {dest} cron must be {expected!r}, got {cron!r}",
        )


def test_drill_document_contract() -> None:
    """The drill doc is the Stage 2 public deliverable; pin its result contract.

    This is not a source-grep of implementation. The markdown file is the
    operator-facing result artifact the migration gate produces - the same
    class of owned text contract as a serialized protocol or snapshot. We
    parse measured fields out of it and assert the gate criteria.
    """
    require(DRILL_DOC.is_file(), f"missing drill document {DRILL_DOC.relative_to(ROOT)}")
    require(
        VOLSYNC_DRILL.is_file(),
        "VolSync sibling drill must remain (house standard this one is built to)",
    )
    text = DRILL_DOC.read_text()
    lowered = text.lower()

    # Result: Stage 2 passes, both destinations, sabnzbd-config subject.
    require(
        re.search(r"stage\s*2\s+pass", lowered),
        "drill must declare Stage 2 PASS",
    )
    require(
        "sabnzbd-config" in lowered,
        "drill fidelity subject must be sabnzbd-config",
    )
    require(
        re.search(r"\bceph\b", lowered) and re.search(r"\br2\b", lowered),
        "drill must cover both ceph and r2 destinations",
    )

    # Measured fidelity numbers (the acceptance gate).
    require(
        str(STAGE2_FILE_COUNT) in text,
        f"drill must record file count {STAGE2_FILE_COUNT}",
    )
    # Accept either space-grouped or plain integer forms of the byte count.
    byte_plain = str(STAGE2_BYTE_COUNT)
    byte_grouped = "2 208 506 538"
    require(
        byte_plain in text.replace(",", "") or byte_grouped in text,
        f"drill must record byte count {STAGE2_BYTE_COUNT}",
    )
    require(
        STAGE2_MANIFEST_DIGEST in text,
        f"drill must record the per-file sha256 manifest digest {STAGE2_MANIFEST_DIGEST}",
    )

    # Two findings must be first-class sections, not buried asides.
    require(
        re.search(r"^##\s+Finding 1\b", text, re.M),
        "Finding 1 must be a top-level section",
    )
    require(
        re.search(r"^##\s+Finding 2\b", text, re.M),
        "Finding 2 must be a top-level section",
    )
    require(
        ".status.stats" in text or "status.stats" in text,
        "Finding 1 mitigation must name .status.stats",
    )
    require(
        re.search(r"filesNew\D*0", text) and re.search(r"sizeBytes\D*0", text),
        "Finding 1 must record the empty-snapshot stats shape",
    )
    require(
        "KOPIUR_PUID" in text and "KOPIUR_PGID" in text,
        "Finding 2 must name KOPIUR_PUID/PGID",
    )
    require(
        re.search(r"permissiondenied|permission denied|fatal error", lowered),
        "Finding 2 must record the kopia PermissionDenied / fatal failure mode",
    )
    require(
        re.search(r"stage\s*3", lowered)
        and ("rollout prerequisite" in lowered or "prerequisite for every" in lowered),
        "Finding 2 must call out Stage 3 rollout prerequisite",
    )
    require(
        "readonly" in lowered.replace("-", "").replace(" ", "")
        or "read only" in lowered
        or "readOnly" in text,
        "Finding 2 must explain kopiur's readOnly staged source vs VolSync writable",
    )

    # Hard constraint (binding from the VolSync sibling).
    require(
        re.search(r"hard constraint", lowered),
        "drill must carry the hard-constraint section",
    )
    require(
        "onmissingsnapshot" in lowered.replace(" ", "")
        or "onMissingSnapshot" in text,
        "drill must require onMissingSnapshot: Fail for drill Restores",
    )
    require(
        "target.pvc" in lowered or "target.pvc" in text.lower(),
        "drill must require Restore.spec.target.pvc (never live claim / pvcRef)",
    )
    require(
        "just kube restore" in lowered or "`just kube restore`" in text,
        "drill must forbid the in-place just kube restore recipe",
    )

    # Proved VolSync simultaneity - observed lastSync after kopiur snapshots.
    require(
        OBSERVED_SAB_CEPH_LASTSYNC in text,
        f"drill must record sabnzbd-ceph lastSync {OBSERVED_SAB_CEPH_LASTSYNC}",
    )
    require(
        OBSERVED_AUTOBRR_CEPH_LASTSYNC in text,
        f"drill must record autobrr-ceph lastSync {OBSERVED_AUTOBRR_CEPH_LASTSYNC}",
    )
    require(
        KOPIUR_SAB_CEPH_SNAPSHOT in text or "19:45:46Z" in text,
        f"drill must record sabnzbd kopiur snapshot time {KOPIUR_SAB_CEPH_SNAPSHOT}",
    )
    require(
        KOPIUR_AUTOBRR_CEPH_SNAPSHOT in text or "18:53:22Z" in text,
        f"drill must record autobrr kopiur snapshot time {KOPIUR_AUTOBRR_CEPH_SNAPSHOT}",
    )
    require(
        re.search(r"simultane", lowered),
        "drill must discuss VolSync simultaneity",
    )
    # Must present simultaneity as observed/proved, not still pending.
    require(
        re.search(
            r"(proved|observed|closed).{0,80}simultane|simultane.{0,80}(proved|observed|closed)",
            lowered,
            re.S,
        ),
        "drill must state VolSync simultaneity as observed/proved",
    )
    require(
        not re.search(
            r"observed-pending.{0,40}simultane|simultane.{0,40}observed-pending",
            lowered,
            re.S,
        ),
        "drill must not leave VolSync simultaneity as observed-pending",
    )
    require(
        "not yet the full simultaneity proof" not in lowered,
        "drill must drop the stale 'not yet the full simultaneity proof' hedge",
    )

    # Procedure must include the .status.stats pre-check (finding 1 mitigation).
    require(
        re.search(r"status\.stats", text),
        "procedure must include the .status.stats non-zero check",
    )

    # Retained snapshot names (real first backups, not swept).
    for name in (
        "autobrr-ceph-stage1-verify",
        "autobrr-r2-stage1-verify",
        "sabnzbd-ceph-stage2-verify",
        "sabnzbd-r2-stage2-verify",
    ):
        require(name in text, f"drill must record retained Snapshot CR {name}")


def test_operator_docs_reflect_stage2() -> None:
    """Operator-facing docs keep the Stage 2 documentary facts that still hold.

    Stage 3 advanced the fleet past two-volume coverage; this pin no longer
    freezes that end-state or a captain-gated Stage 3 freeze. It owns the Stage
    2 facts that remain true after the parallel run landed: sabnzbd-config as
    the fidelity subject, the mover-identity trap, the Stage 2 PASS result, and
    both-destination evidence. Fleet coverage counts live in stage3-test.
    """
    for path, label in (
        (COMPONENT_README, "components/kopiur/Readme.md"),
        (STAGE0_README, "system/kopiur/README.md"),
        (KOPIUR_SKILL, ".claude/skills/kopiur-backups/SKILL.md"),
    ):
        require(path.is_file(), f"missing {label}")
        text = path.read_text()
        lowered = text.lower()
        require(
            "sabnzbd" in lowered,
            f"{label} must name sabnzbd as the Stage 2 fidelity subject",
        )
        require(
            not re.search(r"live on exactly ONE volume", text),
            f"{label} must not still say 'exactly ONE volume'",
        )
        require(
            "KOPIUR_PUID" in text or ("mover" in lowered and "1000" in text),
            f"{label} must document the mover-identity trap",
        )
        require(
            re.search(
                r"stage\s*2[\s\S]{0,120}?(?:pass|passed)|(?:pass|passed)[\s\S]{0,120}?stage\s*2",
                lowered,
            ),
            f"{label} must record that Stage 2's restore gate passed",
        )
        require(
            ("both" in lowered and ("ceph" in lowered and "r2" in lowered))
            or "both destinations" in lowered
            or "from both" in lowered,
            f"{label} must record both-destination (ceph and r2) restore evidence",
        )
        require(
            "byte-identically" in lowered or "byte-identical" in lowered,
            f"{label} must record the sabnzbd byte-identical restore result",
        )


def test_no_embedded_credentials(docs_list: list[list[dict[str, Any]]]) -> None:
    suspicious = re.compile(
        r"(?i)\b(sk-ant-|sk-live-|AKIA[0-9A-Z]{16}|BEGIN (RSA |OPENSSH )?PRIVATE KEY)\b"
    )

    def walk(obj: Any, path: str, hits: list[str]) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}", hits)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]", hits)
        elif isinstance(obj, str):
            if suspicious.search(obj):
                hits.append(f"{path}={obj[:32]}...")

    hits: list[str] = []
    for docs in docs_list:
        for d in docs:
            walk(d, d.get("kind", "?"), hits)
    require(not hits, f"rendered manifests appear to embed credentials: {hits}")


def main() -> int:
    tests: list[str] = []
    failures: list[str] = []

    def run(name: str, fn) -> None:
        tests.append(name)
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as e:
            print(f"[FAIL] {name}: {e}")
            failures.append(f"{name}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")
            failures.append(f"{name}: {e}")

    # Production sabnzbd render: parse the LIVE overlay substitute map so the
    # pin stays honest as downloads/sabnzbd.yaml evolves.
    try:
        sab_sub = overlay_substitute(SABNZBD_OVERLAY)
    except Failure as e:
        print(f"[FAIL] parse sabnzbd overlay: {e}")
        print("Summary: 0 passed, 1 failed")
        return 1

    sab_env = {
        **sab_sub,
        "SECRET_DOMAIN": "example.test",
    }

    try:
        kopiur_docs = render_with_substitute(KOPIUR_BACKUP, sab_env)
        # The claim itself now comes from the kopiur pvc Component, which is
        # what replaced volsync's pvc.yaml when this volume retired.
        pvc_docs = render_with_substitute(KOPIUR_PVC, sab_env)
    except Failure as e:
        print(f"[FAIL] render: {e}")
        print("Summary: 0 passed, 1 failed")
        return 1

    run("stage2_volumes_still_onboarded", test_stage2_volumes_still_onboarded)
    run("sabnzbd_overlay_wiring", test_sabnzbd_overlay_wiring)
    run(
        "rendered_claim_override_and_puid",
        lambda: test_rendered_claim_override_and_puid(kopiur_docs),
    )
    run(
        "volsync_retired_and_claim_survives",
        lambda: test_volsync_retired_and_claim_survives(pvc_docs),
    )
    run("workload_identity_matches_override", test_workload_identity_matches_override)
    run("default_puid_without_override_is_1000", test_default_puid_without_override_is_1000)
    run("drill_document_contract", test_drill_document_contract)
    run("operator_docs_reflect_stage2", test_operator_docs_reflect_stage2)
    run(
        "no_embedded_credentials",
        lambda: test_no_embedded_credentials([kopiur_docs, pvc_docs]),
    )

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
