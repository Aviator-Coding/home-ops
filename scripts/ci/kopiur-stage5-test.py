#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 5 (the VolSync retirements).

Stage 5 is the IRREVERSIBLE step of the VolSync -> kopiur migration: it removes
a volume's second backup engine. Eight of the fleet's 30 claims went through it,
in two waves, and 22 remain dual-engine.

One of those eight, `downloads/autobrr`, is no longer in the fleet at all: the
APP was removed on 2026-09-02 (captain decision - unused), so its overlay, its
claim and its kopiur onboarding all went with it and there is nothing left for
this file to assert. Its kopia snapshots were deliberately KEPT. That leaves
seven retired-and-still-present volumes below against a 29-claim fleet; the
wave-two record still documents autobrr's retirement, because it happened.

  wave one, 2026-09-01 - the pilot: `ai/repo-wiki`,
  `downloads/recyclarr-config`, `downloads/sabnzbd-config`, `media/seerr`,
  chosen for regenerable or reconstructible content and clean restore proofs.
  Authorising evidence: docs/backups/kopiur-restore-proof-2026-09-01.md (all 30
  claims restore-proven on both destinations). Record:
  docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md.

  wave two, 2026-09-02: `downloads/prowlarr-config`, `selfhosted/ntfy`,
  `downloads/autobrr` (app since removed, see above),
  `selfhosted/obsidian-livesync`. These are NOT all
  regenerable - `ntfy` holds real auth state and `obsidian-livesync` is a
  genuine Obsidian vault retired on an explicit captain decision after an
  objection. What authorises them is completeness of proof (100% of claim
  content, destination-identical in content AND metadata) plus, for the two
  2Gi `selfhosted` claims, a PVC that cannot outgrow its restore cache.
  Authorising evidence: docs/backups/kopiur-wave-two-reproof-2026-09-02.md
  part 4. Record: docs/backups/kopiur-wave-two-retirement-2026-09-02.md.

`selfhosted/paperless-ngx` stays dual-engine permanently by captain carve-out,
and `selfhosted/syncthing-data` / `selfhosted/paperless-ngx-media` were
assessed and left dual-engine; any further retirement needs its own decision.

The single most dangerous thing about this change is that the volsync Component
was the ONLY manifest emitting each app's PVC, and every app overlay runs
`prune: true`. Dropping the Component without a replacement makes Flux delete
the app's data volume as an ordinary garbage-collect. Most of this file exists
to make that specific mistake fail CI.

This test does not grep source text as its evidence. It:
  1. Parses every retired Flux overlay into structured objects.
  2. Renders the real kustomize build of components/kopiur/pvc under each
     overlay's OWN substitute map, with a Flux-shaped envsubst, and asserts on
     the resulting PersistentVolumeClaim.
  3. Renders components/kopiur/backup the same way and asserts the volume still
     has a complete single-engine backup after losing VolSync (both destinations'
     SnapshotPolicy + SnapshotSchedule, the standing Restore, credentialProjection
     on each, and matching mover identities so the populator can read back what
     was written).
  4. Cross-checks the retired set against RETIRED_CLAIMS loaded via importlib
     from kopiur-stage3-test.py, the fleet's authoritative record, so the two
     cannot drift apart.

Live evidence - snapshots Succeeded on both destinations after the removal,
restores Completed through the populator path, byte-identical trees - is in the
pilot document and was collected before merge. This pins the GitOps contract.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APPS_MAIN = REPO / "kubernetes" / "apps" / "main"
KOPIUR_PVC = REPO / "kubernetes" / "components" / "kopiur" / "pvc"
KOPIUR_BACKUP = REPO / "kubernetes" / "components" / "kopiur" / "backup"
STAGE3_TEST = REPO / "scripts" / "ci" / "kopiur-stage3-test.py"

KOPIUR_COMPONENT = "../../../../../components/kopiur"
KOPIUR_PVC_COMPONENT = "../../../../../components/kopiur/pvc"
VOLSYNC_COMPONENT = "../../../../../components/volsync"

# app-overlay path -> (namespace, app, claim, live capacity).
# Capacity is the LIVE claim size, measured 2026-09-01 (wave one) and
# 2026-09-02 (wave two). It matters because the retired PVC carries
# `ssa: IfNotPresent`, so this value is create-time-only and stays unexercised
# until someone rebuilds the claim - which is exactly what makes a wrong value
# dangerous rather than merely untidy.
RETIRED: dict[str, tuple[str, str, str, str]] = {
    # wave one - the pilot, 2026-09-01
    "ai/repo-wiki.yaml": ("ai", "repo-wiki", "repo-wiki", "5Gi"),
    "downloads/recyclarr.yaml": ("downloads", "recyclarr", "recyclarr-config", "5Gi"),
    "downloads/sabnzbd.yaml": ("downloads", "sabnzbd", "sabnzbd-config", "5Gi"),
    "media/seerr.yaml": ("media", "seerr", "seerr", "2Gi"),
    # wave two, 2026-09-02
    # "downloads/autobrr.yaml" was here. The app was REMOVED on 2026-09-02, so
    # the overlay this row reads no longer exists and every assertion below
    # would fail on a missing file - the retired-app/CI-gate trap this repo
    # documents in AGENTS.md. Removed rather than retained because this map is
    # keyed by live overlay path, not by history; the retirement itself stays
    # recorded in docs/backups/kopiur-wave-two-retirement-2026-09-02.md.
    "downloads/prowlarr.yaml": ("downloads", "prowlarr", "prowlarr-config", "5Gi"),
    "selfhosted/ntfy.yaml": ("selfhosted", "ntfy", "ntfy", "2Gi"),
    "selfhosted/obsidian-livesync.yaml": (
        "selfhosted",
        "obsidian-livesync",
        "obsidian-livesync",
        "2Gi",
    ),
}

# Restore-proof finding 2 / the 2026-09-02 cache gate: an r2 restore needs
# materially more kopia cache than the same restore from ceph, required cache is
# `min(snapshot sizeBytes, ~6.2 GiB)`, it is a CLIFF rather than a slope, and a
# failed Restore is terminal and never retries.
#
# Overlay path -> the raised value, for every retired volume that needs one.
# Everything else must sit at the component default, and that is asserted rather
# than assumed: a volume quietly acquiring a raised cache means someone found a
# risk that belongs in the retirement record too.
COMPONENT_DEFAULT_CACHE = "2Gi"
RAISED_CACHE: dict[str, str] = {
    # 2.06 GiB of data against the 2Gi default - the only volume in either wave
    # inside the measured danger zone.
    "downloads/sabnzbd.yaml": "10Gi",
}

# Stated separately from len(RETIRED) on purpose: this is the number a human
# decided, so a row appearing or vanishing from RETIRED has to be a deliberate
# edit here too rather than something the test silently accommodates.
#
# Was 8 (4 pilot + 4 wave two). Now 7: `downloads/autobrr` was retired in wave
# two and then the APP ITSELF was removed on 2026-09-02, so there is no overlay
# left to hold a retirement contract against. Eight volumes were retired; seven
# are still in the fleet, and this counts the latter.
EXPECTED_RETIRED_COUNT = 7


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_multi(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def overlay(rel: str) -> dict[str, Any]:
    docs = [d for d in load_multi(APPS_MAIN / rel) if d.get("kind") == "Kustomization"]
    require(len(docs) == 1, f"expected exactly 1 Flux Kustomization in {rel}, got {len(docs)}")
    return docs[0]


def substitute(d: dict[str, Any]) -> dict[str, str]:
    sub = ((d.get("spec") or {}).get("postBuild") or {}).get("substitute") or {}
    return {str(k): str(v) for k, v in sub.items()}


def flux_envsubst(text: str, env: dict[str, str]) -> str:
    """Flux-shaped ${VAR} / ${VAR:-default} substitution, including nesting."""

    def expand(s: str) -> str:
        out: list[str] = []
        i, n = 0, len(s)
        while i < n:
            if s[i] != "$" or i + 1 >= n or s[i + 1] != "{":
                out.append(s[i])
                i += 1
                continue
            depth, j = 1, i + 2
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
            key, default = (body.split(":-", 1) + [None])[:2] if ":-" in body else (body, None)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key or ""):
                out.append(s[i:j])
                i = j
                continue
            val = env.get(key) or None
            if val is not None:
                out.append(val)
            elif default is not None:
                out.append(expand(default))
            else:
                out.append(s[i:j])
            i = j
        return "".join(out)

    return expand(text)


_BUILD_CACHE: dict[Path, str] = {}


def kustomize_build(path: Path) -> str:
    cached = _BUILD_CACHE.get(path)
    if cached is not None:
        return cached
    exe = shutil.which("kustomize")
    cmd = [exe, "build", str(path)] if exe else None
    if cmd is None:
        kubectl = shutil.which("kubectl")
        require(kubectl, "neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(path)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    require(proc.returncode == 0, f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    _BUILD_CACHE[path] = proc.stdout
    return proc.stdout


def render_docs(path: Path, env: dict[str, str]) -> list[dict[str, Any]]:
    rendered = flux_envsubst(kustomize_build(path), env)
    unresolved = sorted(set(re.findall(r"\$\{[A-Za-z_][^}]*\}", rendered)))
    require(not unresolved, f"unresolved substitution tokens after render of {path}: {unresolved}")
    return [d for d in yaml.safe_load_all(rendered) if d]


def render_pvc(env: dict[str, str]) -> dict[str, Any]:
    docs = render_docs(KOPIUR_PVC, env)
    claims = [d for d in docs if d.get("kind") == "PersistentVolumeClaim"]
    require(
        len(claims) == 1 and len(docs) == 1,
        f"kopiur pvc Component must render exactly one PersistentVolumeClaim, got {[d.get('kind') for d in docs]}",
    )
    return claims[0]


def render_backup(env: dict[str, str]) -> list[dict[str, Any]]:
    return render_docs(KOPIUR_BACKUP, env)


def load_stage3_retired_claims() -> set[tuple[str, str]]:
    """Import RETIRED_CLAIMS as a real Python object from the stage3 module."""
    require(STAGE3_TEST.is_file(), f"missing {STAGE3_TEST}")
    name = "kopiur_stage3_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, STAGE3_TEST)
    require(spec is not None and spec.loader is not None, f"cannot load {STAGE3_TEST}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    claims = getattr(mod, "RETIRED_CLAIMS", None)
    require(
        isinstance(claims, set),
        f"kopiur-stage3-test.py RETIRED_CLAIMS must be a set, got {type(claims).__name__}",
    )
    return {(str(ns), str(claim)) for ns, claim in claims}


def mover_identity(doc: dict[str, Any]) -> tuple[Any, Any, Any]:
    psc = ((doc.get("spec") or {}).get("mover") or {}).get("podSecurityContext") or {}
    return (psc.get("runAsUser"), psc.get("runAsGroup"), psc.get("fsGroup"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_retired_overlays_dropped_volsync() -> None:
    """No volsync Component, no volsync dependency, no VOLSYNC_* key survives."""
    for rel, (ns, app, _claim, _cap) in RETIRED.items():
        d = overlay(rel)
        spec = d["spec"]
        require(
            d["metadata"]["namespace"] == ns and d["metadata"]["name"] == app,
            f"{rel}: expected {ns}/{app}, got "
            f"{d['metadata'].get('namespace')}/{d['metadata'].get('name')}",
        )
        comps = spec.get("components") or []
        require(
            VOLSYNC_COMPONENT not in comps,
            f"{rel}: volsync Component is back - this volume is recorded as retired",
        )
        deps = {(x.get("name"), x.get("namespace")) for x in (spec.get("dependsOn") or [])}
        require(
            ("volsync", "system") not in deps,
            f"{rel}: still dependsOn volsync/system, but renders no VolSync object - "
            f"that is a wait on an unrelated app, and the signature of a half-revert",
        )
        leftover = sorted(k for k in substitute(d) if k.startswith("VOLSYNC_"))
        require(
            not leftover,
            f"{rel}: VOLSYNC_* substitute keys survived retirement: {leftover}. Nothing reads "
            f"them, and on the claim variables they misdescribe what a rebuild would provision.",
        )


def test_retired_overlays_keep_the_claim() -> None:
    """The kopiur pvc Component is present - without it, prune deletes the volume."""
    for rel, (_ns, _app, _claim, _cap) in RETIRED.items():
        comps = overlay(rel)["spec"].get("components") or []
        require(
            comps == [KOPIUR_COMPONENT, KOPIUR_PVC_COMPONENT],
            f"{rel}: components must be exactly [kopiur, kopiur/pvc], got {comps}. The pvc "
            f"Component is NOT optional: volsync's pvc.yaml was the only manifest emitting this "
            f"claim and the overlay runs prune: true, so dropping both deletes the data volume.",
        )
        require(
            overlay(rel)["spec"].get("prune") is True,
            f"{rel}: prune must stay true (this test's premise, and the fleet convention)",
        )


def test_rendered_claim_is_correct_per_app() -> None:
    """Render components/kopiur/pvc under each retired overlay's own substitute map."""
    for rel, (_ns, app, claim, cap) in RETIRED.items():
        env = {**substitute(overlay(rel)), "SECRET_DOMAIN": "example.test"}
        pvc = render_pvc(env)

        require(
            pvc["metadata"]["name"] == claim,
            f"{rel}: rendered claim must be {claim!r}, got {pvc['metadata']['name']!r}",
        )
        labels = pvc["metadata"].get("labels") or {}

        # dataSourceRef is immutable on a bound PVC (measured 2026-09-01: a
        # server-side dry-run of the swap returns `spec: Forbidden: spec is
        # immutable after creation except resources.requests and
        # volumeAttributesClassName for bound claims`). Without IfNotPresent,
        # Flux retries that forbidden change forever and the Kustomization
        # never goes Ready again.
        require(
            labels.get("kustomize.toolkit.fluxcd.io/ssa") == "IfNotPresent",
            f"{rel}: retired claim must carry ssa: IfNotPresent, got {labels}",
        )
        # `force: enabled` resolves an immutable-field conflict by DELETING and
        # recreating the object. Here that object is the app's data volume.
        require(
            "kustomize.toolkit.fluxcd.io/force" not in labels,
            f"{rel}: the force label would make Flux delete the data volume to resolve the "
            f"immutable dataSourceRef; got {labels}",
        )

        dsr = pvc["spec"].get("dataSourceRef") or {}
        require(
            dsr.get("kind") == "Restore"
            and dsr.get("apiGroup") == "kopiur.home-operations.com"
            and dsr.get("name") == f"{app}-kopiur-dst",
            f"{rel}: a rebuilt claim must be populated from Restore/{app}-kopiur-dst, got {dsr}",
        )
        require(
            pvc["spec"]["resources"]["requests"]["storage"] == cap,
            f"{rel}: rendered capacity must match the live claim ({cap}), got "
            f"{pvc['spec']['resources']['requests']['storage']!r}",
        )
        require(
            pvc["spec"].get("storageClassName") == "ceph-block",
            f"{rel}: storageClassName must be ceph-block, got {pvc['spec'].get('storageClassName')!r}",
        )


def test_capacity_is_stated_not_defaulted() -> None:
    """KOPIUR_CAPACITY must be explicit on every retired overlay.

    seerr is the reason this is a hard requirement rather than a style note: its
    live claim is 2Gi while the component default is 5Gi, so an overlay that
    leaves it out would silently provision a rebuilt claim at 2.5x the size of
    the one it replaced. Requiring it everywhere means nobody has to remember
    which apps are the exceptions.
    """
    for rel, (_ns, _app, _claim, cap) in RETIRED.items():
        sub = substitute(overlay(rel))
        require(
            sub.get("KOPIUR_CAPACITY") == cap,
            f"{rel}: KOPIUR_CAPACITY must be stated as {cap!r} (the live claim), got "
            f"{sub.get('KOPIUR_CAPACITY')!r}",
        )


def test_restore_cache_matches_the_measured_gate() -> None:
    """Every retired volume's restore cache is the one its own measurement asked for.

    Asserted in both directions. A volume in RAISED_CACHE must carry exactly that
    value - retirement removes the second engine, so an under-sized cache turns a
    terminal restore failure into something only discoverable during a real
    disaster. A volume NOT in RAISED_CACHE must sit at the component default,
    because a cache that quietly grew means someone found an exposure that
    belongs in the retirement record rather than only in a substitute map.

    The wave-two claims are all far under the default: 54.1 MiB (prowlarr-config),
    184 KiB (ntfy) and 561 KiB (obsidian-livesync) against ~1.95 GiB of usable
    cache - as was autobrr's 2,179 B before that app was removed. The two `selfhosted` ones are additionally
    structural: a 2Gi PVC cannot hold more than its 2Gi cache covers.
    """
    for rel in RETIRED:
        got = substitute(overlay(rel)).get("KOPIUR_CACHE_CAPACITY")
        if rel in RAISED_CACHE:
            want = RAISED_CACHE[rel]
            require(
                got == want,
                f"{rel}: KOPIUR_CACHE_CAPACITY must be {want} - measured to need more than the "
                f"{COMPONENT_DEFAULT_CACHE} default, and an r2 restore needs materially more "
                f"kopia cache than a ceph one (a failed Restore is terminal). Got {got!r}",
            )
        else:
            require(
                got in (None, COMPONENT_DEFAULT_CACHE),
                f"{rel}: expected the {COMPONENT_DEFAULT_CACHE} default cache, got {got!r}. If "
                f"this volume has grown enough to need more, add it to RAISED_CACHE and update "
                f"the retirement document too.",
            )


def test_retired_set_matches_stage3() -> None:
    """This file and kopiur-stage3-test.py must name the same retired claims.

    stage3's RETIRED_CLAIMS is the fleet's authoritative single-engine record -
    it is what enforces that every OTHER claim still has two engines. If the two
    lists drift, one of them is silently wrong about which volumes have a safety
    net, so they are compared rather than maintained in parallel. Loaded via
    importlib so a renamed, moved, or non-set value fails instead of matching
    source text.
    """
    stage3 = load_stage3_retired_claims()
    here = {(ns, claim) for (ns, _app, claim, _cap) in RETIRED.values()}
    require(
        stage3 == here,
        f"retired set drifted between the two tests: only in stage3 {sorted(stage3 - here)}, "
        f"only here {sorted(here - stage3)}",
    )
    require(
        len(here) == EXPECTED_RETIRED_COUNT,
        f"expected {EXPECTED_RETIRED_COUNT} retired volumes still in the fleet "
        f"(4 pilot + 4 wave two, less autobrr whose app was removed); "
        f"got {len(here)}",
    )


def test_no_other_overlay_uses_the_pvc_component() -> None:
    """A dual-engine app must NOT add components/kopiur/pvc.

    It would collide with volsync's pvc.yaml on the same PVC name - the exact
    kustomize resource collision that is the reason this is a separate
    Component in the first place.
    """
    offenders: list[str] = []
    for f in sorted(APPS_MAIN.rglob("*.yaml")):
        rel = f"{f.parent.name}/{f.name}"
        for d in load_multi(f):
            if d.get("kind") != "Kustomization":
                continue
            comps = (d.get("spec") or {}).get("components") or []
            has_pvc = KOPIUR_PVC_COMPONENT in comps
            has_volsync = VOLSYNC_COMPONENT in comps
            if has_pvc and has_volsync:
                offenders.append(f"{rel}: both volsync and kopiur/pvc (PVC name collision)")
            if has_pvc and rel not in RETIRED:
                offenders.append(f"{rel}: uses kopiur/pvc but is not a recorded retired volume")
    require(not offenders, "kopiur/pvc Component misuse: " + "; ".join(offenders))


def test_retired_backup_shape_is_complete() -> None:
    """After losing VolSync, every retired claim must still render a full kopiur backup.

    Retirement must never leave a claim with zero engines or half a configuration.
    Render components/kopiur/backup under each overlay's own substitute map and
    require both destinations' SnapshotPolicy + SnapshotSchedule, the standing
    Restore, credentialProjection on each of those, and a Restore mover identity
    equal to the SnapshotPolicy's so the populator can read back what was written.
    """
    for rel, (_ns, app, claim, _cap) in RETIRED.items():
        env = {**substitute(overlay(rel)), "SECRET_DOMAIN": "example.test"}
        docs = render_backup(env)
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for d in docs:
            by_kind.setdefault(d.get("kind") or "", []).append(d)

        policies = {d["metadata"]["name"]: d for d in by_kind.get("SnapshotPolicy", [])}
        schedules = {d["metadata"]["name"]: d for d in by_kind.get("SnapshotSchedule", [])}
        restores = {d["metadata"]["name"]: d for d in by_kind.get("Restore", [])}

        ceph_policy_name = f"{app}-ceph"
        r2_policy_name = f"{app}-r2"
        restore_name = f"{app}-kopiur-dst"

        require(
            set(policies) == {ceph_policy_name, r2_policy_name},
            f"{rel}: expected SnapshotPolicy names {{{ceph_policy_name}, {r2_policy_name}}}, "
            f"got {sorted(policies)}",
        )
        require(
            set(schedules) == {ceph_policy_name, r2_policy_name},
            f"{rel}: expected SnapshotSchedule names {{{ceph_policy_name}, {r2_policy_name}}}, "
            f"got {sorted(schedules)}",
        )
        require(
            set(restores) == {restore_name},
            f"{rel}: expected standing Restore {restore_name!r}, got {sorted(restores)}",
        )

        for name, policy in policies.items():
            sources = (policy.get("spec") or {}).get("sources") or []
            pvc_names = [
                ((s.get("pvc") or {}).get("name"))
                for s in sources
                if isinstance(s, dict)
            ]
            require(
                claim in pvc_names,
                f"{rel}: SnapshotPolicy/{name} must source claim {claim!r}, got {pvc_names}",
            )
            proj = (policy.get("spec") or {}).get("credentialProjection") or {}
            require(
                proj.get("enabled") is True,
                f"{rel}: SnapshotPolicy/{name} must enable credentialProjection, got {proj}",
            )

        for name, schedule in schedules.items():
            pref = ((schedule.get("spec") or {}).get("policyRef") or {}).get("name")
            require(
                pref == name,
                f"{rel}: SnapshotSchedule/{name} must policyRef {name!r}, got {pref!r}",
            )

        restore = restores[restore_name]
        rproj = (restore.get("spec") or {}).get("credentialProjection") or {}
        require(
            rproj.get("enabled") is True,
            f"{rel}: Restore/{restore_name} must enable credentialProjection, got {rproj}",
        )
        from_policy = (((restore.get("spec") or {}).get("source") or {}).get("fromPolicy") or {}).get(
            "name"
        )
        require(
            from_policy == ceph_policy_name,
            f"{rel}: Restore/{restore_name} must fromPolicy {ceph_policy_name!r}, got {from_policy!r}",
        )
        target = (restore.get("spec") or {}).get("target") or {}
        require(
            "populator" in target,
            f"{rel}: Restore/{restore_name} must be a standing populator, got target={target}",
        )

        ceph_id = mover_identity(policies[ceph_policy_name])
        r2_id = mover_identity(policies[r2_policy_name])
        restore_id = mover_identity(restore)
        require(
            ceph_id == r2_id == restore_id,
            f"{rel}: mover identity must match across ceph policy / r2 policy / restore so the "
            f"populator can read back what was written; got ceph={ceph_id} r2={r2_id} "
            f"restore={restore_id}",
        )
        require(
            all(v is not None for v in ceph_id),
            f"{rel}: mover identity must be fully set, got {ceph_id}",
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

    run("retired_overlays_dropped_volsync", test_retired_overlays_dropped_volsync)
    run("retired_overlays_keep_the_claim", test_retired_overlays_keep_the_claim)
    run("rendered_claim_is_correct_per_app", test_rendered_claim_is_correct_per_app)
    run("capacity_is_stated_not_defaulted", test_capacity_is_stated_not_defaulted)
    run("restore_cache_matches_the_measured_gate", test_restore_cache_matches_the_measured_gate)
    run("retired_set_matches_stage3", test_retired_set_matches_stage3)
    run("no_other_overlay_uses_the_pvc_component", test_no_other_overlay_uses_the_pvc_component)
    run("retired_backup_shape_is_complete", test_retired_backup_shape_is_complete)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
