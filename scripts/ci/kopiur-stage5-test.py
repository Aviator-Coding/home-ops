#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 5 (the four-volume retirement pilot).

Stage 5 is the first IRREVERSIBLE step of the VolSync -> kopiur migration: it
removes a volume's second backup engine. On 2026-09-01 four volumes went
through it - `ai/repo-wiki`, `downloads/recyclarr-config`,
`downloads/sabnzbd-config`, `media/seerr` - chosen for regenerable or
reconstructible content and clean restore proofs. The other 26 claims stay
dual-engine pending a separate captain decision.

Authorising evidence: docs/backups/kopiur-restore-proof-2026-09-01.md (all 30
claims restore-proven on both destinations). Record of the pilot itself:
docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md.

The single most dangerous thing about this change is that the volsync Component
was the ONLY manifest emitting each app's PVC, and every app overlay runs
`prune: true`. Dropping the Component without a replacement makes Flux delete
the app's data volume as an ordinary garbage-collect. Most of this file exists
to make that specific mistake fail CI.

This test does not grep source text as its evidence. It:
  1. Parses the four retired Flux overlays into structured objects.
  2. Renders the real kustomize build of components/kopiur/pvc under each
     overlay's OWN substitute map, with a Flux-shaped envsubst, and asserts on
     the resulting PersistentVolumeClaim.
  3. Cross-checks the retired set against RETIRED_CLAIMS in
     kopiur-stage3-test.py, which is the fleet's authoritative record, so the
     two cannot drift apart.
  4. Asserts the pilot document records the results it claims.

Live evidence - snapshots Succeeded on both destinations after the removal,
restores Completed through the populator path, byte-identical trees - is in the
pilot document and was collected before merge. This pins the GitOps contract.
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
KOPIUR_PVC = REPO / "kubernetes" / "components" / "kopiur" / "pvc"
STAGE3_TEST = REPO / "scripts" / "ci" / "kopiur-stage3-test.py"
PILOT_DOC = REPO / "docs" / "backups" / "kopiur-stage5-pilot-retirement-2026-09-01.md"
PROOF_DOC = REPO / "docs" / "backups" / "kopiur-restore-proof-2026-09-01.md"
KOPIUR_README = REPO / "kubernetes" / "components" / "kopiur" / "Readme.md"
VOLSYNC_README = REPO / "kubernetes" / "components" / "volsync" / "Readme.md"

KOPIUR_COMPONENT = "../../../../../components/kopiur"
KOPIUR_PVC_COMPONENT = "../../../../../components/kopiur/pvc"
VOLSYNC_COMPONENT = "../../../../../components/volsync"

# app-overlay path -> (namespace, app, claim, live capacity).
# Capacity is the LIVE claim size, measured 2026-09-01. It matters because the
# retired PVC carries `ssa: IfNotPresent`, so this value is create-time-only and
# stays unexercised until someone rebuilds the claim - which is exactly what
# makes a wrong value dangerous rather than merely untidy.
RETIRED: dict[str, tuple[str, str, str, str]] = {
    "ai/repo-wiki.yaml": ("ai", "repo-wiki", "repo-wiki", "5Gi"),
    "downloads/recyclarr.yaml": ("downloads", "recyclarr", "recyclarr-config", "5Gi"),
    "downloads/sabnzbd.yaml": ("downloads", "sabnzbd", "sabnzbd-config", "5Gi"),
    "media/seerr.yaml": ("media", "seerr", "seerr", "2Gi"),
}

# Restore-proof finding 2: an r2 restore needs materially more kopia cache than
# the same restore from ceph, and a failed Restore is terminal. sabnzbd-config
# is the only pilot volume near the measured danger zone (2.06 GiB of data
# against the 2Gi component default), so it alone is raised.
SABNZBD_CACHE = "10Gi"
COMPONENT_DEFAULT_CACHE = "2Gi"


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


_BUILD: str | None = None


def kustomize_build(path: Path) -> str:
    global _BUILD
    if _BUILD is not None:
        return _BUILD
    exe = shutil.which("kustomize")
    cmd = [exe, "build", str(path)] if exe else None
    if cmd is None:
        kubectl = shutil.which("kubectl")
        require(kubectl, "neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(path)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    require(proc.returncode == 0, f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    _BUILD = proc.stdout
    return _BUILD


def render_pvc(env: dict[str, str]) -> dict[str, Any]:
    rendered = flux_envsubst(kustomize_build(KOPIUR_PVC), env)
    unresolved = sorted(set(re.findall(r"\$\{[A-Za-z_][^}]*\}", rendered)))
    require(not unresolved, f"unresolved substitution tokens: {unresolved}")
    docs = [d for d in yaml.safe_load_all(rendered) if d]
    claims = [d for d in docs if d.get("kind") == "PersistentVolumeClaim"]
    require(
        len(claims) == 1 and len(docs) == 1,
        f"kopiur pvc Component must render exactly one PersistentVolumeClaim, got {[d.get('kind') for d in docs]}",
    )
    return claims[0]


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


def test_sabnzbd_restore_cache_raised() -> None:
    """Restore-proof finding 2, applied to the one pilot volume it reaches."""
    sub = substitute(overlay("downloads/sabnzbd.yaml"))
    require(
        sub.get("KOPIUR_CACHE_CAPACITY") == SABNZBD_CACHE,
        f"sabnzbd KOPIUR_CACHE_CAPACITY must be {SABNZBD_CACHE} - it holds 2.06 GiB against a "
        f"{COMPONENT_DEFAULT_CACHE} default, and an r2 restore needs materially more kopia cache "
        f"than a ceph one (a failed Restore is terminal). Got {sub.get('KOPIUR_CACHE_CAPACITY')!r}",
    )
    # The other three hold 458 KB / 79 MB / 3.4 MB, orders of magnitude under
    # the default, and were re-proven from r2 at it rather than assumed.
    for rel in ("ai/repo-wiki.yaml", "downloads/recyclarr.yaml", "media/seerr.yaml"):
        got = substitute(overlay(rel)).get("KOPIUR_CACHE_CAPACITY")
        require(
            got in (None, COMPONENT_DEFAULT_CACHE),
            f"{rel}: expected the {COMPONENT_DEFAULT_CACHE} default cache, got {got!r}. If this "
            f"volume has grown enough to need more, update the pilot document too.",
        )


def test_retired_set_matches_stage3() -> None:
    """This file and kopiur-stage3-test.py must name the same four claims.

    stage3's RETIRED_CLAIMS is the fleet's authoritative single-engine record -
    it is what enforces that every OTHER claim still has two engines. If the two
    lists drift, one of them is silently wrong about which volumes have a safety
    net, so they are compared rather than maintained in parallel.
    """
    src = STAGE3_TEST.read_text()
    m = re.search(r"RETIRED_CLAIMS: set\[tuple\[str, str\]\] = \{(.*?)\}", src, re.S)
    require(m, "could not find RETIRED_CLAIMS in kopiur-stage3-test.py")
    stage3 = set(re.findall(r'\("([^"]+)",\s*"([^"]+)"\)', m.group(1)))
    here = {(ns, claim) for (ns, _app, claim, _cap) in RETIRED.values()}
    require(
        stage3 == here,
        f"retired set drifted between the two tests: only in stage3 {sorted(stage3 - here)}, "
        f"only here {sorted(here - stage3)}",
    )
    require(len(here) == 4, f"the pilot is four volumes; got {len(here)}")


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


def test_pilot_document_records_the_result() -> None:
    """The document must state what was actually measured, not just that it happened."""
    require(PILOT_DOC.is_file(), f"missing {PILOT_DOC}")
    text = PILOT_DOC.read_text()
    for needle, why in [
        ("kopiur-restore-proof-2026-09-01.md", "must cite the authorising evidence"),
        # The quoted API error is line-wrapped in the document, so match the
        # distinctive fragments rather than the whole sentence.
        ("Forbidden", "must record the measured dataSourceRef API rejection"),
        ("spec is immutable", "must quote the immutability error verbatim"),
        ("IfNotPresent", "must explain the ssa policy that makes the swap possible"),
        ("prune: true", "must state the prune hazard that would delete the claim"),
        ("populator", "must record that the restore proof used the real populator path"),
        ("ownerReference", "must explain why the cache PVCs and Secrets needed no cleanup"),
        ("flux resume ks", "must hand over the suspended Kustomizations"),
        ("RETIRED_CLAIMS", "must point at the machine-readable record"),
    ]:
        require(needle in text, f"pilot document {why} (missing {needle!r})")
    for _rel, (ns, _app, claim, _cap) in RETIRED.items():
        require(f"{ns}/{claim}" in text, f"pilot document must name {ns}/{claim}")
    # The two findings that gate FURTHER retirement must not be quietly dropped.
    require(
        "finding 2" in text and "hermes" in text and "plex" in text,
        "pilot document must record that restore-proof finding 2 still blocks the large claims",
    )
    # And it must be honest about what the pilot does not cover.
    require(
        "26" in text,
        "pilot document must state how many claims remain dual-engine",
    )


def test_surrounding_docs_agree() -> None:
    """The proof doc, both component Readmes and the pilot doc tell one story."""
    proof = PROOF_DOC.read_text()
    require(
        "kopiur-stage5-pilot-retirement-2026-09-01.md" in proof,
        "the restore proof must forward-point at what it authorised, or a reader lands on "
        "'Nothing was retired by this exercise' and believes the fleet is still dual-engine",
    )
    kopiur = KOPIUR_README.read_text()
    require(
        "Retiring a volume" in kopiur,
        "components/kopiur/Readme.md must document the retirement procedure",
    )
    require(
        "pvc/" in kopiur and "KOPIUR_CAPACITY" in kopiur,
        "components/kopiur/Readme.md must document the pvc Component and its variables",
    )
    volsync = VOLSYNC_README.read_text()
    require(
        "78" in volsync and "26" in volsync,
        "components/volsync/Readme.md must record the reduced footprint (26 claims / 78 sources)",
    )
    for name in ("repo-wiki", "recyclarr", "sabnzbd", "seerr"):
        require(
            name in volsync,
            f"components/volsync/Readme.md must name {name} as retired so a reader of the "
            f"schedule table knows why it is absent",
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
    run("sabnzbd_restore_cache_raised", test_sabnzbd_restore_cache_raised)
    run("retired_set_matches_stage3", test_retired_set_matches_stage3)
    run("no_other_overlay_uses_the_pvc_component", test_no_other_overlay_uses_the_pvc_component)
    run("pilot_document_records_the_result", test_pilot_document_records_the_result)
    run("surrounding_docs_agree", test_surrounding_docs_agree)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
