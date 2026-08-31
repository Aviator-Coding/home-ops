#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 3 (fleet-wide parallel run).

Stage 3 onboarded every remaining VolSync-protected claim onto kopiur ALONGSIDE
VolSync, namespace by namespace, one commit each. The safety property of the
whole stage is that BOTH engines stay live on every volume: nothing is retired
here, and no VolSync object is touched. Retirement is Stage 5 and needs a
per-volume restore proof first.

No remaining deferred claims: Stage 4 onboarded both previously deferred
volumes, so kopiur is live on 31 of 31 VolSync-protected claims (alongside
untouched VolSync). This test pins the fleet coverage set and measured mover
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
     an app's second claim, and for an app whose own Kustomization sets
     `wait: true`).
  2. Renders the real kustomize build of components/kopiur/backup and runs a
     Flux-shaped envsubst (including ${VAR:-default} and nested ${A:-${B}})
     under each onboarded claim's own substitute map.
  3. Asserts the Stage 3 contract on those rendered objects.

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
    ("ai", "agentmemory"): ("1000", "1000"),
    ("ai", "hermes"): ("10000", "10000"),
    ("ai", "opencode"): ("1000", "1000"),
    ("ai", "repo-wiki"): ("1000", "1000"),
    ("database", "pgadmin"): ("5050", "5050"),
    ("downloads", "autobrr"): ("2000", "2000"),
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

# Apps whose own Kustomization sets wait: true, so the kopiur half MUST ship as
# a separate wait: false Kustomization - the component's passive Restore is
# Ready=False for the whole parallel run by design, and Flux with wait: true
# assesses every object in the inventory.
WAIT_TRUE_SPLIT = {("database", "pgadmin"), ("media", "calibre-web-automated")}


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
        f"these need a root mover the component cannot express and must stay off kopiur: {sorted(swept)}",
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


def test_wait_true_apps_use_a_split_kustomization() -> None:
    """An app with wait: true must not carry the component inline.

    The component's standing Restore is a passive populator that reports
    Ready=False for the whole parallel run by design, and wait: true makes Flux
    assess every object in the inventory - so inline it would block forever.
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
    # and the two known split apps really are split
    split_names = {
        (d["metadata"].get("namespace"), str((d.get("spec") or {}).get("path") or ""))
        for d, _ in flux_kustomizations()
    }
    for ns, app in WAIT_TRUE_SPLIT:
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
    """Build once and memoise - the path is the same for all 29 claims.

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


def test_volsync_untouched() -> None:
    """Every onboarded claim still has a VolSync sibling; nothing is retired here.

    Covers both onboarding shapes: inline components/volsync, and path-based
    splits (pgadmin, calibre-web-automated, paperless-ngx-media, syncthing-data)
    whose kopiur half is a separate Kustomization. Building the VolSync-covered
    set from every Flux Kustomization means dropping volsync from a parent or
    deleting a second-claim volsync KS fails this pin rather than being skipped.
    """
    covered = volsync_covered()
    for ns, claim, _sub, _d, path in onboarded():
        require(
            (ns, claim) in covered,
            f"{ns}/{claim} ({path.name}): no VolSync sibling covering this claim - both "
            f"engines must run on every volume through the parallel run. Retirement is "
            f"Stage 5.",
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
    run("wait_true_apps_use_a_split_kustomization", test_wait_true_apps_use_a_split_kustomization)
    run("depends_on_repository", test_every_onboarded_kustomization_depends_on_repository)
    run("rendered_objects_hold_the_contract", test_rendered_objects_hold_the_contract)
    run("volsync_untouched", test_volsync_untouched)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
