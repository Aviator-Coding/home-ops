#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 1 (backup component + one pilot).

Stage 1 is the first LIVE use of kopiur: a reusable backup component that
mirrors components/volsync, onboarded first on downloads/autobrr ALONGSIDE its
existing VolSync backups. MinIO is not a kopiur destination. Credentials are
pilot-scoped copies into downloads only. Stage 2 later added sabnzbd-config as
a second volume (see kopiur-stage2-test.py for the two-volume set and the
PUID/claim contracts); this file still pins the Stage 1 component + autobrr
contract and only requires that autobrr remain onboarded.

This test does not grep source text as its evidence. It:
  1. Renders the real kustomize builds Flux would apply for the kopiur backup
     component and the volsync backup sibling, then runs a Flux-shaped
     envsubst (including ${VAR:-default} and nested ${A:-${B}}) with the pilot
     APP=autobrr map.
  2. Parses the pilot Flux Kustomization, the downloads credentials overlay,
     the security store overlay, and every apps/main/*/app overlay into
     structured objects.
  3. Asserts the Stage 1 safety contract on those objects:
       - rendered kinds are exactly 2 SnapshotPolicy + 2 SnapshotSchedule +
         1 Restore; NO MinIO policy/schedule, NO PVC, NO ReplicationSource
       - both policies use ClusterRepository ceph/r2, claim name autobrr
         (default ${KOPIUR_CLAIM:-${APP}}), copyMethod Snapshot, Ephemeral
         cache, retention matching VolSync for that destination, and pinned
         deletion.onPolicyDelete: Retain
       - both schedules pin deletion.onScheduleDelete: Retain, jitter 5m,
         native bare H minute, and HOUR slots that cannot collide with the
         pilot's VolSync schedules (ceph odd 4h vs even :45; r2 04 vs 03)
       - Restore is ${APP}-kopiur-dst (not ${APP}-dst), passive populator,
         onMissingSnapshot Continue, ssa IfNotPresent
       - parent Component resources only ./backup (no pvc.yaml)
       - downloads/autobrr includes components/kopiur alongside components/volsync
         (Stage 2 may add further authorised volumes; the exclusive set is
         owned by kopiur-stage2-test.py)
       - credentials: ClusterSecretStore restricted to [downloads], three
         ExternalSecrets targeting the exact Secret names the ClusterRepos
         reference, Ceph S3 keys from the OBC Secret via the kubernetes store
         (not 1Password), no embedded secret values
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
AUTOBRR_OVERLAY = ROOT / "kubernetes/apps/main/downloads/autobrr.yaml"
DOWNLOADS_OVERLAY_KUST = ROOT / "kubernetes/apps/main/downloads/kustomization.yaml"
KOPIUR_CREDS_OVERLAY = ROOT / "kubernetes/apps/main/downloads/kopiur-credentials.yaml"
KOPIUR_CREDS_APP = ROOT / "kubernetes/apps/base/downloads/kopiur-credentials/app"
KOPIUR_SYSTEM_STORE = (
    ROOT / "kubernetes/apps/base/security/external-secrets/stores/kopiur-system-secrets"
)
SECURITY_ES_OVERLAY = ROOT / "kubernetes/apps/main/security/external-secrets.yaml"
APPS_MAIN = ROOT / "kubernetes/apps/main"
KOPIUR_STAGE0_BASE = ROOT / "kubernetes/apps/base/system/kopiur"
STAGE0_README = KOPIUR_STAGE0_BASE / "README.md"

PILOT_APP = "autobrr"
PILOT_NS = "downloads"

# Pilot's live VolSync schedules from the overlay (must stay untouched).
PILOT_VOLSYNC_CEPH = "45 */4 * * *"
PILOT_VOLSYNC_MINIO = "30 */6 * * *"
PILOT_VOLSYNC_R2 = "45 3 * * *"

# Component defaults that keep kopiur off those minutes via the HOUR field.
EXPECTED_KOPIUR_CEPH_CRON = "H 1-23/4 * * *"
EXPECTED_KOPIUR_R2_CRON = "H 4 * * *"

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
            psc.get("runAsUser") == 1000
            and psc.get("runAsGroup") == 1000
            and psc.get("fsGroup") == 1000,
            f"{dest}: podSecurityContext must be uid/gid/fsGroup 1000, got {psc}",
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
    schedules = {s["metadata"]["name"]: s for s in by_kind(docs, "SnapshotSchedule")}
    for dest, expected_cron in (
        ("ceph", EXPECTED_KOPIUR_CEPH_CRON),
        ("r2", EXPECTED_KOPIUR_R2_CRON),
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

    # Structural hour non-overlap with the pilot's VolSync schedules.
    k_ceph_hours = cron_hours(EXPECTED_KOPIUR_CEPH_CRON)
    # VolSync ceph "45 */4 * * *" -> hours 0,4,8,12,16,20
    v_ceph_hours = cron_hours(PILOT_VOLSYNC_CEPH)
    require(
        k_ceph_hours.isdisjoint(v_ceph_hours),
        f"kopiur ceph hours {sorted(k_ceph_hours)} overlap volsync {sorted(v_ceph_hours)}",
    )
    require(
        k_ceph_hours == {1, 5, 9, 13, 17, 21},
        f"kopiur ceph hours must be the odd 4h slots, got {sorted(k_ceph_hours)}",
    )

    k_r2_hours = cron_hours(EXPECTED_KOPIUR_R2_CRON)
    v_r2_hours = cron_hours(PILOT_VOLSYNC_R2)
    require(
        k_r2_hours.isdisjoint(v_r2_hours),
        f"kopiur r2 hours {sorted(k_r2_hours)} overlap volsync {sorted(v_r2_hours)}",
    )
    require(k_r2_hours == {4}, f"kopiur r2 hour must be 4, got {sorted(k_r2_hours)}")
    require(v_r2_hours == {3}, f"pilot volsync r2 hour must stay 3, got {sorted(v_r2_hours)}")


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


def test_volsync_pilot_still_triple(vs_docs: list[dict[str, Any]]) -> None:
    """autobrr's VolSync render still has ceph+minio+r2 sources + one destination."""
    sources = by_kind(vs_docs, "ReplicationSource")
    dests = by_kind(vs_docs, "ReplicationDestination")
    names = sorted(s["metadata"]["name"] for s in sources)
    require(
        names == [f"{PILOT_APP}-ceph", f"{PILOT_APP}-minio", f"{PILOT_APP}-r2"],
        f"volsync must still emit all three sources for the pilot, got {names}",
    )
    require(len(dests) == 1, f"volsync must still emit one ReplicationDestination, got {len(dests)}")
    require(
        dests[0]["metadata"]["name"] == f"{PILOT_APP}-dst",
        f"volsync destination must remain {PILOT_APP}-dst, got {dests[0]['metadata']['name']}",
    )
    # Schedules match the overlay's substitute map (untouched).
    by_name = {s["metadata"]["name"]: s for s in sources}
    require(
        by_name[f"{PILOT_APP}-ceph"]["spec"]["trigger"]["schedule"] == PILOT_VOLSYNC_CEPH,
        "pilot volsync ceph schedule drifted",
    )
    require(
        by_name[f"{PILOT_APP}-minio"]["spec"]["trigger"]["schedule"] == PILOT_VOLSYNC_MINIO,
        "pilot volsync minio schedule drifted",
    )
    require(
        by_name[f"{PILOT_APP}-r2"]["spec"]["trigger"]["schedule"] == PILOT_VOLSYNC_R2,
        "pilot volsync r2 schedule drifted",
    )


def test_autobrr_overlay_wiring() -> None:
    docs = load_multi(AUTOBRR_OVERLAY)
    require(len(docs) == 1, f"autobrr overlay must be one Kustomization, got {len(docs)}")
    ks = docs[0]
    require(ks.get("kind") == "Kustomization", "autobrr overlay kind")
    require(ks["metadata"]["name"] == PILOT_APP, "autobrr metadata.name")
    require(ks["metadata"]["namespace"] == PILOT_NS, "autobrr metadata.namespace")
    spec = ks["spec"]
    require(spec.get("targetNamespace") == PILOT_NS, "targetNamespace")
    require(spec.get("prune") is True, "prune must stay true")
    require(spec.get("wait") is False, "wait must be false")

    components = spec.get("components") or []
    require(
        components
        == [
            "../../../../../components/volsync",
            "../../../../../components/kopiur",
        ],
        f"components must be volsync THEN kopiur (both), got {components}",
    )

    deps = {(d.get("name"), d.get("namespace")) for d in (spec.get("dependsOn") or [])}
    require(
        ("volsync", "system") in deps,
        f"must still dependOn volsync/system, got {deps}",
    )
    require(
        ("kopiur-repository", "system") in deps,
        f"must dependOn kopiur-repository/system (webhook Fail), got {deps}",
    )
    require(
        ("kopiur-credentials", "downloads") in deps,
        f"must dependOn kopiur-credentials/downloads, got {deps}",
    )
    # Must NOT dependOn the bare 'kopiur' operator Kustomization name alone as
    # a substitute for repository - repository already chains to the operator.
    # (Depending on both is fine; depending on neither is not.)
    require(
        ("kopiur-repository", "system") in deps,
        "kopiur-repository dependency required",
    )

    sub = ((spec.get("postBuild") or {}).get("substitute")) or {}
    require(sub.get("APP") == PILOT_APP or sub.get("APP") == "*app", "APP substitute")
    # YAML anchor resolves on parse for some loaders; accept either the alias
    # string or the resolved name - safe_load resolves anchors.
    require(sub.get("APP") == PILOT_APP, f"APP must resolve to {PILOT_APP}, got {sub.get('APP')!r}")
    require(
        sub.get("VOLSYNC_SCHEDULE_CEPH") == PILOT_VOLSYNC_CEPH,
        f"VOLSYNC_SCHEDULE_CEPH must stay {PILOT_VOLSYNC_CEPH!r}, got {sub.get('VOLSYNC_SCHEDULE_CEPH')!r}",
    )
    require(
        sub.get("VOLSYNC_SCHEDULE_MINIO") == PILOT_VOLSYNC_MINIO,
        "VOLSYNC_SCHEDULE_MINIO must stay untouched",
    )
    require(
        sub.get("VOLSYNC_SCHEDULE_R2") == PILOT_VOLSYNC_R2,
        "VOLSYNC_SCHEDULE_R2 must stay untouched",
    )
    # No KOPIUR_SCHEDULE_* overrides - component defaults own non-collision.
    require(
        not any(k.startswith("KOPIUR_SCHEDULE") for k in sub),
        f"pilot must not override KOPIUR_SCHEDULE_*; got {sorted(sub)}",
    )


def test_autobrr_still_onboarded() -> None:
    """autobrr remains a kopiur+volsync dual-backup volume after Stage 2."""
    onboarded: list[tuple[str, str, Path]] = []
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
                    onboarded.append((ns, name, path))
    keys = {(ns, name) for ns, name, _ in onboarded}
    require(
        (PILOT_NS, PILOT_APP) in keys,
        f"Stage 1 pilot {PILOT_NS}/{PILOT_APP} must still include components/kopiur, "
        f"got {sorted(f'{ns}/{name}' for ns, name in keys)}",
    )
    # autobrr's own components list is still volsync THEN kopiur.
    docs = load_multi(AUTOBRR_OVERLAY)
    components = docs[0]["spec"].get("components") or []
    require(
        components
        == [
            "../../../../../components/volsync",
            "../../../../../components/kopiur",
        ],
        f"autobrr components must stay volsync+kopiur, got {components}",
    )

    dl = yaml.safe_load(DOWNLOADS_OVERLAY_KUST.read_text())
    resources = dl.get("resources") or []
    require("./autobrr.yaml" in resources, "downloads overlay must list autobrr")
    require(
        "./kopiur-credentials.yaml" in resources,
        "downloads overlay must list kopiur-credentials scaffolding",
    )


def test_credentials_pilot_scoped() -> None:
    # ClusterSecretStore restricted to downloads.
    store_docs = kustomize_build(KOPIUR_SYSTEM_STORE)
    stores = by_kind(store_docs, "ClusterSecretStore")
    require(len(stores) == 1, f"expected 1 ClusterSecretStore, got {len(stores)}")
    store = stores[0]
    require(store["metadata"]["name"] == "kopiur-system-secrets", "store name")
    conditions = store.get("spec", {}).get("conditions") or []
    require(len(conditions) == 1, f"store must have exactly one condition, got {conditions}")
    nss = conditions[0].get("namespaces") or []
    require(
        nss == [PILOT_NS],
        f"store conditions.namespaces must be exactly [{PILOT_NS}] (load-bearing), got {nss}",
    )
    provider = (store.get("spec") or {}).get("provider") or {}
    require("kubernetes" in provider, "store must be kubernetes provider")
    k8s = provider["kubernetes"]
    require(
        k8s.get("remoteNamespace") == "system",
        f"remoteNamespace must be system, got {k8s.get('remoteNamespace')!r}",
    )
    sa = (k8s.get("auth") or {}).get("serviceAccount") or {}
    require(
        sa.get("name") == "external-secrets" and sa.get("namespace") == "security",
        f"store must reuse ESO SA, got {sa}",
    )

    # Security overlay wires the store Kustomization with dependsOn external-secrets.
    sec_docs = load_multi(SECURITY_ES_OVERLAY)
    by_name = {d["metadata"]["name"]: d for d in sec_docs if d.get("kind") == "Kustomization"}
    require(
        "kopiur-system-secrets" in by_name,
        f"security/external-secrets overlay must define kopiur-system-secrets, got {sorted(by_name)}",
    )
    kss = by_name["kopiur-system-secrets"]["spec"]
    require(
        kss.get("path")
        == "./kubernetes/apps/base/security/external-secrets/stores/kopiur-system-secrets",
        f"unexpected store path {kss.get('path')!r}",
    )
    deps = {(d.get("name"), d.get("namespace")) for d in (kss.get("dependsOn") or [])}
    require(("external-secrets", "security") in deps, f"store dependsOn missing, got {deps}")

    # Credentials overlay: downloads-only, dependsOn both stores.
    cred_overlay = load_multi(KOPIUR_CREDS_OVERLAY)
    require(len(cred_overlay) == 1, "kopiur-credentials overlay must be one doc")
    co = cred_overlay[0]
    require(co["metadata"]["name"] == "kopiur-credentials", "creds overlay name")
    require(co["metadata"]["namespace"] == PILOT_NS, "creds overlay namespace")
    require(co["spec"].get("targetNamespace") == PILOT_NS, "creds targetNamespace")
    require(
        co["spec"].get("path") == "./kubernetes/apps/base/downloads/kopiur-credentials/app",
        f"unexpected creds path {co['spec'].get('path')!r}",
    )
    cdeps = {(d.get("name"), d.get("namespace")) for d in (co["spec"].get("dependsOn") or [])}
    require(
        ("onepassword-store", "security") in cdeps
        and ("kopiur-system-secrets", "security") in cdeps,
        f"creds dependsOn must include both stores, got {cdeps}",
    )

    # Three ExternalSecrets with the exact target Secret names + property form.
    cred_docs = kustomize_build(KOPIUR_CREDS_APP)
    es = by_kind(cred_docs, "ExternalSecret")
    require(len(es) == 3, f"expected 3 ExternalSecrets, got {len(es)}")
    by_es = {e["metadata"]["name"]: e for e in es}
    require(
        set(by_es) == {"kopiur-ceph-bucket", "kopiur-ceph", "kopiur-r2"},
        f"ExternalSecret names unexpected: {sorted(by_es)}",
    )

    # Ceph bucket keys from OBC Secret via kubernetes store - NOT 1Password.
    bucket = by_es["kopiur-ceph-bucket"]
    bspec = bucket["spec"]
    require(
        (bspec.get("secretStoreRef") or {})
        == {"kind": "ClusterSecretStore", "name": "kopiur-system-secrets"},
        f"bucket storeRef must be kopiur-system-secrets, got {bspec.get('secretStoreRef')}",
    )
    require(
        (bspec.get("target") or {}).get("name") == "kopiur",
        f"bucket target Secret must be 'kopiur' (ClusterRepository auth name), "
        f"got {(bspec.get('target') or {}).get('name')!r}",
    )
    require("dataFrom" not in bspec, "bucket must not use dataFrom.extract")
    bdata = {d["secretKey"]: d["remoteRef"] for d in bspec.get("data") or []}
    require(
        set(bdata) == {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"},
        f"bucket keys unexpected: {sorted(bdata)}",
    )
    for sk, ref in bdata.items():
        require(
            ref.get("key") == "kopiur" and ref.get("property") == sk,
            f"bucket {sk} must read system/kopiur property {sk}, got {ref}",
        )

    # kopia password for ceph from 1Password item, target name matches ClusterRepository.
    ceph = by_es["kopiur-ceph"]
    cspec = ceph["spec"]
    require(
        (cspec.get("secretStoreRef") or {})
        == {"kind": "ClusterSecretStore", "name": "onepassword"},
        f"ceph password storeRef must be onepassword, got {cspec.get('secretStoreRef')}",
    )
    require((cspec.get("target") or {}).get("name") == "kopiur-ceph-secret", "ceph target name")
    require("dataFrom" not in cspec, "ceph must not use dataFrom.extract")
    cdata = {d["secretKey"]: d["remoteRef"] for d in cspec.get("data") or []}
    require(set(cdata) == {"KOPIA_PASSWORD"}, f"ceph secret keys unexpected: {sorted(cdata)}")
    require(
        cdata["KOPIA_PASSWORD"].get("key") == "kopiur-ceph"
        and cdata["KOPIA_PASSWORD"].get("property") == "KOPIA_PASSWORD",
        f"ceph password remoteRef unexpected: {cdata['KOPIA_PASSWORD']}",
    )

    r2 = by_es["kopiur-r2"]
    rspec = r2["spec"]
    require(
        (rspec.get("secretStoreRef") or {})
        == {"kind": "ClusterSecretStore", "name": "onepassword"},
        f"r2 storeRef must be onepassword, got {rspec.get('secretStoreRef')}",
    )
    require((rspec.get("target") or {}).get("name") == "kopiur-r2-secret", "r2 target name")
    require("dataFrom" not in rspec, "r2 must not use dataFrom.extract")
    rdata = {d["secretKey"]: d["remoteRef"] for d in rspec.get("data") or []}
    require(
        set(rdata)
        == {"KOPIA_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"},
        f"r2 secret keys unexpected: {sorted(rdata)}",
    )
    require(rdata["KOPIA_PASSWORD"].get("key") == "kopiur-r2", "r2 password item")
    require(rdata["AWS_ACCESS_KEY_ID"].get("property") == "R2_ACCESS_KEY_ID", "r2 access key property")
    require(
        rdata["AWS_SECRET_ACCESS_KEY"].get("property") == "R2_SECRET_ACCESS_KEY",
        "r2 secret key property",
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


def test_component_readme_records_pilot_risk() -> None:
    """Readme is the operator-facing contract for the credential compromise."""
    readme = (KOPIUR_COMPONENT / "Readme.md").read_text()
    require(
        (KOPIUR_COMPONENT / "Readme.md").is_file(),
        "components/kopiur/Readme.md must exist",
    )
    # Observable contract statements a future operator relies on - not a
    # source-grep of implementation tokens. These are the doc's own public
    # claims about scope and risk.
    require(
        "Stage 1" in readme and "autobrr" in readme,
        "Readme must name Stage 1 pilot autobrr",
    )
    require(
        "PILOT" in readme.upper() or "pilot" in readme,
        "Readme must mark credentials as pilot-scoped",
    )
    require(
        "credential" in readme.lower() and "downloads" in readme,
        "Readme must document downloads credential copy",
    )
    require(
        "Stage 3" in readme,
        "Readme must keep the Stage 3 credentials decision explicitly open",
    )
    require(
        "onPolicyDelete" in readme and "Retain" in readme,
        "Readme must document pinned deletion Retain",
    )
    require(
        "Ephemeral" in readme,
        "Readme must document Ephemeral cache (no standing cache PVCs)",
    )
    require(
        "MinIO" in readme or "minio" in readme,
        "Readme must state MinIO is out",
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

    pilot_env = {
        "APP": PILOT_APP,
        # Match the pilot overlay: VolSync schedules are substituted; kopiur
        # uses component defaults (no KOPIUR_SCHEDULE_* in the overlay).
        "VOLSYNC_SCHEDULE_CEPH": PILOT_VOLSYNC_CEPH,
        "VOLSYNC_SCHEDULE_MINIO": PILOT_VOLSYNC_MINIO,
        "VOLSYNC_SCHEDULE_R2": PILOT_VOLSYNC_R2,
        # cluster-secrets keys the volsync ExternalSecrets reference. Value is
        # a non-secret placeholder - we only need the render to complete so the
        # ReplicationSource contracts can be asserted.
        "SECRET_DOMAIN": "example.test",
    }

    try:
        kopiur_docs = render_with_substitute(KOPIUR_BACKUP, pilot_env)
        volsync_docs = render_with_substitute(VOLSYNC_BACKUP, pilot_env)
        cred_docs = kustomize_build(KOPIUR_CREDS_APP)
        store_docs = kustomize_build(KOPIUR_SYSTEM_STORE)
    except Failure as e:
        print(f"[FAIL] render: {e}")
        print("Summary: 0 passed, 1 failed")
        return 1

    run("component_layout", test_component_layout)
    run("rendered_pilot_kinds", lambda: test_rendered_pilot_kinds_and_names(kopiur_docs))
    run("policies_contract", lambda: test_policies_contract(kopiur_docs, volsync_docs))
    run("schedules_non_collision", lambda: test_schedules_non_collision(kopiur_docs))
    run("restore_passive", lambda: test_restore_passive_contract(kopiur_docs))
    run("volsync_pilot_still_triple", lambda: test_volsync_pilot_still_triple(volsync_docs))
    run("autobrr_overlay_wiring", test_autobrr_overlay_wiring)
    run("autobrr_still_onboarded", test_autobrr_still_onboarded)
    run("credentials_pilot_scoped", test_credentials_pilot_scoped)
    run(
        "no_embedded_credentials",
        lambda: test_no_embedded_credentials(
            [kopiur_docs, volsync_docs, cred_docs, store_docs]
        ),
    )
    run("stage0_tree_policy_free", test_stage0_tree_still_policy_free)
    run("stage0_readme_record_only", test_stage0_readme_record_only_still_true)
    run("component_readme_pilot_risk", test_component_readme_records_pilot_risk)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
