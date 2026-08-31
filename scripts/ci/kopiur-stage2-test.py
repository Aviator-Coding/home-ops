#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 2 (fidelity subject + restore drill).

Stage 2 is the migration acceptance gate: prove a restore from BOTH kopiur
destinations (ceph and r2) on a volume that actually holds data, and record the
drill. Captain decision `kopiur-stage2-empty-pilot` authorised ONE additional
volume - downloads/sabnzbd-config - because the Stage 1 pilot (autobrr) holds
zero files. This is NOT permission to begin Stage 3.

This test does not grep source text as its evidence. It:
  1. Renders the real kustomize builds Flux would apply for the kopiur and
     volsync backup components under the sabnzbd substitute map
     (KOPIUR_CLAIM=sabnzbd-config, KOPIUR_PUID/PGID=2000).
  2. Parses the sabnzbd Flux overlay, every apps/main overlay's components
     list, the sabnzbd workload securityContext, and the Stage 2 drill
     document into structured objects / measured fields.
  3. Asserts the Stage 2 safety contract on those objects:
       - exactly two onboarded volumes: downloads/autobrr + downloads/sabnzbd
       - sabnzbd components are volsync THEN kopiur; volsync stays triple-dest
       - rendered SnapshotPolicy PVC name is sabnzbd-config (claim override)
       - rendered mover podSecurityContext is uid/gid/fsGroup 2000 (finding 2)
       - sabnzbd workload securityContext is 2000 (the reason the override
         is load-bearing); autobrr remains at the component default 1000
       - no KOPIUR_SCHEDULE_* overrides; hour non-collision with sabnzbd VolSync
       - dependsOn includes kopiur-repository + volsync, and NOT
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
VOLSYNC_BACKUP = ROOT / "kubernetes/components/volsync/backup"
SABNZBD_OVERLAY = ROOT / "kubernetes/apps/main/downloads/sabnzbd.yaml"
AUTOBRR_OVERLAY = ROOT / "kubernetes/apps/main/downloads/autobrr.yaml"
SABNZBD_HR = ROOT / "kubernetes/apps/base/downloads/sabnzbd/app/helmrelease.yaml"
AUTOBRR_HR = ROOT / "kubernetes/apps/base/downloads/autobrr/app/helmrelease.yaml"
APPS_MAIN = ROOT / "kubernetes/apps/main"
DRILL_DOC = ROOT / "docs/backups/kopiur-restore-drill-2026-08-30.md"
VOLSYNC_DRILL = ROOT / "docs/backups/restore-drill-2026-08-23.md"
COMPONENT_README = ROOT / "kubernetes/components/kopiur/Readme.md"
STAGE0_README = ROOT / "kubernetes/apps/base/system/kopiur/README.md"
AGENTS = ROOT / "AGENTS.md"

STAGE2_APP = "sabnzbd"
STAGE2_NS = "downloads"
STAGE2_CLAIM = "sabnzbd-config"
STAGE2_PUID = 2000
STAGE2_PGID = 2000

STAGE1_APP = "autobrr"
STAGE1_NS = "downloads"

# sabnzbd overlay VolSync schedules (must stay untouched by kopiur onboarding).
SAB_VOLSYNC_CEPH = "30 */4 * * *"
SAB_VOLSYNC_MINIO = "15 */6 * * *"
SAB_VOLSYNC_R2 = "30 3 * * *"

EXPECTED_KOPIUR_CEPH_CRON = "H 1-23/4 * * *"
EXPECTED_KOPIUR_R2_CRON = "H 4 * * *"

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


def test_exactly_two_onboarded() -> None:
    onboarded = flux_overlays_with_kopiur()
    keys = {(ns, name) for ns, name, _ in onboarded}
    require(
        keys == STAGE2_ONBOARDED,
        f"Stage 2 onboarded set must be exactly {sorted(f'{n}/{a}' for n, a in STAGE2_ONBOARDED)}, "
        f"got {sorted(f'{n}/{a}' for n, a in keys)}",
    )
    # Paths must be the two known overlays (no surprise third file name).
    paths = {p.resolve() for _, _, p in onboarded}
    require(
        paths
        == {
            AUTOBRR_OVERLAY.resolve(),
            SABNZBD_OVERLAY.resolve(),
        },
        f"unexpected overlay paths for kopiur onboard: {sorted(str(p) for p in paths)}",
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
            "../../../../../components/volsync",
            "../../../../../components/kopiur",
        ],
        f"components must be volsync THEN kopiur (both, parallel), got {components}",
    )

    deps = {(d.get("name"), d.get("namespace")) for d in (spec.get("dependsOn") or [])}
    require(("volsync", "system") in deps, f"must dependOn volsync/system, got {deps}")
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
        sub.get("VOLSYNC_CLAIM") == STAGE2_CLAIM,
        f"VOLSYNC_CLAIM must stay {STAGE2_CLAIM!r}, got {sub.get('VOLSYNC_CLAIM')!r}",
    )
    require(
        sub.get("KOPIUR_CLAIM") == STAGE2_CLAIM,
        f"KOPIUR_CLAIM must override to {STAGE2_CLAIM!r} (claim != app name), "
        f"got {sub.get('KOPIUR_CLAIM')!r}",
    )
    require(
        sub.get("VOLSYNC_SCHEDULE_CEPH") == SAB_VOLSYNC_CEPH,
        f"VOLSYNC_SCHEDULE_CEPH must stay {SAB_VOLSYNC_CEPH!r}, got {sub.get('VOLSYNC_SCHEDULE_CEPH')!r}",
    )
    require(
        sub.get("VOLSYNC_SCHEDULE_MINIO") == SAB_VOLSYNC_MINIO,
        "VOLSYNC_SCHEDULE_MINIO must stay untouched",
    )
    require(
        sub.get("VOLSYNC_SCHEDULE_R2") == SAB_VOLSYNC_R2,
        "VOLSYNC_SCHEDULE_R2 must stay untouched",
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
    # No schedule overrides - component defaults own hour non-collision.
    require(
        not any(k.startswith("KOPIUR_SCHEDULE") for k in sub),
        f"sabnzbd must not override KOPIUR_SCHEDULE_*; got {sorted(sub)}",
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
        ("ceph", EXPECTED_KOPIUR_CEPH_CRON),
        ("r2", EXPECTED_KOPIUR_R2_CRON),
    ):
        cron = ((by_name[f"{STAGE2_APP}-{dest}"]["spec"].get("schedule")) or {}).get("cron")
        require(cron == expected, f"{dest}: cron must be {expected!r}, got {cron!r}")
        minute = cron.split()[0]
        require(minute == "H", f"{dest}: minute must be bare H, got {minute!r}")

    # Hour non-overlap with sabnzbd VolSync.
    k_ceph = cron_hours(EXPECTED_KOPIUR_CEPH_CRON)
    v_ceph = cron_hours(SAB_VOLSYNC_CEPH)
    require(
        k_ceph.isdisjoint(v_ceph),
        f"kopiur ceph hours {sorted(k_ceph)} overlap sabnzbd volsync {sorted(v_ceph)}",
    )
    k_r2 = cron_hours(EXPECTED_KOPIUR_R2_CRON)
    v_r2 = cron_hours(SAB_VOLSYNC_R2)
    require(
        k_r2.isdisjoint(v_r2),
        f"kopiur r2 hours {sorted(k_r2)} overlap sabnzbd volsync {sorted(v_r2)}",
    )

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


def test_volsync_sabnzbd_still_triple(vs_docs: list[dict[str, Any]]) -> None:
    sources = by_kind(vs_docs, "ReplicationSource")
    dests = by_kind(vs_docs, "ReplicationDestination")
    names = sorted(s["metadata"]["name"] for s in sources)
    # VolSync names objects from APP, not VOLSYNC_CLAIM, for the source name;
    # the claim itself is inside the restic source PVC ref.
    require(
        names == [f"{STAGE2_APP}-ceph", f"{STAGE2_APP}-minio", f"{STAGE2_APP}-r2"],
        f"volsync must still emit all three sources, got {names}",
    )
    require(len(dests) == 1, f"volsync must still emit one destination, got {len(dests)}")
    by_name = {s["metadata"]["name"]: s for s in sources}
    require(
        by_name[f"{STAGE2_APP}-ceph"]["spec"]["trigger"]["schedule"] == SAB_VOLSYNC_CEPH,
        "sabnzbd volsync ceph schedule drifted",
    )
    require(
        by_name[f"{STAGE2_APP}-minio"]["spec"]["trigger"]["schedule"] == SAB_VOLSYNC_MINIO,
        "sabnzbd volsync minio schedule drifted",
    )
    require(
        by_name[f"{STAGE2_APP}-r2"]["spec"]["trigger"]["schedule"] == SAB_VOLSYNC_R2,
        "sabnzbd volsync r2 schedule drifted",
    )
    # Claim name on each source must be sabnzbd-config (spec.sourcePVC).
    for s in sources:
        pvc = s["spec"].get("sourcePVC")
        require(
            pvc == STAGE2_CLAIM,
            f"{s['metadata']['name']}: volsync sourcePVC must be {STAGE2_CLAIM!r}, got {pvc!r}",
        )


def test_workload_identity_matches_override() -> None:
    """sabnzbd runs as 2000; that is why KOPIUR_PUID/PGID=2000 is required.

    autobrr stays at a non-2000 identity so the stage-1 default path remains valid.
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

    # autobrr must NOT need the 2000 override (its files are empty anyway, but the
    # default path should still render at 1000).
    auto_docs = render_with_substitute(
        KOPIUR_BACKUP,
        {
            "APP": STAGE1_APP,
            "SECRET_DOMAIN": "example.test",
        },
    )
    for p in by_kind(auto_docs, "SnapshotPolicy"):
        psc = ((p["spec"].get("mover") or {}).get("podSecurityContext")) or {}
        require(
            psc.get("runAsUser") == 1000
            and psc.get("runAsGroup") == 1000
            and psc.get("fsGroup") == 1000,
            f"autobrr must keep default mover identity 1000, got {psc}",
        )
        claim = ((p["spec"].get("sources") or [{}])[0].get("pvc") or {}).get("name")
        require(
            claim == STAGE1_APP,
            f"autobrr claim must default to app name, got {claim!r}",
        )


def test_default_puid_without_override_is_1000() -> None:
    """Negative control: rendering sabnzbd WITHOUT KOPIUR_PUID stays at 1000.

    This is the failure mode finding 2 measured live - the test pins that the
    component default really is 1000 so the overlay override is doing real work.
    """
    docs = render_with_substitute(
        KOPIUR_BACKUP,
        {
            "APP": STAGE2_APP,
            "KOPIUR_CLAIM": STAGE2_CLAIM,
            # deliberately omit KOPIUR_PUID/PGID
            "SECRET_DOMAIN": "example.test",
        },
    )
    for p in by_kind(docs, "SnapshotPolicy"):
        psc = ((p["spec"].get("mover") or {}).get("podSecurityContext")) or {}
        require(
            psc.get("runAsUser") == 1000
            and psc.get("runAsGroup") == 1000
            and psc.get("fsGroup") == 1000,
            f"without override, mover must default to 1000 (finding 2 baseline), got {psc}",
        )
        claim = ((p["spec"].get("sources") or [{}])[0].get("pvc") or {}).get("name")
        require(claim == STAGE2_CLAIM, f"claim override must still apply, got {claim!r}")


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
    """Operator-facing docs are part of the Stage 2 hand-off contract."""
    for path, label in (
        (COMPONENT_README, "components/kopiur/Readme.md"),
        (STAGE0_README, "system/kopiur/README.md"),
        (AGENTS, "AGENTS.md"),
    ):
        require(path.is_file(), f"missing {label}")
        text = path.read_text()
        lowered = text.lower()
        require(
            "sabnzbd" in lowered,
            f"{label} must name sabnzbd as the Stage 2 volume",
        )
        require(
            "two" in lowered or "2" in text,
            f"{label} must reflect two-volume coverage (not still 'exactly one')",
        )
        # Must not still claim single-volume-only without Stage 2 update.
        require(
            not re.search(
                r"live on exactly ONE volume",
                text,
            ),
            f"{label} must not still say 'exactly ONE volume'",
        )
        require(
            "KOPIUR_PUID" in text or "mover" in lowered and "1000" in text,
            f"{label} must document the mover-identity trap",
        )
        require(
            "stage 3" in lowered,
            f"{label} must keep Stage 3 closed / captain-gated",
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

    sab_env = {
        "APP": STAGE2_APP,
        "VOLSYNC_CLAIM": STAGE2_CLAIM,
        "KOPIUR_CLAIM": STAGE2_CLAIM,
        "KOPIUR_PUID": str(STAGE2_PUID),
        "KOPIUR_PGID": str(STAGE2_PGID),
        "VOLSYNC_SCHEDULE_CEPH": SAB_VOLSYNC_CEPH,
        "VOLSYNC_SCHEDULE_MINIO": SAB_VOLSYNC_MINIO,
        "VOLSYNC_SCHEDULE_R2": SAB_VOLSYNC_R2,
        "SECRET_DOMAIN": "example.test",
    }

    try:
        kopiur_docs = render_with_substitute(KOPIUR_BACKUP, sab_env)
        volsync_docs = render_with_substitute(VOLSYNC_BACKUP, sab_env)
    except Failure as e:
        print(f"[FAIL] render: {e}")
        print("Summary: 0 passed, 1 failed")
        return 1

    run("exactly_two_onboarded", test_exactly_two_onboarded)
    run("sabnzbd_overlay_wiring", test_sabnzbd_overlay_wiring)
    run(
        "rendered_claim_override_and_puid",
        lambda: test_rendered_claim_override_and_puid(kopiur_docs),
    )
    run(
        "volsync_sabnzbd_still_triple",
        lambda: test_volsync_sabnzbd_still_triple(volsync_docs),
    )
    run("workload_identity_matches_override", test_workload_identity_matches_override)
    run("default_puid_without_override_is_1000", test_default_puid_without_override_is_1000)
    run("drill_document_contract", test_drill_document_contract)
    run("operator_docs_reflect_stage2", test_operator_docs_reflect_stage2)
    run(
        "no_embedded_credentials",
        lambda: test_no_embedded_credentials([kopiur_docs, volsync_docs]),
    )

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
