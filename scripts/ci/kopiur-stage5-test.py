#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 5 (the VolSync retirements).

Stage 5 is the IRREVERSIBLE step of the VolSync -> kopiur migration: it removes
a volume's second backup engine. 27 of the fleet's 30 claims have now gone
through it, in three waves, and 3 remain dual-engine - which is the whole of
what VolSync still protects.

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

  wave three, 2026-09-04: the remaining 19 eligible claims, on the same fleet
  restore proof (every one of them a destination-identical PASS row) plus a
  re-measured restore-cache audit. Shipped as three risk-tiered commits so a
  revert stays surgical - tier A `database/pgadmin`, `downloads/{bazarr,lidarr,
  radarr,readarr,sonarr}`, `home-automation/{esphome,matter-server,
  zigbee2mqtt}`, `media/tdarr`, `selfhosted/changedetection`; tier B
  `ai/{hermes,opencode}`, `media/{plex,calibre-web-automated}`; tier C
  `home-automation/home-assistant`, `selfhosted/{n8n,linkwarden,syncthing}`.
  Record: docs/backups/kopiur-wave-three-retirement-2026-09-04.md.

`selfhosted/paperless-ngx` stays dual-engine permanently by captain carve-out,
and `selfhosted/syncthing-data` / `selfhosted/paperless-ngx-media` were
assessed and left dual-engine; any further retirement needs its own decision.
Those three are now the entire dual-engine fleet.

TWO OVERLAY SHAPES. Most retired volumes put both `components/kopiur` and
`components/kopiur/pvc` on the app's own Flux Kustomization. Two cannot:
`database/pgadmin` and `media/calibre-web-automated` set `wait: true`, which
makes Flux assess every object in the inventory including the kopiur component's
standing `Restore` - permanently `Ready=False` by design. Those two keep the
kopiur BACKUP half in its own `wait: false` Kustomization and move only the
CLAIM onto the app's, so their `components:` list is `[kopiur/pvc]` alone. The
table below records that split explicitly rather than special-casing it in the
assertions.

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
from typing import Any, NamedTuple

import yaml

REPO = Path(__file__).resolve().parents[2]
APPS_MAIN = REPO / "kubernetes" / "apps" / "main"
KOPIUR_PVC = REPO / "kubernetes" / "components" / "kopiur" / "pvc"
KOPIUR_BACKUP = REPO / "kubernetes" / "components" / "kopiur" / "backup"
STAGE3_TEST = REPO / "scripts" / "ci" / "kopiur-stage3-test.py"

KOPIUR_COMPONENT = "../../../../../components/kopiur"
KOPIUR_PVC_COMPONENT = "../../../../../components/kopiur/pvc"
VOLSYNC_COMPONENT = "../../../../../components/volsync"

# The retired volumes, keyed "<namespace>/<app>".
#
#   rel       overlay file, relative to kubernetes/apps/main
#   ks        the Flux Kustomization in that file that OWNS THE CLAIM
#   claim     the PersistentVolumeClaim name
#   capacity  the LIVE claim size, measured 2026-09-01 (wave one), 2026-09-02
#             (wave two) and 2026-09-04 (wave three). It matters because the
#             retired PVC carries `ssa: IfNotPresent`, so this value is
#             create-time-only and stays unexercised until someone rebuilds the
#             claim - which is exactly what makes a wrong value dangerous
#             rather than merely untidy.
#   backup_ks the Flux Kustomization that owns the kopiur BACKUP objects.
#             None means "the same one" - the fleet's normal shape. A name
#             means the `wait: true` split described in the module docstring,
#             and the assertions below read the two halves from their own
#             Kustomizations rather than assuming one substitute map.
class Retired(NamedTuple):
    rel: str
    ks: str
    claim: str
    capacity: str
    backup_ks: str | None = None


RETIRED: dict[str, Retired] = {
    # wave one - the pilot, 2026-09-01
    "ai/repo-wiki": Retired("ai/repo-wiki.yaml", "repo-wiki", "repo-wiki", "5Gi"),
    "downloads/recyclarr": Retired("downloads/recyclarr.yaml", "recyclarr", "recyclarr-config", "5Gi"),
    "downloads/sabnzbd": Retired("downloads/sabnzbd.yaml", "sabnzbd", "sabnzbd-config", "5Gi"),
    "media/seerr": Retired("media/seerr.yaml", "seerr", "seerr", "2Gi"),
    # wave two, 2026-09-02
    # "downloads/autobrr" was here. The app was REMOVED on 2026-09-02, so the
    # overlay this row read no longer exists and every assertion below would
    # fail on a missing file - the retired-app/CI-gate trap this repo documents
    # in AGENTS.md. Removed rather than retained because this map is keyed by
    # live overlay, not by history; the retirement itself stays recorded in
    # docs/backups/kopiur-wave-two-retirement-2026-09-02.md.
    "downloads/prowlarr": Retired("downloads/prowlarr.yaml", "prowlarr", "prowlarr-config", "5Gi"),
    "selfhosted/ntfy": Retired("selfhosted/ntfy.yaml", "ntfy", "ntfy", "2Gi"),
    "selfhosted/obsidian-livesync": Retired(
        "selfhosted/obsidian-livesync.yaml", "obsidian-livesync", "obsidian-livesync", "2Gi"
    ),
    # wave three, 2026-09-04 - tier A, ordinary config volumes (11)
    #
    # pgadmin is the split shape: its claim rides the `pgadmin` Kustomization
    # inside database/cloudnative-pg.yaml (`wait: true`), its kopiur backup
    # objects ride `pgadmin-kopiur` in the same file.
    "database/pgadmin": Retired(
        "database/cloudnative-pg.yaml", "pgadmin", "pgadmin", "2Gi", "pgadmin-kopiur"
    ),
    "downloads/bazarr": Retired("downloads/bazarr.yaml", "bazarr", "bazarr-config", "5Gi"),
    "downloads/lidarr": Retired("downloads/lidarr.yaml", "lidarr", "lidarr-config", "8Gi"),
    "downloads/radarr": Retired("downloads/radarr.yaml", "radarr", "radarr-config", "5Gi"),
    "downloads/readarr": Retired("downloads/readarr.yaml", "readarr", "readarr-config", "5Gi"),
    "downloads/sonarr": Retired("downloads/sonarr.yaml", "sonarr", "sonarr-config", "5Gi"),
    "home-automation/esphome": Retired(
        "home-automation/esphome.yaml", "esphome", "esphome-config", "5Gi"
    ),
    "home-automation/matter-server": Retired(
        "home-automation/matter-server.yaml", "matter-server", "matter-server", "1Gi"
    ),
    "home-automation/zigbee2mqtt": Retired(
        "home-automation/zigbee2mqtt.yaml", "zigbee2mqtt", "zigbee2mqtt-data", "5Gi"
    ),
    "media/tdarr": Retired("media/tdarr.yaml", "tdarr", "tdarr-config", "5Gi"),
    "selfhosted/changedetection": Retired(
        "selfhosted/changedetection.yaml", "changedetection", "changedetection-config", "1Gi"
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
    # 2.06 GiB of data against the 2Gi default - the only volume in the first
    # two waves inside the measured danger zone.
    "downloads/sabnzbd": "10Gi",
    # Raised 2Gi -> 10Gi on 2026-09-02, before this retirement, by the fleet
    # audit in docs/backups/kopiur-populator-drift-2026-09-02.md: tdarr sat at
    # 87% of usable cache and radarr at 70%, and the requirement does not rise
    # smoothly past the cache - it jumps to the ~6.2 GiB plateau. 10Gi clears
    # the plateau outright, so neither can reach the cliff again. tdarr is
    # additionally r2-PROVEN at exactly this value.
    "media/tdarr": "10Gi",
    "downloads/radarr": "10Gi",
}

# Stated separately from len(RETIRED) on purpose: this is the number a human
# decided, so a row appearing or vanishing from RETIRED has to be a deliberate
# edit here too rather than something the test silently accommodates.
#
# Was 8 (4 pilot + 4 wave two), then 7 once `downloads/autobrr` was retired in
# wave two and the APP ITSELF removed on 2026-09-02, leaving no overlay to hold
# a retirement contract against. Wave three adds 11 in tier A.
EXPECTED_RETIRED_COUNT = 18


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_multi(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def kustomizations(rel: str) -> dict[str, dict[str, Any]]:
    """Every Flux Kustomization in an overlay file, keyed by metadata.name."""
    docs = [d for d in load_multi(APPS_MAIN / rel) if d.get("kind") == "Kustomization"]
    require(docs, f"no Flux Kustomization in {rel}")
    by_name = {d["metadata"]["name"]: d for d in docs}
    require(
        len(by_name) == len(docs),
        f"duplicate Kustomization names in {rel}: {[d['metadata']['name'] for d in docs]}",
    )
    return by_name


def overlay(rel: str, name: str) -> dict[str, Any]:
    """The one Flux Kustomization named `name` in overlay file `rel`.

    Selected by name rather than by "the only document in the file" because two
    retired volumes live in multi-Kustomization overlays (the `wait: true`
    split), and a third - `selfhosted/syncthing` - shares its file with the
    separate, deliberately NOT-retired `syncthing-data` claim.
    """
    by_name = kustomizations(rel)
    require(name in by_name, f"{rel}: no Flux Kustomization named {name!r} (have {sorted(by_name)})")
    return by_name[name]


def claim_ks(v: Retired) -> dict[str, Any]:
    """The Kustomization that owns the PVC."""
    return overlay(v.rel, v.ks)


def backup_ks(v: Retired) -> dict[str, Any]:
    """The Kustomization that owns the kopiur SnapshotPolicy/Schedule/Restore."""
    return overlay(v.rel, v.backup_ks or v.ks)


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
    for key, v in RETIRED.items():
        ns, app = key.split("/", 1)
        # Both halves are checked, because a half-revert that put volsync back
        # on only the backup Kustomization would otherwise pass.
        for role, d in (("claim", claim_ks(v)), ("backup", backup_ks(v))):
            spec = d["spec"]
            require(
                d["metadata"]["namespace"] == ns,
                f"{v.rel}[{d['metadata']['name']}]: expected namespace {ns}, got "
                f"{d['metadata'].get('namespace')}",
            )
            comps = spec.get("components") or []
            require(
                VOLSYNC_COMPONENT not in comps,
                f"{v.rel}[{d['metadata']['name']}] ({role}): volsync Component is back - "
                f"{key} is recorded as retired",
            )
            require(
                not str(spec.get("path") or "").rstrip("/").endswith("components/volsync/backup"),
                f"{v.rel}[{d['metadata']['name']}] ({role}): path points at the volsync backup "
                f"bundle, but {key} is recorded as retired",
            )
            deps = {(x.get("name"), x.get("namespace")) for x in (spec.get("dependsOn") or [])}
            require(
                ("volsync", "system") not in deps,
                f"{v.rel}[{d['metadata']['name']}] ({role}): still dependsOn volsync/system, but "
                f"renders no VolSync object - that is a wait on an unrelated app, and the "
                f"signature of a half-revert",
            )
            leftover = sorted(k for k in substitute(d) if k.startswith("VOLSYNC_"))
            require(
                not leftover,
                f"{v.rel}[{d['metadata']['name']}] ({role}): VOLSYNC_* substitute keys survived "
                f"retirement: {leftover}. Nothing reads them, and on the claim variables they "
                f"misdescribe what a rebuild would provision.",
            )
        require(
            app == v.ks,
            f"{key}: table says the claim Kustomization is {v.ks!r}; the key says {app!r}",
        )


def test_retired_overlays_keep_the_claim() -> None:
    """The kopiur pvc Component is present - without it, prune deletes the volume."""
    for key, v in RETIRED.items():
        spec = claim_ks(v)["spec"]
        comps = spec.get("components") or []
        # Normal shape carries both Components on one Kustomization. The
        # `wait: true` split shape carries only the pvc one here and the kopiur
        # backup half in its own Kustomization, asserted just below.
        want = [KOPIUR_PVC_COMPONENT] if v.backup_ks else [KOPIUR_COMPONENT, KOPIUR_PVC_COMPONENT]
        require(
            comps == want,
            f"{key}: components must be exactly {want}, got {comps}. The pvc Component is NOT "
            f"optional: volsync's pvc.yaml was the only manifest emitting this claim and the "
            f"overlay runs prune: true, so dropping both deletes the data volume.",
        )
        require(
            spec.get("prune") is True,
            f"{key}: prune must stay true (this test's premise, and the fleet convention)",
        )
        if v.backup_ks:
            bspec = backup_ks(v)["spec"]
            require(
                str(bspec.get("path") or "").rstrip("/").endswith("components/kopiur/backup"),
                f"{key}: split-shape backup Kustomization {v.backup_ks!r} must point at "
                f"components/kopiur/backup, got {bspec.get('path')!r}",
            )
            require(
                bspec.get("wait") is not True,
                f"{key}: the split exists precisely so the kopiur half runs wait: false - the "
                f"standing Restore is Ready=False by design and would time the Kustomization out",
            )
            require(
                bspec.get("prune") is True,
                f"{key}: prune must stay true on the split-shape backup Kustomization too",
            )


def test_rendered_claim_is_correct_per_app() -> None:
    """Render components/kopiur/pvc under each retired overlay's own substitute map."""
    for key, v in RETIRED.items():
        app = v.ks
        claim, cap, rel = v.claim, v.capacity, v.rel
        env = {**substitute(claim_ks(v)), "SECRET_DOMAIN": "example.test"}
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
    for key, v in RETIRED.items():
        sub = substitute(claim_ks(v))
        require(
            sub.get("KOPIUR_CAPACITY") == v.capacity,
            f"{key}: KOPIUR_CAPACITY must be stated as {v.capacity!r} (the live claim), got "
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
    for key, v in RETIRED.items():
        # The cache variable drives the backup movers AND the standing Restore,
        # so it is read from whichever Kustomization renders those - which is
        # not the claim's one under the split shape.
        got = substitute(backup_ks(v)).get("KOPIUR_CACHE_CAPACITY")
        if key in RAISED_CACHE:
            want = RAISED_CACHE[key]
            require(
                got == want,
                f"{key}: KOPIUR_CACHE_CAPACITY must be {want} - measured to need more than the "
                f"{COMPONENT_DEFAULT_CACHE} default, and an r2 restore needs materially more "
                f"kopia cache than a ceph one (a failed Restore is terminal). Got {got!r}",
            )
        else:
            require(
                got in (None, COMPONENT_DEFAULT_CACHE),
                f"{key}: expected the {COMPONENT_DEFAULT_CACHE} default cache, got {got!r}. If "
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
    here = {(key.split("/", 1)[0], v.claim) for key, v in RETIRED.items()}
    require(
        stage3 == here,
        f"retired set drifted between the two tests: only in stage3 {sorted(stage3 - here)}, "
        f"only here {sorted(here - stage3)}",
    )
    require(
        len(here) == EXPECTED_RETIRED_COUNT,
        f"expected {EXPECTED_RETIRED_COUNT} retired volumes still in the fleet "
        f"(4 pilot + 4 wave two, less autobrr whose app was removed, + wave three); "
        f"got {len(here)}",
    )


def test_no_other_overlay_uses_the_pvc_component() -> None:
    """A dual-engine app must NOT add components/kopiur/pvc.

    It would collide with volsync's pvc.yaml on the same PVC name - the exact
    kustomize resource collision that is the reason this is a separate
    Component in the first place.
    """
    # (overlay file, Kustomization name) pairs entitled to carry the Component.
    entitled = {(v.rel, v.ks) for v in RETIRED.values()}
    offenders: list[str] = []
    for f in sorted(APPS_MAIN.rglob("*.yaml")):
        rel = f"{f.parent.name}/{f.name}"
        for d in load_multi(f):
            if d.get("kind") != "Kustomization":
                continue
            name = (d.get("metadata") or {}).get("name")
            comps = (d.get("spec") or {}).get("components") or []
            has_pvc = KOPIUR_PVC_COMPONENT in comps
            has_volsync = VOLSYNC_COMPONENT in comps
            if has_pvc and has_volsync:
                offenders.append(f"{rel}[{name}]: both volsync and kopiur/pvc (PVC name collision)")
            if has_pvc and (rel, name) not in entitled:
                offenders.append(
                    f"{rel}[{name}]: uses kopiur/pvc but is not a recorded retired volume"
                )
    require(not offenders, "kopiur/pvc Component misuse: " + "; ".join(offenders))


def test_retired_backup_shape_is_complete() -> None:
    """After losing VolSync, every retired claim must still render a full kopiur backup.

    Retirement must never leave a claim with zero engines or half a configuration.
    Render components/kopiur/backup under each overlay's own substitute map and
    require both destinations' SnapshotPolicy + SnapshotSchedule, the standing
    Restore, credentialProjection on each of those, and a Restore mover identity
    equal to the SnapshotPolicy's so the populator can read back what was written.
    """
    for key, v in RETIRED.items():
        app, claim, rel = v.ks, v.claim, v.rel
        env = {**substitute(backup_ks(v)), "SECRET_DOMAIN": "example.test"}
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
