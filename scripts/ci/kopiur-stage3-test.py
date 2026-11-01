#!/usr/bin/env python3
# ci: gate
"""Semantic regression test for kopiur Stage 3 (fleet-wide parallel run).

Prevents: a claim silently falling out of kopiur backup coverage, backing up under
the wrong mover identity, or an app and its claim pruned because its overlay file is
not listed in its namespace kustomization.yaml.

Stage 3 onboarded every remaining VolSync-protected claim onto kopiur ALONGSIDE
VolSync, namespace by namespace, one commit each. The safety property of the
whole stage was that BOTH engines stay live on every volume: nothing was retired
there, and no VolSync object was touched.

UPDATED 2026-09-01 for Stage 5, which began retiring VolSync per volume after
.agents/skills/kopiur-backups/references/proof-ledger.md restore-proved all 30 claims on
both destinations. The dual-engine invariant is therefore no longer universal -
it is now EXACT, against the RETIRED_CLAIMS set below. A claim
that goes single-engine without being listed there still fails, and so does a
listed claim that still renders VolSync. Since issue #2202 PR-6 removed the
operator and component, no overlay may render VolSync at all, and
RETIRED_CLAIMS | NEVER_VOLSYNC must equal the onboarded fleet exactly.

No remaining deferred claims: Stage 4 onboarded both previously deferred
volumes, so kopiur went live on 30 of 30 VolSync-protected claims (alongside
untouched VolSync). The fleet is now 29: the `downloads/autobrr` APP was
removed on 2026-09-02 (captain decision - unused), taking its claim, its
overlay and therefore its kopiur onboarding with it. That is a different event
from a Stage 5 retirement, which only removes an ENGINE and always leaves the
claim in this set - see RETIRED_CLAIMS below. autobrr's kopia snapshots were
deliberately kept. This test pins the fleet coverage set and measured mover
identities. The privileged-movers namespace annotation that the
`home-automation/matter-server` root mover needs is skill `kopiur-backups`,
references/onboarding.md (a missing one stalls the snapshot and fires
KopiurBackupStaleCeph).

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
does NOT require VOLSYNC_PUID == KOPIUR_PUID fleet-wide. No claim runs VolSync
any more (the last, paperless-ngx, left on 2026-11-01, issue #2202 PR-4), so no
VolSync mover identity is pinned.

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
# Removed with the operator in issue #2202 PR-6. Any Flux Kustomization that still
# names it (component, path or the old operator app) is a half-revert.
VOLSYNC_PATHS = ("components/volsync", "kubernetes/apps/base/system/volsync/app")

# (namespace, claim) -> (uid, gid), measured live 2026-08-30.
EXPECTED_IDENTITY: dict[tuple[str, str], tuple[str, str]] = {
    ("ai", "hermes"): ("10000", "10000"),
    ("ai", "cliproxyapi"): ("1000", "1000"),
    ("ai", "opencode"): ("1000", "1000"),
    ("ai", "repo-wiki"): ("1000", "1000"),
    # database/falkordb removed with the app 2026-11-01 (owner); kopia snapshots kept under Retain; do not re-add.
    ("database", "pgadmin"): ("5050", "5050"),
    # Chainguard/distroless nonroot image (surrealdb/surrealdb). Read live
    # 2026-09-12 from the running pod's rendered securityContext
    # (spec.securityContext: {fsGroup: 65532}, container securityContext: {}),
    # corroborated by the app's own helmrelease.yaml comment "SurrealDB image
    # runs as UID 65532" - not a full byte-level file-ownership audit, because
    # the image ships no shell/coreutils to run one with.
    ("database", "surrealdb"): ("65532", "65532"),
    ("downloads", "bazarr-config"): ("2000", "2000"),
    ("downloads", "cleanuparr"): ("2000", "2000"),
    ("media", "wizarr"): ("2000", "2000"),
    ("downloads", "lidarr-config"): ("2000", "2000"),
    ("downloads", "prowlarr-config"): ("3002", "3000"),
    ("downloads", "radarr-config"): ("2000", "2000"),
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
# 30 claims across four waves; 28 are still in the fleet (autobrr and readarr left
# with their apps). None of the fleet stays dual-engine.
#
# Wave one - the pilot four, retired 2026-09-01. Each was restore-proven on
# BOTH destinations first (.agents/skills/kopiur-backups/references/proof-ledger.md) and
# re-proven after retirement
# (.agents/skills/kopiur-backups/references/retirement-and-removal.md). They were chosen
# for regenerable/reconstructible content and clean, unambiguous proofs.
#
# Wave two - four more, retired 2026-09-02 on the deeper proofs in
# .agents/skills/kopiur-backups/references/proof-ledger.md part 4, recorded in
# .agents/skills/kopiur-backups/references/retirement-and-removal.md. Unlike wave one these
# are NOT all regenerable: `ntfy` holds real auth state and
# `obsidian-livesync` is a genuine Obsidian vault, escalated to the captain and
# retired on an explicit decision after an objection. What authorises them is
# the completeness of the proof (100% of claim content, destination-identical
# in content AND metadata) plus, for the two `selfhosted` claims, a 2Gi PVC
# that cannot outgrow its 2Gi restore cache.
#
# Wave three - the remaining 19 eligible claims, retired 2026-09-04, on the
# same fleet restore proof plus a re-measured restore-cache audit. Recorded in
# that file. Shipped in three
# risk-tiered commits (11 ordinary config volumes, 4 large claims, 4 carrying
# real user-authored state) so a revert stays surgical; the tiering is a
# sequencing device, not three different evidence standards - every row cleared
# the same gate.
#
# Wave four - `selfhosted/paperless-ngx-media` (PR-2) and `selfhosted/syncthing-data`
# (PR-3), retired 2026-11-01 (issue #2202). `syncthing-data` was the same story at a
# 15Gi cache (5 files / 531 B, ceph and r2 identical to live); its claim is a plain
# PVC manifest under the `syncthing` Kustomization. For paperless-ngx-media, its kopiur cache was raised to the claim size (50Gi) and a scratch restore drill
# from both ceph and r2 passed at that cache under decision (b): the current
# contents (1 file, 0 B) plus the structural guarantee cache >= claim. Recorded in
# .agents/skills/kopiur-backups/references/proof-ledger.md. Its claim is chart-owned
# and the VolSync half was a backup-only Kustomization, so no PVC changed hands.
#
# `selfhosted/paperless-ngx` (PR-4, the former captain carve-out) closed wave four:
# the one PVC takeover (components/volsync/pvc.yaml -> components/kopiur/pvc, same
# claim name), after ceph, r2 and populator-path drills at a 10Gi cache, recorded in
# proof-ledger.md row 27. With it the dual-engine fleet is empty.
#
# This set is the fleet's legibility record. With NEVER_VOLSYNC it partitions the
# kopiur fleet exactly (no claim has two engines since 2026-11-01, and the operator
# is removed, issue #2202 PR-6). Adding a row here is an
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
    # row behind fails `no_overlay_renders_volsync`, which reads a
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
    # files) is a DIFFERENT claim with its own row below (wave four).
    ("selfhosted", "syncthing"),
    # wave four, 2026-11-01 - the chart-owned second volume of paperless-ngx (backup-only shape)
    ("selfhosted", "paperless-ngx-media"),
    # wave four, 2026-11-01 - the 15Gi files claim, owned by a plain PVC manifest (backup-only shape)
    ("selfhosted", "syncthing-data"),
    # wave four, 2026-11-01 - the 10Gi data claim, PVC takeover to components/kopiur/pvc (normal shape)
    ("selfhosted", "paperless-ngx"),
}

# --- claims that were NEVER VolSync-protected (single-engine from birth) ---
#
# A THIRD category, and it is deliberately not folded into RETIRED_CLAIMS above.
# Both sets describe a claim kopiur is the only engine for, but they assert
# completely different things:
#
#   RETIRED_CLAIMS - VolSync ran here and was REMOVED. Membership is a claim
#                    that a restore proof exists for that volume, and it is
#                    cross-checked against scripts/ci/kopiur-stage5-test.py's
#                    own RETIRED map, its live-measured capacities and its
#                    retirement records.
#   NEVER_VOLSYNC  - the claim was created after the migration, so VolSync was
#                    never wired onto it and nothing was removed. No restore
#                    proof is implied, and none of the Stage 5 retirement
#                    paperwork applies.
#
# Putting a new app in RETIRED_CLAIMS to quiet the dual-engine assertion would
# make both files claim a retirement that never happened and a proof that does
# not exist - so the distinction is kept in the types rather than in a comment.
#
# These claims also do NOT use components/kopiur/pvc: that Component exists to
# take over PVC ownership from volsync at retirement, and stage5's
# test_no_other_overlay_uses_the_pvc_component holds it to exactly the recorded
# retired volumes. A born-kopiur app's claim comes from its own chart.
NEVER_VOLSYNC: set[tuple[str, str]] = {
    # database/falkordb removed with the app 2026-11-01 (owner); kopia snapshots kept under Retain;
    # do not re-add.
    # Unprotected-volume-coverage onboarding, 2026-09-12 (captain decision).
    # Never had VolSync wired onto it - it simply had NO backup engine at all
    # until that change. The three comfyui claims onboarded alongside it were
    # removed with the app on 2026-09-15 (captain decision).
    ("database", "surrealdb"),
    # downloads/cleanuparr, added 2026-10-18. New app; its claim comes from its own
    # HelmRelease and kopiur is its only engine.
    ("downloads", "cleanuparr"),
    # media/wizarr, added 2026-10-18. New app; its claim comes from its own persistence block.
    ("media", "wizarr"),
    # ai/cliproxyapi, added 2026-10-30. New app; its claim comes from its own persistence block.
    ("ai", "cliproxyapi"),
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
SPLIT_BACKUP_APPS = {
    ("database", "pgadmin"),
    ("media", "calibre-web-automated"),
    # surrealdb.yaml's parent Kustomization runs wait: true for its
    # healthCheckExprs, same reason as pgadmin/calibre-web-automated above -
    # added 2026-09-12.
    ("database", "surrealdb"),
}


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

    Prefer `kustomize`, fall back to
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


def volsync_wired() -> list[str]:
    """Every Flux Kustomization that still wires VolSync in, one line each.

    The operator, its CRDs' chart and components/volsync were removed in issue #2202
    PR-6. A Kustomization that lists a volsync component, points its path at one or at
    the old operator app, carries a VOLSYNC_* substitute key, or dependsOn
    system/volsync (which now renders only the bucket) is a half-revert.
    """
    out: list[str] = []
    for d, path in flux_kustomizations():
        spec = d.get("spec") or {}
        name = f"{path.relative_to(APPS_MAIN)}[{(d.get('metadata') or {}).get('name')}]"
        refs = [str(c) for c in (spec.get("components") or [])] + [str(spec.get("path") or "")]
        hits = [r for r in refs if any(v in r for v in VOLSYNC_PATHS)]
        if hits:
            out.append(f"{name}: renders {hits}")
        sub = ((spec.get("postBuild") or {}).get("substitute")) or {}
        keys = sorted(str(k) for k in sub if str(k).startswith("VOLSYNC_"))
        if keys:
            out.append(f"{name}: VOLSYNC_* substitute keys {keys}")
        deps = {(x.get("name"), x.get("namespace")) for x in (spec.get("dependsOn") or [])}
        if ("volsync", "system") in deps:
            out.append(f"{name}: dependsOn system/volsync")
    return out


def test_no_overlay_renders_volsync() -> None:
    """No overlay renders VolSync, and the two single-engine sets cover the fleet exactly.

    Until 2026-11-01 this was `volsync_still_on_every_unretired_claim`: every onboarded
    claim outside RETIRED_CLAIMS and NEVER_VOLSYNC had to keep a VolSync sibling. The
    last claim left VolSync then (issue #2202 PR-4) and PR-6 removed the operator and
    component, so the invariant is now absolute: nothing wires VolSync in, and every
    kopiur claim is in exactly one of RETIRED_CLAIMS (VolSync ran and was removed; a
    restore proof exists) or NEVER_VOLSYNC (born on kopiur). A brand-new app goes in
    NEVER_VOLSYNC, never in RETIRED_CLAIMS. See the comment on NEVER_VOLSYNC.
    """
    wired = volsync_wired()
    require(
        not wired,
        "VolSync is removed (issue #2202 PR-6) but these Flux Kustomizations still wire "
        f"it in: {wired}",
    )
    onboarded_claims = {(ns, claim) for ns, claim, _s, _d, _p in onboarded()}

    stale = sorted(RETIRED_CLAIMS - onboarded_claims)
    require(
        not stale,
        f"RETIRED_CLAIMS names claims that are not kopiur-onboarded at all: {stale}. "
        f"Retiring VolSync from a claim kopiur does not protect leaves it with NO backup.",
    )

    stale_new = sorted(NEVER_VOLSYNC - onboarded_claims)
    require(
        not stale_new,
        f"NEVER_VOLSYNC names claims that are not kopiur-onboarded at all: {stale_new}. "
        f"A claim that never had VolSync and is not onboarded to kopiur has NO backup.",
    )
    overlap = sorted(RETIRED_CLAIMS & NEVER_VOLSYNC)
    require(
        not overlap,
        f"a claim cannot be both retired-from-VolSync and never-VolSync: {overlap}",
    )

    unfiled = sorted(onboarded_claims - (RETIRED_CLAIMS | NEVER_VOLSYNC))
    require(
        not unfiled,
        f"kopiur claims in neither RETIRED_CLAIMS nor NEVER_VOLSYNC: {unfiled}. A brand-new "
        f"app that never had VolSync goes in NEVER_VOLSYNC - NOT in RETIRED_CLAIMS, which "
        f"asserts a retirement and a restore proof "
        f"(.agents/skills/kopiur-backups/references/retirement-and-removal.md).",
    )


def _resources(root: Path) -> list[Path]:
    """Files (and, recursively, directories) a kustomization.yaml in `root` includes."""
    kfile = root / "kustomization.yaml"
    if not kfile.is_file():
        return []
    k = yaml.safe_load(kfile.read_text()) or {}
    out: list[Path] = []
    for entry in (k.get("resources") or []) + (k.get("components") or []):
        if not isinstance(entry, str) or "://" in entry:
            continue
        target = (root / entry).resolve()
        if target.is_dir():
            out.extend(_resources(target))
        elif target.is_file():
            out.append(target)
    return out


def test_every_overlay_file_is_listed() -> None:
    """Every kubernetes/apps/main overlay file is reachable from the root kustomization.

    The namespace Kustomizations run `prune: true`, so dropping an overlay file from its
    kustomization.yaml prunes the app and its claim. The coverage checks above walk the
    directory, not the kustomization, and would never notice.
    """
    listed = {p for p in _resources(APPS_MAIN.resolve()) if p.suffix == ".yaml"}
    unlisted = sorted(
        str(p.relative_to(APPS_MAIN))
        for p in APPS_MAIN.rglob("*.yaml")
        if p.name != "kustomization.yaml" and p.resolve() not in listed
    )
    require(not unlisted, f"overlay files not listed in their kustomization.yaml: {unlisted}")


# Claims created by a chart that is not app-template, so the PVC name cannot be derived
# from the values here. Each still needs persistence enabled on a release of that name.
CHART_OWNED_CLAIMS = {
    # surrealdb chart 0.4.0 names its data PVC after the release.
    ("database", "surrealdb"),
}


def _is_app_template(release: dict[str, Any]) -> bool:
    spec = release.get("spec") or {}
    ref = (spec.get("chartRef") or {}).get("name")
    chart = (((spec.get("chart") or {}).get("spec")) or {}).get("chart")
    return "app-template" in (ref, chart)


def _app_template_claims(release: dict[str, Any]) -> set[str]:
    """PVC names app-template creates: forceRename, else the release name for a single
    claim, else <release>-<identifier> (a second entry renames the first)."""
    name = (release.get("spec") or {}).get("releaseName") or release["metadata"]["name"]
    created = {
        ident: entry
        for ident, entry in (((release.get("spec") or {}).get("values") or {}).get("persistence") or {}).items()
        if isinstance(entry, dict)
        and entry.get("type") == "persistentVolumeClaim"
        and not entry.get("existingClaim")
        and entry.get("enabled", True) is not False
    }
    single = len(created) == 1
    return {
        entry.get("forceRename") or (name if single else f"{name}-{ident}")
        for ident, entry in created.items()
    }


def provided_claims(overlay: Path) -> set[str]:
    """Claim names the Kustomizations in one overlay file create, after substitution."""
    names: set[str] = set()
    for d in load_multi(overlay):
        if d.get("kind") != "Kustomization" or str(d.get("apiVersion", "")).startswith("kustomize.config"):
            continue
        spec = d.get("spec") or {}
        sub = {str(k): str(v) for k, v in (((spec.get("postBuild") or {}).get("substitute")) or {}).items()}
        base = (REPO / str(spec.get("path") or "")).resolve()
        files = _resources(base)
        for comp in spec.get("components") or []:
            target = (base / comp).resolve()
            files.extend(_resources(target) if target.is_dir() else [target])
        ns = (d.get("metadata") or {}).get("namespace") or spec.get("targetNamespace")
        for f in files:
            if f.suffix not in (".yaml", ".yml"):
                continue
            for doc in [x for x in yaml.safe_load_all(_envsubst(f.read_text(), sub)) if isinstance(x, dict)]:
                if doc.get("kind") == "PersistentVolumeClaim":
                    names.add(str((doc.get("metadata") or {}).get("name")))
                elif doc.get("kind") == "HelmRelease":
                    if _is_app_template(doc):
                        names |= _app_template_claims(doc)
                    elif (ns, doc["metadata"]["name"]) in CHART_OWNED_CLAIMS and (
                        ((doc.get("spec") or {}).get("values") or {}).get("persistence") or {}
                    ).get("enabled") is True:
                        names.add(doc["metadata"]["name"])
    return names


def test_every_covered_claim_is_created() -> None:
    """Each kopiur-covered claim is created by its own overlay file: a PVC object (an app
    pvc.yaml or components/kopiur/pvc), or an app-template
    persistence entry. A renamed claim would leave the SnapshotPolicy backing up nothing."""
    by_file: dict[Path, set[str]] = {}
    missing = []
    for ns, claim, _, _, path in onboarded():
        provided = by_file.setdefault(path, provided_claims(path))
        if claim not in provided:
            missing.append(f"{ns}/{claim} ({path.name} creates {sorted(provided)})")
    require(not missing, f"kopiur claims no overlay creates: {missing}")


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
    run("no_overlay_renders_volsync", test_no_overlay_renders_volsync)
    run("every_overlay_file_is_listed", test_every_overlay_file_is_listed)
    run("every_covered_claim_is_created", test_every_covered_claim_is_created)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
