#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 3 (fleet-wide parallel run).

Stage 3 onboarded every remaining VolSync-protected claim onto kopiur ALONGSIDE
VolSync, namespace by namespace, one commit each. The safety property of the
whole stage was that BOTH engines stay live on every volume: nothing was retired
there, and no VolSync object was touched.

UPDATED 2026-09-01 for Stage 5, which began retiring VolSync per volume after
docs/backups/kopiur-restore-proof-2026-09-01.md restore-proved all 30 claims on
both destinations. The dual-engine invariant is therefore no longer universal -
it is now EXACT, against the RETIRED_CLAIMS set below (the pilot four). A claim
that goes single-engine without being listed there still fails, and so does a
listed claim that still renders VolSync.

No remaining deferred claims: Stage 4 onboarded both previously deferred
volumes, so kopiur went live on 30 of 30 VolSync-protected claims (alongside
untouched VolSync). The fleet is now 29: the `downloads/autobrr` APP was
removed on 2026-09-02 (captain decision - unused), taking its claim, its
overlay and therefore its kopiur onboarding with it. That is a different event
from a Stage 5 retirement, which only removes an ENGINE and always leaves the
claim in this set - see RETIRED_CLAIMS below. autobrr's kopia snapshots were
deliberately kept. This test pins the fleet coverage set and measured mover
identities; Stage 4 root-mover / GitOps annotation specifics for
`home-automation/matter-server` live in `kopiur-stage4-test.py`.

`selfhosted/changedetection-config` (Stage 4, 2026-08-31) never actually needed
a root mover: the app had NO securityContext at all, so it ran as the image
default (root) and wrote 2292 mode-0600 root-owned files, while the overlay
declared a 2000:2000 identity that nothing consumed. Giving the app the
1000:1000 identity its data already carried, and re-owning the volume to match,
removed the need for a 0:1000 mover entirely - which is why it arrives with no
KOPIUR_PUID/PGID override and no namespace-wide privileged-mover grant on
`selfhosted`.

`home-automation/matter-server` (Stage 4, 2026-08-31) stays root by design:
explicit `KOPIUR_PUID/PGID: 0` plus the namespace-wide privileged-mover
annotation on the home-automation overlay.

This test does not grep source text as its evidence. It:
  1. Parses every Flux Kustomization under kubernetes/apps/main into structured
     objects, recognising BOTH onboarding shapes - a `components:` entry, and a
     dedicated Kustomization whose `path` is components/kopiur/backup (used for
     an app's second claim, and for the retained pgadmin/calibre-web-automated
     split shape).
  2. Renders the real kustomize build of components/kopiur/backup and runs a
     Flux-shaped envsubst (including ${VAR:-default} and nested ${A:-${B}})
     under each onboarded claim's own substitute map.
  3. Asserts the Stage 3 contract on those rendered objects.

Note: EXPECTED_IDENTITY pins the kopiur mover (and the files on disk). VolSync
often rides a different mover identity because it stages a writable clone and
kubelet's fsGroup walk rewrites group bits before restic reads - so this file
does NOT require VOLSYNC_PUID == KOPIUR_PUID fleet-wide. The
selfhosted/obsidian-livesync VolSync alignment is pinned in
`selfhosted-backup-identity-test.py`.

The identity table below is the measured result of execing into every running
pod on 2026-08-30 and reading uid/gid/mode of every file - NOT the pods'
declared securityContexts, which are wrong for four of these volumes
(media/plex, media/tdarr-config and media/calibre-web-automated run
runAsUser 0 while owning their files 2000:2000; ai/hermes pins no runAsUser at
all). 10 of the 33 volumes surveyed needed a non-default identity, and kopia
treats a single unreadable file as fatal, so a wrong pair fails BOTH
destinations closed.

Live Snapshot Succeeded on both destinations is a POST-MERGE gate - nothing
here proves a backup has run. This pins the GitOps contract that must hold
before merge.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APPS_MAIN = REPO / "kubernetes" / "apps" / "main"
KOPIUR_BACKUP = REPO / "kubernetes" / "components" / "kopiur" / "backup"
KOPIUR_COMPONENT_SUFFIX = "components/kopiur"
KOPIUR_BACKUP_PATH = "kubernetes/components/kopiur/backup"
VOLSYNC_COMPONENT_SUFFIX = "components/volsync"
VOLSYNC_BACKUP_PATH = "kubernetes/components/volsync/backup"

# (namespace, claim) -> (uid, gid), measured live 2026-08-30.
EXPECTED_IDENTITY: dict[tuple[str, str], tuple[str, str]] = {
    ("ai", "hermes"): ("10000", "10000"),
    ("ai", "opencode"): ("1000", "1000"),
    ("ai", "repo-wiki"): ("1000", "1000"),
    ("database", "pgadmin"): ("5050", "5050"),
    ("downloads", "bazarr-config"): ("2000", "2000"),
    ("downloads", "lidarr-config"): ("2000", "2000"),
    ("downloads", "prowlarr-config"): ("3002", "3000"),
    ("downloads", "radarr-config"): ("2000", "2000"),
    ("downloads", "readarr-config"): ("2000", "2000"),
    ("downloads", "recyclarr-config"): ("2000", "2000"),
    ("downloads", "sabnzbd-config"): ("2000", "2000"),
    ("downloads", "sonarr-config"): ("2000", "2000"),
    ("home-automation", "esphome-config"): ("2000", "2000"),
    ("home-automation", "home-assistant"): ("1000", "1000"),
    ("home-automation", "matter-server"): ("0", "0"),
    ("home-automation", "zigbee2mqtt-data"): ("2000", "2000"),
    ("media", "calibre-web-automated"): ("2000", "2000"),
    ("media", "plex"): ("2000", "2000"),
    ("media", "seerr"): ("2000", "2000"),
    ("media", "tdarr-config"): ("2000", "2000"),
    # Measured 2026-08-31, after the Stage 4 re-own: all 3063 entries 1000:1000,
    # zero unreadable at 1000, all 5 setgid directories preserved.
    ("selfhosted", "changedetection-config"): ("1000", "1000"),
    ("selfhosted", "linkwarden"): ("1000", "1000"),
    ("selfhosted", "n8n"): ("1000", "1000"),
    ("selfhosted", "ntfy"): ("1000", "1000"),
    ("selfhosted", "obsidian-livesync"): ("5984", "5984"),
    ("selfhosted", "paperless-ngx"): ("1000", "1000"),
    ("selfhosted", "paperless-ngx-media"): ("1000", "1000"),
    ("selfhosted", "syncthing"): ("1000", "1000"),
    ("selfhosted", "syncthing-data"): ("1000", "1000"),
}

# Both previously deferred claims are now onboarded (Stage 4). Keep empty so a
# regression that re-defers either fails coverage_is_exact instead of silently
# shrinking the fleet pin.
DEFERRED_CLAIMS: set[tuple[str, str]] = set()

# --- Stage 5: claims VolSync has been RETIRED from (kopiur is their only engine) ---
#
# 27 claims across three waves. Three of the fleet's 29 stay dual-engine.
#
# Wave one - the pilot four, retired 2026-09-01. Each was restore-proven on
# BOTH destinations first (docs/backups/kopiur-restore-proof-2026-09-01.md) and
# re-proven after retirement
# (docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md). They were chosen
# for regenerable/reconstructible content and clean, unambiguous proofs.
#
# Wave two - four more, retired 2026-09-02 on the deeper proofs in
# docs/backups/kopiur-wave-two-reproof-2026-09-02.md part 4, recorded in
# docs/backups/kopiur-wave-two-retirement-2026-09-02.md. Unlike wave one these
# are NOT all regenerable: `ntfy` holds real auth state and
# `obsidian-livesync` is a genuine Obsidian vault, escalated to the captain and
# retired on an explicit decision after an objection. What authorises them is
# the completeness of the proof (100% of claim content, destination-identical
# in content AND metadata) plus, for the two `selfhosted` claims, a 2Gi PVC
# that cannot outgrow its 2Gi restore cache.
#
# Wave three - the remaining 19 eligible claims, retired 2026-09-04, on the
# same fleet restore proof plus a re-measured restore-cache audit. Recorded in
# docs/backups/kopiur-wave-three-retirement-2026-09-04.md. Shipped in three
# risk-tiered commits (11 ordinary config volumes, 4 large claims, 4 carrying
# real user-authored state) so a revert stays surgical; the tiering is a
# sequencing device, not three different evidence standards - every row cleared
# the same gate.
#
# Deliberately NOT here, and this is now the WHOLE of the dual-engine fleet:
# `selfhosted/paperless-ngx` (captain carve-out, stays dual-engine
# permanently), `selfhosted/syncthing-data` and
# `selfhosted/paperless-ngx-media` (wave two found their proofs cover nothing
# meaningful - 5 files / 531 B and 1 file / 0 B respectively - and both sit
# behind a restore cache they would cross the first time they hold real data;
# re-measured 2026-09-04 and both still empty, so both verdicts stand).
#
# This set is the fleet's legibility record: anything in it has ONE backup
# engine, and anything not in it must still have two. Adding a row here is an
# assertion that a restore proof exists for that volume - do not add one to
# quiet a failing test.
RETIRED_CLAIMS: set[tuple[str, str]] = {
    # wave one, 2026-09-01
    ("ai", "repo-wiki"),
    ("downloads", "recyclarr-config"),
    ("downloads", "sabnzbd-config"),
    ("media", "seerr"),
    # wave two, 2026-09-02
    #
    # ("downloads", "autobrr") was here until the autobrr APP was removed on
    # 2026-09-02 (captain decision - unused). It is gone rather than moved to
    # some "formerly retired" list because this set is asserted BOTH ways
    # against the live overlays: in-set means kopiur-only, not-in-set means
    # dual-engine, and a claim with no overlay at all is neither. Leaving the
    # row behind fails `volsync_still_on_every_unretired_claim`, which reads a
    # retired-but-unonboarded claim as a volume with NO backup - the exact
    # dangerous state this assertion exists to catch.
    #
    # Its kopia snapshots were deliberately KEPT (deletion.onPolicyDelete /
    # onScheduleDelete: Retain), but retained backup data is not a protected
    # claim, and nothing in this repo declares it any more.
    ("downloads", "prowlarr-config"),
    ("selfhosted", "ntfy"),
    ("selfhosted", "obsidian-livesync"),
    # wave three, 2026-09-04 - tier A, ordinary config volumes (11)
    ("database", "pgadmin"),
    ("downloads", "bazarr-config"),
    ("downloads", "lidarr-config"),
    ("downloads", "radarr-config"),
    ("downloads", "readarr-config"),
    ("downloads", "sonarr-config"),
    ("home-automation", "esphome-config"),
    ("home-automation", "matter-server"),
    ("home-automation", "zigbee2mqtt-data"),
    ("media", "tdarr-config"),
    ("selfhosted", "changedetection-config"),
    # wave three, 2026-09-04 - tier B, the large claims (4)
    ("ai", "hermes"),
    ("ai", "opencode"),
    ("media", "calibre-web-automated"),
    ("media", "plex"),
    # wave three, 2026-09-04 - tier C, real user-authored state (4)
    ("home-automation", "home-assistant"),
    ("selfhosted", "linkwarden"),
    ("selfhosted", "n8n"),
    # The 1Gi CONFIG claim. `selfhosted/syncthing-data` (15Gi, the synced
    # files) is a DIFFERENT claim and is deliberately absent - it stays
    # dual-engine.
    ("selfhosted", "syncthing"),
}

# One free hour per namespace: free of every VolSync destination and of
# kopiur's own ceph slots (01/05/09/13/17/21). The component default `H 4 * * *`
# would put every r2 policy into the hour that already carries VolSync's whole
# ceph slot plus 13 VolSync r2 runs.
EXPECTED_R2_HOUR = {
    "database": 7,
    "home-automation": 10,
    "downloads": 11,
    "selfhosted": 14,
    "media": 15,
    "ai": 19,
}

# Apps that keep the kopiur BACKUP half on a dedicated components/kopiur/backup
# Kustomization rather than inlining components/kopiur on the claim KS. The
# split was introduced under a former claim-side wait:true; that wait is gone,
# and the split is retained deliberately (see wave-three retirement doc).
SPLIT_BACKUP_APPS = {("database", "pgadmin"), ("media", "calibre-web-automated")}


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_multi(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def flux_kustomizations() -> list[tuple[dict[str, Any], Path]]:
    out: list[tuple[dict[str, Any], Path]] = []
    for path in sorted(APPS_MAIN.rglob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        try:
            docs = load_multi(path)
        except yaml.YAMLError:
            continue
        for d in docs:
            if d.get("kind") != "Kustomization":
                continue
            if str(d.get("apiVersion", "")).startswith("kustomize.config.k8s.io"):
                continue
            out.append((d, path))
    return out


def onboarded() -> list[tuple[str, str, dict[str, str], dict[str, Any], Path]]:
    """(namespace, claim, substitute, kustomization, path) for every kopiur onboarding."""
    found = []
    for d, path in flux_kustomizations():
        spec = d.get("spec") or {}
        comps = [c for c in (spec.get("components") or []) if isinstance(c, str)]
        via_component = any(c.rstrip("/").endswith(KOPIUR_COMPONENT_SUFFIX) for c in comps)
        via_path = str(spec.get("path") or "").strip("./") == KOPIUR_BACKUP_PATH
        if not (via_component or via_path):
            continue
        sub = {
            str(k): str(v)
            for k, v in (((spec.get("postBuild") or {}).get("substitute")) or {}).items()
        }
        app = sub.get("APP") or d.get("metadata", {}).get("name", "?")
        claim = sub.get("KOPIUR_CLAIM", app)
        ns = d.get("metadata", {}).get("namespace") or spec.get("targetNamespace") or "?"
        found.append((ns, claim, sub, d, path))
    return found


def test_coverage_is_exact() -> None:
    got = {(ns, claim) for ns, claim, _, _, _ in onboarded()}
    missing = set(EXPECTED_IDENTITY) - got
    extra = got - set(EXPECTED_IDENTITY)
    require(not missing, f"claims missing from kopiur: {sorted(missing)}")
    require(not extra, f"unexpected claims onboarded to kopiur: {sorted(extra)}")


def test_deferred_claims_absent() -> None:
    got = {(ns, claim) for ns, claim, _, _, _ in onboarded()}
    swept = DEFERRED_CLAIMS & got
    require(
        not swept,
        f"these stay VolSync-only until their deferred root-mover work lands: {sorted(swept)}",
    )


def test_identity_matches_measurement() -> None:
    for ns, claim, sub, _, path in onboarded():
        want = EXPECTED_IDENTITY.get((ns, claim))
        if want is None:
            continue
        got = (sub.get("KOPIUR_PUID", "1000"), sub.get("KOPIUR_PGID", "1000"))
        require(
            got == want,
            f"{ns}/{claim} ({path.name}): mover identity {got} != measured {want}",
        )


def test_schedules() -> None:
    for ns, claim, sub, _, path in onboarded():
        require(
            "KOPIUR_SCHEDULE_CEPH" not in sub,
            f"{ns}/{claim}: ceph must stay at the component default (structurally offset)",
        )
        r2 = sub.get("KOPIUR_SCHEDULE_R2")
        require(
            r2 is not None,
            f"{ns}/{claim}: KOPIUR_SCHEDULE_R2 must be set - the component default "
            f"`H 4 * * *` concentrates every r2 policy into VolSync's busiest hour",
        )
        m = re.fullmatch(r"H (\d{1,2}) \* \* \*", r2)
        require(m is not None, f"{ns}/{claim}: r2 must be a bare-H hourly cron, got {r2!r}")
        hour = int(m.group(1))
        want = EXPECTED_R2_HOUR.get(ns)
        require(
            want is not None and hour == want,
            f"{ns}/{claim}: r2 hour {hour} != the namespace's assigned free hour {want}",
        )


def test_no_inline_kopiur_is_wait_true() -> None:
    """No Kustomization may carry components/kopiur inline with wait: true.

    The component's standing Restore is a passive populator that reports
    Ready=False for the whole parallel run by design, and wait: true makes Flux
    assess every object in the inventory - so inline it would block forever.
    The two retained split apps must also keep a dedicated backup Kustomization.
    """
    for d, path in flux_kustomizations():
        spec = d.get("spec") or {}
        comps = [c for c in (spec.get("components") or []) if isinstance(c, str)]
        if not any(c.rstrip("/").endswith(KOPIUR_COMPONENT_SUFFIX) for c in comps):
            continue
        require(
            spec.get("wait") is not True,
            f"{path.name}: {d['metadata'].get('name')} has the kopiur component inline with "
            f"wait: true - the passive Restore never becomes Ready, so Flux would block. "
            f"Ship the kopiur half as its own wait: false Kustomization instead.",
        )
    split_names = {
        (d["metadata"].get("namespace"), str((d.get("spec") or {}).get("path") or ""))
        for d, _ in flux_kustomizations()
    }
    for ns, app in SPLIT_BACKUP_APPS:
        require(
            any(n == ns and KOPIUR_BACKUP_PATH in p for n, p in split_names),
            f"{ns}/{app} must keep a dedicated components/kopiur/backup Kustomization",
        )


def test_every_onboarded_kustomization_depends_on_repository() -> None:
    for ns, claim, _, d, path in onboarded():
        deps = {
            (x.get("name"), x.get("namespace"))
            for x in ((d.get("spec") or {}).get("dependsOn") or [])
        }
        require(
            ("kopiur-repository", "system") in deps,
            f"{ns}/{claim} ({path.name}): must dependOn kopiur-repository/system - the "
            f"admission webhook is failurePolicy: Fail, so without it the API server "
            f"rejects these CRs outright. got {sorted(deps)}",
        )
        require(
            not any(n == "kopiur-credentials" for n, _ in deps),
            f"{ns}/{claim}: standing per-namespace credentials were replaced by per-run "
            f"projection and must not come back",
        )


def _envsubst(text: str, env: dict[str, str]) -> str:
    pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-((?:[^{}]|\$\{[^}]*\})*))?\}")

    def one(s: str) -> str:
        def rep(m: re.Match[str]) -> str:
            name, default = m.group(1), m.group(2)
            if name in env:
                return env[name]
            return default if default is not None else m.group(0)

        return pattern.sub(rep, s)

    prev = text
    for _ in range(6):
        cur = one(prev)
        if cur == prev:
            return cur
        prev = cur
    return prev


_RAW_BUILD: str | None = None


def _kustomize_build(path: Path) -> str:
    """Build once and memoise - the path is the same for every onboarded claim.

    Mirrors kopiur-stage2-test.py: prefer `kustomize`, fall back to
    `kubectl kustomize`, and surface a build failure as a Failure rather than
    an unhandled CalledProcessError.
    """
    global _RAW_BUILD
    if _RAW_BUILD is not None:
        return _RAW_BUILD
    exe = shutil.which("kustomize")
    cmd = [exe, "build", str(path)] if exe else None
    if cmd is None:
        kubectl = shutil.which("kubectl")
        if not kubectl:
            raise Failure("neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(path)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Failure(f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()}")
    _RAW_BUILD = proc.stdout
    return _RAW_BUILD


def render(env: dict[str, str]) -> list[dict[str, Any]]:
    rendered = _envsubst(_kustomize_build(KOPIUR_BACKUP), env)
    unresolved = sorted(set(re.findall(r"\$\{[A-Za-z_][^}]*\}", rendered)))
    require(not unresolved, f"unresolved substitution tokens after envsubst: {unresolved}")
    return [d for d in yaml.safe_load_all(rendered) if d]


def test_rendered_objects_hold_the_contract() -> None:
    for ns, claim, sub, _, path in onboarded():
        if (ns, claim) not in EXPECTED_IDENTITY:
            # coverage_is_exact already reports an unexpected claim; skip here
            # so this test fails on contract violations rather than a KeyError.
            continue
        docs = render(sub)
        policies = [d for d in docs if d.get("kind") == "SnapshotPolicy"]
        schedules = [d for d in docs if d.get("kind") == "SnapshotSchedule"]
        restores = [d for d in docs if d.get("kind") == "Restore"]
        require(len(policies) == 2, f"{ns}/{claim}: expected 2 SnapshotPolicy, got {len(policies)}")
        require(len(schedules) == 2, f"{ns}/{claim}: expected 2 SnapshotSchedule")
        require(len(restores) == 1, f"{ns}/{claim}: expected 1 Restore")

        uid, gid = EXPECTED_IDENTITY[(ns, claim)]
        for p in policies:
            spec = p["spec"]
            sources = spec.get("sources") or []
            require(len(sources) == 1, f"{ns}/{claim}: 1:1 shape - exactly one source")
            require(
                (sources[0].get("pvc") or {}).get("name") == claim,
                f"{ns}/{claim}: policy PVC name must be the claim",
            )
            require(
                "repositories" not in spec,
                f"{ns}/{claim}: fan-out is not the shape used here",
            )
            psc = (spec.get("mover") or {}).get("podSecurityContext") or {}
            require(
                [str(psc.get("runAsUser")), str(psc.get("runAsGroup")), str(psc.get("fsGroup"))]
                == [uid, gid, gid],
                f"{ns}/{claim}: rendered mover identity {psc} != {uid}:{gid}",
            )
            cache = (spec.get("mover") or {}).get("cache") or {}
            require(
                cache.get("mode") == "Ephemeral",
                f"{ns}/{claim}: cache must be Ephemeral - Persistent would add a "
                f"standing PVC per volume, on top of VolSync's existing set",
            )
            require(
                (spec.get("credentialProjection") or {}).get("enabled") is True,
                f"{ns}/{claim}: credentialProjection must be enabled (consumer leg)",
            )
            require(
                (spec.get("deletion") or {}).get("onPolicyDelete") == "Retain",
                f"{ns}/{claim}: onPolicyDelete must stay Retain - a Snapshot CR owns its "
                f"kopia snapshot through a finalizer, and we run Flux with prune: true",
            )
        for s in schedules:
            require(
                (s["spec"].get("deletion") or {}).get("onScheduleDelete") == "Retain",
                f"{ns}/{claim}: onScheduleDelete must stay Retain",
            )


def volsync_covered() -> set[tuple[str, str]]:
    """(namespace, claim) pairs protected by VolSync via component or backup path.

    Mirrors onboarded() discovery for kopiur: a Flux Kustomization either lists
    components/volsync, or points its path at components/volsync/backup (the
    second-claim / split shape). Claim resolution is VOLSYNC_CLAIM if present,
    else APP - the same substitute-map rule kopiur uses with KOPIUR_CLAIM.
    """
    covered: set[tuple[str, str]] = set()
    for d, _path in flux_kustomizations():
        spec = d.get("spec") or {}
        comps = [c for c in (spec.get("components") or []) if isinstance(c, str)]
        via_component = any(
            c.rstrip("/").endswith(VOLSYNC_COMPONENT_SUFFIX) for c in comps
        )
        via_path = str(spec.get("path") or "").strip("./") == VOLSYNC_BACKUP_PATH
        if not (via_component or via_path):
            continue
        sub = {
            str(k): str(v)
            for k, v in (((spec.get("postBuild") or {}).get("substitute")) or {}).items()
        }
        app = sub.get("APP") or d.get("metadata", {}).get("name", "?")
        claim = sub.get("VOLSYNC_CLAIM", app)
        ns = d.get("metadata", {}).get("namespace") or spec.get("targetNamespace") or "?"
        covered.add((ns, claim))
    return covered


def test_volsync_still_on_every_unretired_claim() -> None:
    """VolSync still covers every onboarded claim EXCEPT the Stage 5 pilot four.

    Stage 3's original invariant was absolute - both engines on every volume,
    nothing retired. Stage 5 (2026-09-01) began retiring VolSync per volume, so
    the invariant is now exact rather than universal: the retired set is
    enumerated in RETIRED_CLAIMS and everything else must still be dual-engine.

    Stating it as an exact set is the point. A blanket "some claims may have no
    VolSync" would let the next accidental component deletion pass silently,
    which is precisely the failure this pin exists to catch. Both directions are
    checked: a claim retired without being listed here fails, and a claim listed
    here that still renders VolSync fails too (a half-reverted retirement).

    Covers both onboarding shapes: inline components/volsync, and path-based
    splits (pgadmin, calibre-web-automated, paperless-ngx-media, syncthing-data)
    whose kopiur half is a separate Kustomization. Building the VolSync-covered
    set from every Flux Kustomization means dropping volsync from a parent or
    deleting a second-claim volsync KS fails this pin rather than being skipped.
    """
    covered = volsync_covered()
    onboarded_claims = {(ns, claim) for ns, claim, _s, _d, _p in onboarded()}

    stale = sorted(RETIRED_CLAIMS - onboarded_claims)
    require(
        not stale,
        f"RETIRED_CLAIMS names claims that are not kopiur-onboarded at all: {stale}. "
        f"Retiring VolSync from a claim kopiur does not protect leaves it with NO backup.",
    )

    for ns, claim, _sub, _d, path in onboarded():
        if (ns, claim) in RETIRED_CLAIMS:
            require(
                (ns, claim) not in covered,
                f"{ns}/{claim} ({path.name}): listed in RETIRED_CLAIMS but a VolSync "
                f"sibling still covers it - either the retirement was half-reverted or "
                f"the claim should come off RETIRED_CLAIMS.",
            )
            continue
        require(
            (ns, claim) in covered,
            f"{ns}/{claim} ({path.name}): no VolSync sibling covering this claim, and it "
            f"is not in RETIRED_CLAIMS - both engines must run on every volume that has "
            f"not been through a Stage 5 retirement with its own restore proof "
            f"(docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md).",
        )

    retired_now = onboarded_claims - covered
    require(
        retired_now == RETIRED_CLAIMS,
        f"single-engine set drifted from RETIRED_CLAIMS: "
        f"unexpected {sorted(retired_now - RETIRED_CLAIMS)}, "
        f"missing {sorted(RETIRED_CLAIMS - retired_now)}",
    )


def main() -> int:
    tests: list[str] = []
    failures: list[str] = []

    def run(name: str, fn: Any) -> None:
        tests.append(name)
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as e:
            failures.append(name)
            print(f"[FAIL] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append(name)
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")

    run("coverage_is_exact", test_coverage_is_exact)
    run("deferred_claims_absent", test_deferred_claims_absent)
    run("identity_matches_measurement", test_identity_matches_measurement)
    run("schedules", test_schedules)
    run("no_inline_kopiur_is_wait_true", test_no_inline_kopiur_is_wait_true)
    run("depends_on_repository", test_every_onboarded_kustomization_depends_on_repository)
    run("rendered_objects_hold_the_contract", test_rendered_objects_hold_the_contract)
    run("volsync_still_on_every_unretired_claim", test_volsync_still_on_every_unretired_claim)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
