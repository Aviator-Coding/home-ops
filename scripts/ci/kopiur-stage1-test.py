#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 1 (backup component + one pilot).

Stage 1 is the first LIVE use of kopiur: a reusable backup component that
mirrors components/volsync, onboarded first on downloads/autobrr ALONGSIDE its
existing VolSync backups. MinIO is not a kopiur destination. Stage 2 later
added sabnzbd-config as a second volume (see kopiur-stage2-test.py for the
two-volume set and the PUID/claim contracts). This file still pins the Stage 1
component + historical autobrr contract via recorded substitute maps.

THE PILOT APP IS GONE. VolSync was first retired from autobrr (2026-09-02,
Stage 5 wave two), and later the same day the autobrr APP ITSELF was removed on
a captain decision because it was unused. So Stage 1 no longer has a live
volume to point at, and this file is now the kopiur COMPONENT contract plus the
Stage 0 and credential invariants - which was always the bulk of it.

Both renders are therefore driven by recorded substitute maps (PILOT_SUBSTITUTE
and volsync_env in main) rather than by parsing a live overlay. Those maps hold
autobrr's own values, kept as history, so every component assertion below is
unchanged in meaning. What that costs is the guarantee that the map still
matches something live; that guarantee now belongs to kopiur-stage3-test.py,
which checks all 29 remaining claims against the real overlays.

The pilot assertions inverted rather than disappeared - the same move made when
VolSync was retired from it. `test_pilot_app_is_fully_gone` requires the
overlay, the app directory and the namespace-kustomization entry to STAY gone,
so a half-revert cannot quietly reintroduce an app whose database, 1Password
item and CI pins were all removed with it. autobrr's kopia snapshots were
deliberately KEPT (deletion.onPolicyDelete / onScheduleDelete: Retain), but
retained backup data is not a live claim.

Credentials are NO LONGER per-namespace copies. Captain decision 2026-08-30,
key `credential-scope`, replaced them with operator-minted per-run projection,
and this file now pins that shape instead: all three projection legs present,
and the pilot's standing credential objects gone.

This test does not grep source text as its evidence. It:
  1. Renders the real kustomize builds Flux would apply for the kopiur backup
     component and the volsync backup sibling, then runs a Flux-shaped
     envsubst (including ${VAR:-default} and nested ${A:-${B}}) with the
     recorded pilot substitute map PILOT_SUBSTITUTE (the removed overlay's
     Stage 3 values: KOPIUR_PUID/PGID=2000, KOPIUR_SCHEDULE_R2='H 11 * * *',
     ceph schedule deliberately absent so the component default applies).
     A separate negative-control render uses an empty/synthetic env to pin
     the component defaults themselves (uid/gid 1000, r2 'H 4 * * *').
  2. Parses the downloads namespace kustomization, the security store overlay,
     and every apps/main/*/app overlay into structured objects.
  3. Asserts the Stage 1 safety contract on those objects:
       - rendered kinds are exactly 2 SnapshotPolicy + 2 SnapshotSchedule +
         1 Restore; NO MinIO policy/schedule, NO PVC, NO ReplicationSource
       - both policies use ClusterRepository ceph/r2, claim name autobrr
         (default ${KOPIUR_CLAIM:-${APP}}), copyMethod Snapshot, Ephemeral
         cache, retention matching VolSync for that destination, mover
         identity 2000:2000 from the recorded PILOT_SUBSTITUTE map, and pinned
         deletion.onPolicyDelete: Retain
       - both schedules pin deletion.onScheduleDelete: Retain, jitter 5m,
         native bare H minute, and HOUR slots that cannot collide with the
         pilot's VolSync schedules (ceph odd 4h vs even :45; r2 11 vs 03)
       - Restore is ${APP}-kopiur-dst (not ${APP}-dst), passive populator,
         onMissingSnapshot Continue, ssa IfNotPresent
       - parent Component resources only ./backup (no pvc.yaml)
       - downloads/autobrr is fully absent (overlay, app directory and
         namespace-kustomization entry), and no other overlay picked up its
         claim; components/volsync itself still renders 3 sources + 1
         destination for its 22 remaining users. The onboarded set is owned by
         kopiur-stage2-test.py and the authoritative retired set by
         kopiur-stage3-test.py.
       - credentials: ALL THREE projection legs wired (chart feature flag,
         `allowed` on both ClusterRepositories, `enabled` on every consuming
         SnapshotPolicy/Restore), every repository secretRef carrying an
         explicit namespace, and NO standing per-namespace credential objects
         anywhere - missing leg 2 or 3 leaves every CR green while the mover
         fails at run time, which no other gate in this repo can catch
       - Stage 0 operator/repository tree still free of SnapshotPolicy

Live Snapshot Succeeded / bucket contents / VolSync lastSyncTime remain
post-merge gates; this pins the GitOps contract that must hold before merge.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
KOPIUR_COMPONENT = ROOT / "kubernetes/components/kopiur"
KOPIUR_BACKUP = KOPIUR_COMPONENT / "backup"
KOPIUR_COMPONENT_KUST = KOPIUR_COMPONENT / "kustomization.yaml"
VOLSYNC_BACKUP = ROOT / "kubernetes/components/volsync/backup"
VOLSYNC_COMPONENT_KUST = ROOT / "kubernetes/components/volsync/kustomization.yaml"
DOWNLOADS_OVERLAY_KUST = ROOT / "kubernetes/apps/main/downloads/kustomization.yaml"
# Retired by captain decision `credential-scope` (2026-08-30). Kept as paths so
# the test can assert they are ABSENT - reintroducing standing credentials is
# exactly what that decision removed.
RETIRED_CREDS_OVERLAY = ROOT / "kubernetes/apps/main/downloads/kopiur-credentials.yaml"
RETIRED_CREDS_APP = ROOT / "kubernetes/apps/base/downloads/kopiur-credentials"
RETIRED_SYSTEM_STORE = (
    ROOT / "kubernetes/apps/base/security/external-secrets/stores/kopiur-system-secrets"
)
KOPIUR_HELMRELEASE = ROOT / "kubernetes/apps/base/system/kopiur/app/helmrelease.yaml"
KOPIUR_REPOSITORY = ROOT / "kubernetes/apps/base/system/kopiur/repository"
SECURITY_ES_OVERLAY = ROOT / "kubernetes/apps/main/security/external-secrets.yaml"
APPS_MAIN = ROOT / "kubernetes/apps/main"
KOPIUR_STAGE0_BASE = ROOT / "kubernetes/apps/base/system/kopiur"
STAGE0_README = KOPIUR_STAGE0_BASE / "README.md"

PILOT_APP = "autobrr"
PILOT_NS = "downloads"

# THE PILOT APP NO LONGER EXISTS. `downloads/autobrr` was removed on 2026-09-02
# (captain decision - unused; its kopia snapshots were deliberately KEPT), so
# there is no overlay left to parse and the production render below is driven by
# this recorded map instead of a live file.
#
# That is the same treatment this file already gives the VolSync half, and for
# the same reason: these are autobrr's own values, kept as HISTORY so the
# component contract they pin stays exactly as asserted. What is lost is only
# the guarantee that the map still matches a live overlay - and that guarantee
# now belongs to the 29 claims kopiur-stage3-test.py checks, not to this file.
#
# Do NOT "fix" a future component change by editing these numbers. They describe
# a volume that existed; if the component stops rendering them, the component
# changed.
# Built below from the PILOT_* constants, which are declared further down.
PILOT_SUBSTITUTE: dict[str, str]

# The VolSync schedules autobrr carried until VolSync was RETIRED from it on
# 2026-09-02 (Stage 5 wave two - docs/backups/kopiur-wave-two-retirement-2026-09-02.md).
# They are kept, as HISTORICAL values rather than a live overlay pin, because
# they are what kopiur's own slots were designed around: the odd 4-hour ceph
# offset and the r2 hour-11 assignment below only mean anything against them.
# That structural property still governs the 22 claims that remain dual-engine,
# so the non-collision assertion is retained rather than deleted with the
# schedules it was derived from. Do NOT reintroduce these into any overlay.
RETIRED_PILOT_VOLSYNC_CEPH = "45 */4 * * *"
RETIRED_PILOT_VOLSYNC_MINIO = "30 */6 * * *"
RETIRED_PILOT_VOLSYNC_R2 = "45 3 * * *"

# Production pilot kopiur pins (Stage 3). Historical values from the removed
# autobrr overlay; the component contract they pin stays asserted as recorded.
PILOT_KOPIUR_PUID = 2000
PILOT_KOPIUR_PGID = 2000
PILOT_KOPIUR_R2_CRON = "H 11 * * *"
# The live claim size, measured 2026-09-02. Load-bearing since retirement:
# components/kopiur/pvc carries `ssa: IfNotPresent`, so this value is
# create-time-only and stays unexercised until someone rebuilds the claim.
PILOT_CAPACITY = "5Gi"

PILOT_SUBSTITUTE = {
    "APP": PILOT_APP,
    "KOPIUR_CAPACITY": PILOT_CAPACITY,
    "KOPIUR_PUID": str(PILOT_KOPIUR_PUID),
    "KOPIUR_PGID": str(PILOT_KOPIUR_PGID),
    # KOPIUR_SCHEDULE_CEPH deliberately absent - the component default's odd
    # 4-hour offset is what kept the two engines apart, so it was never
    # overridden per app.
    "KOPIUR_SCHEDULE_R2": PILOT_KOPIUR_R2_CRON,
}

# Component defaults. The recorded pilot map no longer drives a live volume;
# the identity/r2 defaults are pinned only by the explicit negative-control
# render below. Ceph stays on the component default in the pilot map too
# (structurally offset).
COMPONENT_DEFAULT_KOPIUR_CEPH_CRON = "H 1-23/4 * * *"
COMPONENT_DEFAULT_KOPIUR_R2_CRON = "H 4 * * *"
COMPONENT_DEFAULT_PUID = 1000
COMPONENT_DEFAULT_PGID = 1000
# Alias used by the production ceph schedule pin (still the component default).
PILOT_KOPIUR_CEPH_CRON = COMPONENT_DEFAULT_KOPIUR_CEPH_CRON

STAGE1_KINDS = frozenset({"SnapshotPolicy", "SnapshotSchedule", "Restore"})
FORBIDDEN_RENDERED_KINDS = frozenset(
    {
        "ReplicationSource",
        "ReplicationDestination",
        "PersistentVolumeClaim",
        "ExternalSecret",  # credentials are separate, not in the component
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


def kustomize_build(path: Path) -> list[dict[str, Any]]:
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
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    if not docs:
        raise Failure(f"kustomize build of {path} produced no documents")
    return docs


def load_multi(path: Path) -> list[dict[str, Any]]:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    if not docs:
        raise Failure(f"{path} produced no documents")
    return docs


def by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == kind]


def flux_envsubst(text: str, env: dict[str, str]) -> str:
    """Flux-shaped ${VAR} / ${VAR:-default} substitution, including nesting.

    Mirrors kustomize-controller's envsubst enough for our component templates:
    bare ${VAR}, ${VAR:-literal}, and ${VAR:-${OTHER}}. Defaults are themselves
    run through substitution. Unresolved bare vars are left intact so the
    caller can detect them. Brace matching is depth-aware so nested ${} in a
    default does not truncate at the inner closing brace.
    """

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
            # Parse ${...} with nested brace depth.
            depth = 1
            j = i + 2
            while j < n and depth:
                if s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                j += 1
            if depth != 0:
                # Unbalanced - emit literally and move on.
                out.append(s[i])
                i += 1
                continue
            body = s[i + 2 : j - 1]  # inside the outer braces
            # body is VAR or VAR:-default (default may contain ${...})
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
                out.append(s[i:j])  # leave unresolved bare ${VAR}
            i = j
        return "".join(out)

    return expand(text)


def render_with_substitute(path: Path, env: dict[str, str]) -> list[dict[str, Any]]:
    """kustomize build + Flux postBuild.substitute over the whole output."""
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
    # Filter Flux-disabled / Grafana-style tokens if any; component must be clean.
    require(
        not unresolved,
        f"unresolved substitution tokens after envsubst of {path}: {unresolved}",
    )
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    if not docs:
        raise Failure(f"substituted build of {path} produced no documents")
    return docs


def cron_hours(expr: str) -> set[int]:
    """Expand the hour field of a 5-field cron into concrete hours 0-23.

    Supports N, N-M, N-M/S, */S, bare *. Minute may be H or a number; we only
    need hours for the structural non-collision check.
    """
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


def test_component_layout() -> None:
    """Parent is a Component of ./backup only; backup is a plain Kustomization."""
    parent = yaml.safe_load(KOPIUR_COMPONENT_KUST.read_text())
    require(
        parent.get("kind") == "Component",
        f"parent must be kind Component, got {parent.get('kind')!r}",
    )
    require(
        parent.get("apiVersion") == "kustomize.config.k8s.io/v1alpha1",
        f"parent apiVersion unexpected: {parent.get('apiVersion')!r}",
    )
    resources = parent.get("resources") or []
    require(
        resources == ["./backup"],
        f"parent Component must resource ONLY ./backup (no pvc), got {resources}",
    )
    require(
        "pvc.yaml" not in resources and not (KOPIUR_COMPONENT / "pvc.yaml").exists(),
        "kopiur component must not ship a pvc.yaml during the parallel run",
    )

    backup = yaml.safe_load((KOPIUR_BACKUP / "kustomization.yaml").read_text())
    require(
        backup.get("kind") == "Kustomization",
        f"backup/ must be a plain Kustomization (Flux path), got {backup.get('kind')!r}",
    )
    require(
        backup.get("apiVersion") == "kustomize.config.k8s.io/v1beta1",
        f"backup/ apiVersion unexpected: {backup.get('apiVersion')!r}",
    )
    bres = backup.get("resources") or []
    require(
        set(bres) == {"../ceph", "../r2"},
        f"backup/ must include only ../ceph and ../r2 (no minio), got {bres}",
    )
    require(
        not (KOPIUR_COMPONENT / "minio").exists(),
        "components/kopiur/minio must not exist (MinIO out by captain decision)",
    )

    # Sibling shape: volsync parent Component still owns backup+pvc.
    vs_parent = yaml.safe_load(VOLSYNC_COMPONENT_KUST.read_text())
    require(vs_parent.get("kind") == "Component", "volsync parent drifted from Component")
    vs_res = vs_parent.get("resources") or []
    require(
        "./backup" in vs_res and "./pvc.yaml" in vs_res,
        f"volsync parent must still own backup+pvc, got {vs_res}",
    )


def test_rendered_pilot_kinds_and_names(docs: list[dict[str, Any]]) -> None:
    kinds = {d.get("kind") for d in docs}
    require(
        kinds == STAGE1_KINDS,
        f"rendered kinds must be exactly {sorted(STAGE1_KINDS)}, got {sorted(kinds)}",
    )
    for d in docs:
        require(
            d.get("kind") not in FORBIDDEN_RENDERED_KINDS,
            f"component emitted forbidden kind {d.get('kind')}/{d.get('metadata', {}).get('name')}",
        )

    policies = by_kind(docs, "SnapshotPolicy")
    schedules = by_kind(docs, "SnapshotSchedule")
    restores = by_kind(docs, "Restore")
    require(len(policies) == 2, f"expected 2 SnapshotPolicy, got {len(policies)}")
    require(len(schedules) == 2, f"expected 2 SnapshotSchedule, got {len(schedules)}")
    require(len(restores) == 1, f"expected 1 Restore, got {len(restores)}")

    pnames = sorted(p["metadata"]["name"] for p in policies)
    snames = sorted(s["metadata"]["name"] for s in schedules)
    require(
        pnames == [f"{PILOT_APP}-ceph", f"{PILOT_APP}-r2"],
        f"policy names unexpected: {pnames}",
    )
    require(
        snames == [f"{PILOT_APP}-ceph", f"{PILOT_APP}-r2"],
        f"schedule names unexpected: {snames}",
    )
    require(
        restores[0]["metadata"]["name"] == f"{PILOT_APP}-kopiur-dst",
        f"Restore must be named {PILOT_APP}-kopiur-dst (not {PILOT_APP}-dst), "
        f"got {restores[0]['metadata']['name']!r}",
    )
    # No MinIO-named anything.
    for d in docs:
        name = d["metadata"]["name"]
        require(
            "minio" not in name.lower(),
            f"MinIO object leaked into render: {d['kind']}/{name}",
        )


def test_policies_contract(docs: list[dict[str, Any]], vs_docs: list[dict[str, Any]]) -> None:
    policies = {p["metadata"]["name"]: p for p in by_kind(docs, "SnapshotPolicy")}
    vs_sources = {
        s["metadata"]["name"]: s for s in by_kind(vs_docs, "ReplicationSource")
    }
    require(
        f"{PILOT_APP}-ceph" in vs_sources and f"{PILOT_APP}-r2" in vs_sources,
        f"volsync render missing pilot sources, got {sorted(vs_sources)}",
    )

    for dest, repo_name in (("ceph", "ceph"), ("r2", "r2")):
        p = policies[f"{PILOT_APP}-{dest}"]
        spec = p["spec"]
        require(spec.get("copyMethod") == "Snapshot", f"{dest}: copyMethod must be Snapshot")
        require(
            spec.get("volumeSnapshotClassName") == "csi-ceph-blockpool",
            f"{dest}: unexpected volumeSnapshotClassName {spec.get('volumeSnapshotClassName')!r}",
        )
        repo = spec.get("repository") or {}
        require(
            repo.get("kind") == "ClusterRepository" and repo.get("name") == repo_name,
            f"{dest}: repository must be ClusterRepository/{repo_name}, got {repo}",
        )
        # 1:1 shape - no multi-repository fan-out.
        require(
            "repositories" not in spec,
            f"{dest}: must not use spec.repositories fan-out",
        )
        sources = spec.get("sources") or []
        require(len(sources) == 1, f"{dest}: expected one source, got {sources}")
        require(
            (sources[0].get("pvc") or {}).get("name") == PILOT_APP,
            f"{dest}: claim must resolve to {PILOT_APP}, got {sources[0]}",
        )
        # No pvcSelector.
        require(
            all("pvcSelector" not in (s or {}) for s in sources),
            f"{dest}: must not use pvcSelector",
        )
        cache = ((spec.get("mover") or {}).get("cache")) or {}
        require(
            cache.get("mode") == "Ephemeral",
            f"{dest}: cache.mode must be Ephemeral (no standing cache PVCs), got {cache}",
        )
        require(cache.get("capacity") == "2Gi", f"{dest}: cache capacity default 2Gi, got {cache}")
        psc = ((spec.get("mover") or {}).get("podSecurityContext")) or {}
        require(
            psc.get("runAsUser") == PILOT_KOPIUR_PUID
            and psc.get("runAsGroup") == PILOT_KOPIUR_PGID
            and psc.get("fsGroup") == PILOT_KOPIUR_PGID,
            f"{dest}: recorded pilot mover must be "
            f"{PILOT_KOPIUR_PUID}:{PILOT_KOPIUR_PGID} from PILOT_SUBSTITUTE, got {psc}",
        )
        deletion = spec.get("deletion") or {}
        require(
            deletion.get("onPolicyDelete") == "Retain",
            f"{dest}: deletion.onPolicyDelete must be explicitly Retain, got {deletion}",
        )
        # defaultDeletionPolicy must NOT be set to Retain (would block prune).
        require(
            spec.get("defaultDeletionPolicy") in (None, "Delete"),
            f"{dest}: defaultDeletionPolicy must stay Delete-or-unset for retention, "
            f"got {spec.get('defaultDeletionPolicy')!r}",
        )

    # Retention must match VolSync for the same destination.
    ceph_ret = policies[f"{PILOT_APP}-ceph"]["spec"]["retention"]
    vs_ceph_ret = vs_sources[f"{PILOT_APP}-ceph"]["spec"]["restic"]["retain"]
    require(
        ceph_ret.get("keepHourly") == vs_ceph_ret.get("hourly")
        and ceph_ret.get("keepDaily") == vs_ceph_ret.get("daily")
        and ceph_ret.get("keepWeekly") == vs_ceph_ret.get("weekly")
        and ceph_ret.get("keepMonthly") == vs_ceph_ret.get("monthly"),
        f"ceph retention mismatch: kopiur={ceph_ret} volsync={vs_ceph_ret}",
    )
    r2_ret = policies[f"{PILOT_APP}-r2"]["spec"]["retention"]
    vs_r2_ret = vs_sources[f"{PILOT_APP}-r2"]["spec"]["restic"]["retain"]
    require(
        "keepHourly" not in r2_ret and "hourly" not in vs_r2_ret,
        f"r2 must not keep hourly: kopiur={r2_ret} volsync={vs_r2_ret}",
    )
    require(
        r2_ret.get("keepDaily") == vs_r2_ret.get("daily")
        and r2_ret.get("keepWeekly") == vs_r2_ret.get("weekly")
        and r2_ret.get("keepMonthly") == vs_r2_ret.get("monthly"),
        f"r2 retention mismatch: kopiur={r2_ret} volsync={vs_r2_ret}",
    )


def test_schedules_non_collision(docs: list[dict[str, Any]]) -> None:
    """Recorded pilot schedules from the historical PILOT_SUBSTITUTE map."""
    schedules = {s["metadata"]["name"]: s for s in by_kind(docs, "SnapshotSchedule")}
    for dest, expected_cron in (
        ("ceph", PILOT_KOPIUR_CEPH_CRON),
        ("r2", PILOT_KOPIUR_R2_CRON),
    ):
        s = schedules[f"{PILOT_APP}-{dest}"]
        spec = s["spec"]
        require(
            (spec.get("policyRef") or {}).get("name") == f"{PILOT_APP}-{dest}",
            f"{dest}: policyRef mismatch {spec.get('policyRef')}",
        )
        deletion = spec.get("deletion") or {}
        require(
            deletion.get("onScheduleDelete") == "Retain",
            f"{dest}: deletion.onScheduleDelete must be explicitly Retain, got {deletion}",
        )
        sched = spec.get("schedule") or {}
        cron = sched.get("cron")
        require(cron == expected_cron, f"{dest}: cron must be {expected_cron!r}, got {cron!r}")
        require(sched.get("jitter") == "5m", f"{dest}: jitter must be 5m, got {sched.get('jitter')!r}")
        # Bare H only - no H(range) which the webhook rejects.
        minute = cron.split()[0]
        require(
            minute == "H",
            f"{dest}: minute must be bare H (not H(...)), got {minute!r} in {cron!r}",
        )
        require(
            "H(" not in cron,
            f"{dest}: Jenkins range H(...) is webhook-rejected, got {cron!r}",
        )

    # Structural hour non-overlap with the VolSync schedules kopiur's slots were
    # designed around. autobrr no longer runs VolSync (retired 2026-09-02), so
    # these are the recorded historical values - but the property they encode is
    # what still keeps the two engines apart on all 22 dual-engine claims.
    k_ceph_hours = cron_hours(PILOT_KOPIUR_CEPH_CRON)
    # VolSync ceph "45 */4 * * *" -> hours 0,4,8,12,16,20
    v_ceph_hours = cron_hours(RETIRED_PILOT_VOLSYNC_CEPH)
    require(
        k_ceph_hours.isdisjoint(v_ceph_hours),
        f"kopiur ceph hours {sorted(k_ceph_hours)} overlap volsync {sorted(v_ceph_hours)}",
    )
    require(
        k_ceph_hours == {1, 5, 9, 13, 17, 21},
        f"kopiur ceph hours must be the odd 4h slots, got {sorted(k_ceph_hours)}",
    )

    k_r2_hours = cron_hours(PILOT_KOPIUR_R2_CRON)
    v_r2_hours = cron_hours(RETIRED_PILOT_VOLSYNC_R2)
    require(
        k_r2_hours.isdisjoint(v_r2_hours),
        f"kopiur r2 hours {sorted(k_r2_hours)} overlap volsync {sorted(v_r2_hours)}",
    )
    require(
        k_r2_hours == {11},
        f"recorded pilot kopiur r2 hour must be 11 (downloads namespace), got {sorted(k_r2_hours)}",
    )
    require(
        v_r2_hours == {3},
        f"the retired pilot volsync r2 hour is recorded as 3; got {sorted(v_r2_hours)}",
    )


def test_component_defaults_without_override() -> None:
    """Negative control: component defaults with a synthetic env (NOT the recorded pilot map).

    The recorded pilot map carries KOPIUR_PUID/PGID=2000 and KOPIUR_SCHEDULE_R2='H 11 * * *'
    from Stage 3. This render deliberately omits those overrides so the component's
    own defaults stay pinned: mover 1000:1000, ceph 'H 1-23/4 * * *', r2 'H 4 * * *'.
    """
    docs = render_with_substitute(
        KOPIUR_BACKUP,
        {
            # Synthetic env only - do not read the live overlay here.
            "APP": PILOT_APP,
            "SECRET_DOMAIN": "example.test",
            # deliberately omit KOPIUR_PUID/PGID and KOPIUR_SCHEDULE_*
        },
    )
    for p in by_kind(docs, "SnapshotPolicy"):
        psc = ((p["spec"].get("mover") or {}).get("podSecurityContext")) or {}
        require(
            psc.get("runAsUser") == COMPONENT_DEFAULT_PUID
            and psc.get("runAsGroup") == COMPONENT_DEFAULT_PGID
            and psc.get("fsGroup") == COMPONENT_DEFAULT_PGID,
            f"component default mover must be "
            f"{COMPONENT_DEFAULT_PUID}:{COMPONENT_DEFAULT_PGID}, got {psc}",
        )
    schedules = {s["metadata"]["name"]: s for s in by_kind(docs, "SnapshotSchedule")}
    for dest, expected_cron in (
        ("ceph", COMPONENT_DEFAULT_KOPIUR_CEPH_CRON),
        ("r2", COMPONENT_DEFAULT_KOPIUR_R2_CRON),
    ):
        cron = ((schedules[f"{PILOT_APP}-{dest}"]["spec"].get("schedule")) or {}).get("cron")
        require(
            cron == expected_cron,
            f"component default {dest} cron must be {expected_cron!r}, got {cron!r}",
        )
    require(
        cron_hours(COMPONENT_DEFAULT_KOPIUR_R2_CRON) == {4},
        "component default r2 hour pin drifted",
    )


def test_restore_passive_contract(docs: list[dict[str, Any]]) -> None:
    restores = by_kind(docs, "Restore")
    require(len(restores) == 1, f"expected 1 Restore, got {len(restores)}")
    r = restores[0]
    labels = (r.get("metadata") or {}).get("labels") or {}
    require(
        labels.get("kustomize.toolkit.fluxcd.io/ssa") == "IfNotPresent",
        f"Restore must carry ssa IfNotPresent, got {labels}",
    )
    spec = r["spec"]
    repo = spec.get("repository") or {}
    require(
        repo.get("kind") == "ClusterRepository" and repo.get("name") == "ceph",
        f"Restore repository must be ClusterRepository/ceph, got {repo}",
    )
    src = (spec.get("source") or {}).get("fromPolicy") or {}
    require(
        src.get("name") == f"{PILOT_APP}-ceph" and src.get("offset") == 0,
        f"Restore fromPolicy unexpected: {src}",
    )
    target = spec.get("target") or {}
    require(
        "populator" in target and target.get("pvc") is None,
        f"Restore must be passive populator (no target.pvc), got {target}",
    )
    policy = spec.get("policy") or {}
    require(
        policy.get("onMissingSnapshot") == "Continue",
        f"onMissingSnapshot must be Continue (deploy-or-restore), got {policy}",
    )
    cache = ((spec.get("mover") or {}).get("cache")) or {}
    require(cache.get("mode") == "Ephemeral", f"Restore cache must be Ephemeral, got {cache}")


def test_volsync_component_intact(vs_docs: list[dict[str, Any]]) -> None:
    """components/volsync still renders a complete backup set for its 22 users.

    This test has inverted twice. It first asserted that the Stage 1 pilot ran
    three ReplicationSources and one ReplicationDestination ALONGSIDE kopiur -
    Stage 1's whole safety property, two independent engines with nothing
    swapped. When VolSync was retired from that claim (2026-09-02, Stage 5 wave
    two) it asserted the pilot carried no VolSync anything. The pilot app was
    then removed outright the same day, so that half has nothing to read and is
    gone; the pilot-absence assertions live in test_pilot_app_is_fully_gone.

    What survives is the half that always mattered most: the distinction between
    USE and COMPONENT. Retiring or removing one app must never touch
    components/volsync itself, which 22 other claims still depend on. Rendered
    under a SYNTHETIC env carrying the pilot's own historical VOLSYNC_* values,
    so the schedule contract stays exactly as pinned.
    """
    sources = by_kind(vs_docs, "ReplicationSource")
    dests = by_kind(vs_docs, "ReplicationDestination")
    names = sorted(x["metadata"]["name"] for x in sources)
    require(
        names == [f"{PILOT_APP}-ceph", f"{PILOT_APP}-minio", f"{PILOT_APP}-r2"],
        f"components/volsync must still emit all three sources, got {names}",
    )
    require(
        len(dests) == 1,
        f"components/volsync must still emit one ReplicationDestination, got {len(dests)}",
    )
    require(
        dests[0]["metadata"]["name"] == f"{PILOT_APP}-dst",
        f"volsync destination must remain ${{APP}}-dst, got {dests[0]['metadata']['name']}",
    )
    by_name = {x["metadata"]["name"]: x for x in sources}
    require(
        by_name[f"{PILOT_APP}-ceph"]["spec"]["trigger"]["schedule"]
        == RETIRED_PILOT_VOLSYNC_CEPH,
        "volsync component no longer honours VOLSYNC_SCHEDULE_CEPH",
    )
    require(
        by_name[f"{PILOT_APP}-minio"]["spec"]["trigger"]["schedule"]
        == RETIRED_PILOT_VOLSYNC_MINIO,
        "volsync component no longer honours VOLSYNC_SCHEDULE_MINIO",
    )
    require(
        by_name[f"{PILOT_APP}-r2"]["spec"]["trigger"]["schedule"] == RETIRED_PILOT_VOLSYNC_R2,
        "volsync component no longer honours VOLSYNC_SCHEDULE_R2",
    )


def test_pilot_app_is_fully_gone() -> None:
    """The autobrr app is removed - assert its ABSENCE, not its wiring.

    This file used to assert that the Stage 1 pilot was wired correctly and
    still onboarded to kopiur. The app was removed on 2026-09-02 (captain
    decision - unused), so, exactly as when VolSync was retired from it, the
    assertion inverts rather than disappears: a half-revert that puts the
    overlay, the manifests or the namespace kustomization entry back - without
    also restoring the database, the 1Password item and the CI pins - would
    otherwise be invisible to every gate in this repo.

    Its kopia snapshots were deliberately kept (deletion.onPolicyDelete and
    onScheduleDelete: Retain on both policies and both schedules). Retained
    backup data is not a live claim and nothing here should resurrect one.
    """
    overlay = APPS_MAIN / PILOT_NS / f"{PILOT_APP}.yaml"
    require(not overlay.exists(), f"{overlay} is back - the pilot app was removed 2026-09-02")
    app_dir = ROOT / "kubernetes/apps/base" / PILOT_NS / PILOT_APP
    require(not app_dir.exists(), f"{app_dir} is back - the pilot app was removed 2026-09-02")

    dl = yaml.safe_load(DOWNLOADS_OVERLAY_KUST.read_text())
    resources = dl.get("resources") or []
    require(
        f"./{PILOT_APP}.yaml" not in resources,
        f"downloads overlay still lists ./{PILOT_APP}.yaml, whose file is gone - "
        f"Flux would fail the whole downloads Kustomization",
    )
    require(
        "./kopiur-credentials.yaml" not in resources,
        "downloads overlay must NOT list kopiur-credentials (projection replaced it)",
    )

    # And no other overlay quietly inherited the pilot's claim.
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
            sub = ((d.get("spec") or {}).get("postBuild") or {}).get("substitute") or {}
            claim = sub.get("KOPIUR_CLAIM") or sub.get("APP")
            require(
                not (d.get("metadata", {}).get("namespace") == PILOT_NS and claim == PILOT_APP),
                f"{path} still declares the removed {PILOT_NS}/{PILOT_APP} claim",
            )


def test_credential_projection_wired_and_nothing_standing() -> None:
    """The permanent credential shape: projected per run, nothing at rest.

    Captain decision 2026-08-30, key `credential-scope`. This is the gate that
    catches the failure mode nothing else in this repo can: projection is gated
    in THREE places, and if leg 2 (repository consent) or leg 3 (consumer
    opt-in) is missing, every CR still reconciles perfectly clean while the
    mover fails at run time. flate cannot see it; only this can.
    """
    # --- leg 1: operator RBAC, on the chart's own feature flag ---
    hr = yaml.safe_load(KOPIUR_HELMRELEASE.read_text())
    features = ((hr.get("spec") or {}).get("values") or {}).get("features") or {}
    require(
        ((features.get("credentialProjection") or {}).get("enabled")) is True,
        "leg 1: HelmRelease must set features.credentialProjection.enabled: true "
        f"(the operator RBAC), got {features!r}",
    )
    # It must not quietly pick up the OTHER feature asking for the same
    # unscoped Secret verbs - that is outside the decision.
    require(
        "kopiaUi" not in features,
        f"features.kopiaUi is not covered by the credential-scope decision, got {sorted(features)}",
    )

    # --- leg 2: repository-owner consent, on BOTH repositories ---
    repo_docs = [
        d
        for d in yaml.safe_load_all((KOPIUR_REPOSITORY / "clusterrepository.yaml").read_text())
        if d
    ]
    repos = {d["metadata"]["name"]: d for d in repo_docs if d.get("kind") == "ClusterRepository"}
    require(
        set(repos) == {"ceph", "r2"},
        f"expected ClusterRepositories ceph + r2, got {sorted(repos)}",
    )
    for name, repo in sorted(repos.items()):
        spec = repo["spec"]
        require(
            ((spec.get("credentialProjection") or {}).get("allowed")) is True,
            f"leg 2: ClusterRepository {name} must set credentialProjection.allowed: true",
        )
        # Load-bearing for projection: the CRD needs an EXPLICIT namespace on
        # every secretRef or the operator cannot know what to copy.
        refs = []
        auth = ((spec.get("backend") or {}).get("s3") or {}).get("auth") or {}
        if "secretRef" in auth:
            refs.append(("backend.s3.auth", auth["secretRef"]))
        enc = spec.get("encryption") or {}
        if "passwordSecretRef" in enc:
            refs.append(("encryption", enc["passwordSecretRef"]))
        require(refs, f"{name} declares no secretRef at all")
        for where, ref in refs:
            require(
                ref.get("namespace"),
                f"leg 2: {name} {where}.secretRef must set an EXPLICIT namespace "
                f"for projection to know what to copy, got {ref!r}",
            )

    # --- leg 3: consumer opt-in, on every mover-running CR in the component ---
    for path in sorted(KOPIUR_COMPONENT.rglob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not doc or doc.get("kind") not in {"SnapshotPolicy", "Restore"}:
                continue
            rel = path.relative_to(ROOT)
            require(
                ((doc["spec"].get("credentialProjection") or {}).get("enabled")) is True,
                f"leg 3: {rel} {doc['kind']}/{doc['metadata']['name']} must set "
                "credentialProjection.enabled: true, or its mover runs with no "
                "credentials while every CR still reports Ready",
            )

    # --- nothing standing: the pilot copies must be genuinely gone ---
    for path, what in (
        (RETIRED_CREDS_APP, "downloads kopiur-credentials ExternalSecrets"),
        (RETIRED_CREDS_OVERLAY, "downloads kopiur-credentials Flux Kustomization"),
        (RETIRED_SYSTEM_STORE, "downloads-scoped kopiur-system-secrets ClusterSecretStore"),
    ):
        require(
            not path.exists(),
            f"{what} must stay deleted ({path.relative_to(ROOT)}) - standing repository "
            "credentials in an app namespace are what credential-scope removed",
        )

    # And nothing may re-declare that store Kustomization in the security overlay.
    sec_docs = load_multi(SECURITY_ES_OVERLAY)
    names = {d["metadata"]["name"] for d in sec_docs if d.get("kind") == "Kustomization"}
    require(
        "kopiur-system-secrets" not in names,
        f"security/external-secrets overlay must no longer define kopiur-system-secrets, got {sorted(names)}",
    )

    # Belt and braces: no manifest anywhere may target the three Secret names
    # the ClusterRepositories reference, in any namespace other than `system`.
    offenders: list[str] = []
    repo_secret_names = {"kopiur", "kopiur-ceph-secret", "kopiur-r2-secret"}
    for path in sorted((ROOT / "kubernetes/apps").rglob("*.yaml")):
        if "/system/kopiur/" in str(path):
            continue
        try:
            docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
        except yaml.YAMLError:
            continue
        for d in docs:
            if d.get("kind") not in {"ExternalSecret", "Secret", "PushSecret"}:
                continue
            target = ((d.get("spec") or {}).get("target") or {}).get("name")
            for candidate in (target, d.get("metadata", {}).get("name")):
                if candidate in repo_secret_names:
                    offenders.append(f"{path.relative_to(ROOT)}:{d['kind']}->{candidate}")
    require(
        not offenders,
        f"no manifest outside system/kopiur may materialise a repository credential Secret: {offenders}",
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


def test_stage0_tree_still_policy_free() -> None:
    """Stage 1 lives in components/; Stage 0 base must stay policy-free."""
    found: list[str] = []
    for path in KOPIUR_STAGE0_BASE.rglob("*.yaml"):
        for doc in yaml.safe_load_all(path.read_text()):
            if not doc:
                continue
            kind = doc.get("kind")
            if kind in STAGE1_KINDS:
                found.append(f"{path.relative_to(ROOT)}:{kind}")
    require(not found, f"Stage 0 tree must stay free of Stage 1 kinds: {found}")


def test_stage0_readme_record_only_still_true() -> None:
    """Stage 0 README must still call kopiur-ceph-bucket a record nothing reads.

    Captain decision: prefer OBC Secret; only if that were unworkable would we
    read the 1Password item AND correct Stage 0's README. The code reads the
    OBC Secret, so the record-only claim must remain true - no contradiction.
    """
    text = STAGE0_README.read_text()
    # Semantic: the item is described as record / nothing reads it.
    lowered = text.lower()
    require(
        "kopiur-ceph-bucket" in lowered,
        "Stage 0 README must still document the kopiur-ceph-bucket item",
    )
    require(
        ("record" in lowered and "nothing reads" in lowered)
        or ("record-only" in lowered)
        or ("record that nothing reads" in lowered),
        "Stage 0 README must still state kopiur-ceph-bucket is record-only / nothing reads it",
    )


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

    # Pilot render. This used to parse the LIVE downloads/autobrr.yaml so the
    # pin stayed honest as that overlay evolved; the app was removed on
    # 2026-09-02, so it now renders from PILOT_SUBSTITUTE - the same values,
    # recorded as history. See that constant for why they must not be edited to
    # accommodate a component change.
    pilot_env = {
        **PILOT_SUBSTITUTE,
        # cluster-secrets keys the volsync ExternalSecrets reference. Value is
        # a non-secret placeholder - we only need the render to complete so the
        # ReplicationSource contracts can be asserted.
        "SECRET_DOMAIN": "example.test",
    }

    # components/volsync is rendered under a SYNTHETIC env for the same reason
    # the kopiur half now is: VolSync was retired from autobrr on 2026-09-02 and
    # the app removed, so no live overlay carries these keys. Rendering the
    # component under a map that lacks them would fall back to defaults
    # everywhere and quietly assert nothing about the component 22 other claims
    # still use. The values are autobrr's own, recorded as history.
    volsync_env = {
        "APP": PILOT_APP,
        "SECRET_DOMAIN": "example.test",
        "VOLSYNC_SCHEDULE_CEPH": RETIRED_PILOT_VOLSYNC_CEPH,
        "VOLSYNC_SCHEDULE_MINIO": RETIRED_PILOT_VOLSYNC_MINIO,
        "VOLSYNC_SCHEDULE_R2": RETIRED_PILOT_VOLSYNC_R2,
    }

    try:
        kopiur_docs = render_with_substitute(KOPIUR_BACKUP, pilot_env)
        volsync_docs = render_with_substitute(VOLSYNC_BACKUP, volsync_env)
    except Failure as e:
        print(f"[FAIL] render: {e}")
        print("Summary: 0 passed, 1 failed")
        return 1

    run("component_layout", test_component_layout)
    run("rendered_pilot_kinds", lambda: test_rendered_pilot_kinds_and_names(kopiur_docs))
    run("policies_contract", lambda: test_policies_contract(kopiur_docs, volsync_docs))
    run("schedules_non_collision", lambda: test_schedules_non_collision(kopiur_docs))
    run("component_defaults_without_override", test_component_defaults_without_override)
    run("restore_passive", lambda: test_restore_passive_contract(kopiur_docs))
    run("volsync_component_intact", lambda: test_volsync_component_intact(volsync_docs))
    run("pilot_app_is_fully_gone", test_pilot_app_is_fully_gone)
    run(
        "credential_projection_wired",
        test_credential_projection_wired_and_nothing_standing,
    )
    run(
        "no_embedded_credentials",
        lambda: test_no_embedded_credentials([kopiur_docs, volsync_docs]),
    )
    run("stage0_tree_policy_free", test_stage0_tree_still_policy_free)
    run("stage0_readme_record_only", test_stage0_readme_record_only_still_true)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
