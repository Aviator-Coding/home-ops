#!/usr/bin/env python3
"""Regression pin for the 2026-08-31 selfhosted backup-identity cleanup.

Covers two related decisions on the six Flux Kustomizations under
kubernetes/apps/main/selfhosted/ that used to declare a dead APP_UID/APP_GID
pair (n8n, obsidian-livesync, paperless-ngx, ntfy, syncthing, linkwarden), plus
changedetection which established the precedent (PR #1512):

  1. Dead-variable cleanup: APP_UID/APP_GID must stay out of each app's
     postBuild.substitute map. Nothing in this repo consumes either key, so a
     second unenforced identity declaration is a liability, not documentation.
     Wiring them in as a live-driving convention was rejected - Flux substitute
     has no cross-key templating - so the real mover identity lives only under
     KOPIUR_PUID/PGID and VOLSYNC_PUID/PGID.

  2. Latent defect fix (top-billed): obsidian-livesync's movers must render as
     5984:5984, matching its CouchDB workload. The prior 1000:1000 component
     default only "worked" because every file on that claim is mode 644/755 -
     the same latent-trap shape that broke changedetection when files were mode
     0600.

VolSync was RETIRED from `obsidian-livesync` and `ntfy` on 2026-09-02 (Stage 5
wave two - docs/backups/kopiur-wave-two-retirement-2026-09-02.md) and from
`changedetection` on 2026-09-04 (wave three, tier A -
docs/backups/kopiur-wave-three-retirement-2026-09-04.md), so those have no
VolSync mover left to assert on. Neither pin was dropped: the identity
check moved to the kopiur mover, where it binds harder (kopiur stages its source
read-only, gets no kubelet fsGroup fixup and fails CLOSED on the first
unreadable file, with no second engine left to mask a wrong identity), and the
VolSync half became an assertion that no VOLSYNC_* key survived retirement. The
dead APP_UID/APP_GID cleanup in (1) still covers all seven overlays.

Evidence is behavioural, not a source grep:
  - Parse each Flux Kustomization into a structured substitute map.
  - kustomize-build components/volsync/backup and components/kopiur/backup.
  - Run a Flux-shaped envsubst under each map and assert the rendered
    moverSecurityContext / podSecurityContext values.
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
APPS_MAIN = REPO / "kubernetes" / "apps" / "main" / "selfhosted"
VOLSYNC_BACKUP = REPO / "kubernetes" / "components" / "volsync" / "backup"
KOPIUR_BACKUP = REPO / "kubernetes" / "components" / "kopiur" / "backup"

# (overlay filename stem, claim, expected uid, expected gid)
# paperless-ngx and syncthing also protect a second claim via a separate
# Kustomization; those claims are covered by kopiur-stage3-test.py's fleet pin.
#
# Still dual-engine: both a VolSync mover and a kopiur mover must render at the
# measured identity.
DUAL_ENGINE_APPS: list[tuple[str, str, str, str]] = [
    ("n8n", "n8n", "1000", "1000"),
    ("paperless-ngx", "paperless-ngx", "1000", "1000"),
    ("syncthing", "syncthing", "1000", "1000"),
    ("linkwarden", "linkwarden", "1000", "1000"),
]

# VolSync RETIRED (Stage 5, captain decision). There is no VolSync mover left to
# check on these, so only the kopiur half of the identity assertion applies -
# and it applies harder, because kopiur stages its source READ-ONLY, gets no
# kubelet fsGroup fixup, and fails CLOSED on the first unreadable file where
# VolSync silently survived. The authoritative retired set is RETIRED_CLAIMS in
# kopiur-stage3-test.py; the retirement records are
# docs/backups/kopiur-wave-two-retirement-2026-09-02.md and
# docs/backups/kopiur-wave-three-retirement-2026-09-04.md.
RETIRED_APPS: list[tuple[str, str, str, str]] = [
    # wave two, 2026-09-02
    ("obsidian-livesync", "obsidian-livesync", "5984", "5984"),
    ("ntfy", "ntfy", "1000", "1000"),
    # wave three, 2026-09-04. changedetection is the app that established this
    # file's precedent (PR #1512) and it keeps its full pin - the dead
    # APP_UID/APP_GID must stay out, and the identity assertion simply moves to
    # the kopiur mover. Its VolSync half now asserts that no VOLSYNC_* key
    # survived retirement, which is the stronger check on this particular
    # claim: its `existingClaim` token carried no `:-default`, so a leftover
    # VOLSYNC_CLAIM reference would have rendered an EMPTY claim name.
    ("changedetection", "changedetection-config", "1000", "1000"),
]

APPS: list[tuple[str, str, str, str]] = DUAL_ENGINE_APPS + RETIRED_APPS

DEAD_KEYS = ("APP_UID", "APP_GID")


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_flux_ks(path: Path) -> dict[str, Any]:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    flux = [
        d
        for d in docs
        if d.get("kind") == "Kustomization"
        and str(d.get("apiVersion", "")).startswith("kustomize.toolkit.fluxcd.io")
    ]
    require(len(flux) >= 1, f"{path.name}: expected a Flux Kustomization")
    # Primary app Kustomization is the first doc that carries the backup
    # components (second-claim docs may share the file for paperless/syncthing).
    # A retired app has no volsync entry left, so the kopiur PVC Component -
    # which only a retired overlay may carry - identifies it instead.
    for suffix in ("components/volsync", "components/kopiur/pvc"):
        for d in flux:
            comps = [
                c for c in ((d.get("spec") or {}).get("components") or []) if isinstance(c, str)
            ]
            if any(c.rstrip("/").endswith(suffix) for c in comps):
                return d
    return flux[0]


def substitute_of(d: dict[str, Any]) -> dict[str, str]:
    raw = (((d.get("spec") or {}).get("postBuild") or {}).get("substitute")) or {}
    return {str(k): str(v) for k, v in raw.items()}


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


_RAW: dict[str, str] = {}


def kustomize_build(path: Path) -> str:
    key = str(path.resolve())
    if key in _RAW:
        return _RAW[key]
    exe = shutil.which("kustomize")
    cmd = [exe, "build", str(path)] if exe else None
    if cmd is None:
        kubectl = shutil.which("kubectl")
        require(kubectl, "neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(path)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    require(proc.returncode == 0, f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    _RAW[key] = proc.stdout
    return proc.stdout


def render(path: Path, env: dict[str, str]) -> list[dict[str, Any]]:
    # cluster-secrets (substituteFrom) supplies SECRET_DOMAIN at reconcile time;
    # stub it here so local envsubst matches what Flux can resolve.
    merged = {"SECRET_DOMAIN": "example.test", **env}
    rendered = _envsubst(kustomize_build(path), merged)
    unresolved = sorted(set(re.findall(r"\$\{[A-Za-z_][^}]*\}", rendered)))
    require(not unresolved, f"unresolved tokens after envsubst: {unresolved}")
    return [d for d in yaml.safe_load_all(rendered) if d]


def test_dead_app_uid_gid_absent() -> None:
    for stem, _claim, _uid, _gid in APPS:
        path = APPS_MAIN / f"{stem}.yaml"
        require(path.is_file(), f"missing overlay {path}")
        # Check every Flux Kustomization in the file (covers split second claims).
        docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
        for d in docs:
            if d.get("kind") != "Kustomization":
                continue
            if not str(d.get("apiVersion", "")).startswith("kustomize.toolkit.fluxcd.io"):
                continue
            sub = substitute_of(d)
            name = (d.get("metadata") or {}).get("name", stem)
            for key in DEAD_KEYS:
                require(
                    key not in sub,
                    f"{path.name} ({name}): {key} must stay absent from substitute "
                    f"(dead variable; see changedetection.yaml precedent)",
                )


def test_volsync_and_kopiur_movers_match_measured_identity() -> None:
    rows: list[str] = []
    retired_stems = {stem for stem, _c, _u, _g in RETIRED_APPS}
    for stem, claim, uid, gid in APPS:
        path = APPS_MAIN / f"{stem}.yaml"
        d = load_flux_ks(path)
        sub = substitute_of(d)
        retired = stem in retired_stems

        # Claim resolution mirrors the components. A retired overlay carries no
        # VOLSYNC_CLAIM at all, so the kopiur key (or the app name) is the only
        # claim name left - and it is what components/kopiur/pvc renders the PVC
        # from, so getting it wrong there loses the volume rather than a backup.
        kp_claim = sub.get("KOPIUR_CLAIM", sub.get("APP", claim))
        require(kp_claim == claim, f"{stem}: KOPIUR claim {kp_claim!r} != {claim!r}")

        if retired:
            leftover = sorted(k for k in sub if k.startswith("VOLSYNC_"))
            require(
                not leftover,
                f"{stem}: recorded as VolSync-retired but still carries {leftover} - nothing "
                f"reads those keys, and on the claim variables they misdescribe a rebuild",
            )
            vs_summary = "volsync=retired"
        else:
            vs_claim = sub.get("VOLSYNC_CLAIM", sub.get("APP", claim))
            require(vs_claim == claim, f"{stem}: VOLSYNC claim {vs_claim!r} != {claim!r}")

            vs_docs = render(VOLSYNC_BACKUP, sub)
            sources = [x for x in vs_docs if x.get("kind") == "ReplicationSource"]
            require(len(sources) == 3, f"{stem}: expected 3 ReplicationSources, got {len(sources)}")
            for src in sources:
                name = (src.get("metadata") or {}).get("name", "?")
                msc = ((src.get("spec") or {}).get("restic") or {}).get(
                    "moverSecurityContext"
                ) or {}
                got = [
                    str(msc.get("runAsUser")),
                    str(msc.get("runAsGroup")),
                    str(msc.get("fsGroup")),
                ]
                require(
                    got == [uid, gid, gid],
                    f"{stem} VolSync {name}: mover {got} != [{uid}, {gid}, {gid}]",
                )
                require(
                    (src.get("spec") or {}).get("sourcePVC") == claim,
                    f"{stem} {name}: sourcePVC mismatch",
                )
            vs_summary = f"volsync=3x{uid}:{gid}"

        kp_docs = render(KOPIUR_BACKUP, sub)
        policies = [x for x in kp_docs if x.get("kind") == "SnapshotPolicy"]
        require(len(policies) == 2, f"{stem}: expected 2 SnapshotPolicies, got {len(policies)}")
        for pol in policies:
            name = (pol.get("metadata") or {}).get("name", "?")
            psc = ((pol.get("spec") or {}).get("mover") or {}).get("podSecurityContext") or {}
            got = [str(psc.get("runAsUser")), str(psc.get("runAsGroup")), str(psc.get("fsGroup"))]
            require(
                got == [uid, gid, gid],
                f"{stem} kopiur {name}: mover {got} != [{uid}, {gid}, {gid}]",
            )

        rows.append(
            f"{stem}: claim={claim} workload-target={uid}:{gid} "
            f"{vs_summary} kopiur=2x{uid}:{gid} dead_keys=absent"
        )

    # Surface a stable summary line for evidence capture.
    print("identity_matrix:")
    for row in rows:
        print(f"  {row}")


def test_obsidian_mover_is_not_the_component_default() -> None:
    """Explicit guard on the latent defect: 5984 must not collapse to 1000.

    HISTORY, because the shape of this pin changed but the defect it guards did
    not. The 2026-08-31 fix this file was written for was on the VolSync side:
    obsidian-livesync's VolSync mover had been running at the unmatched 1000:1000
    component default and only "happened to read this volume" because every file
    on it is mode 644/755 - the identical latent-trap shape that broke
    changedetection once its files were mode 0600. VOLSYNC_PUID/PGID were pinned
    to 5984 to close it.

    VolSync was then RETIRED from this claim on 2026-09-02 (Stage 5 wave two),
    so those keys are gone and there is no VolSync mover left to check. The
    defect did not go away with them - it got sharper. kopiur stages its source
    READ-ONLY, so kubelet applies no fsGroup walk and a mismatched identity fails
    the backup CLOSED rather than silently succeeding, and there is no second
    engine left to mask it. The pin therefore moves to the kopiur mover, and the
    VolSync half becomes an assertion that no VOLSYNC_* key survived.
    """
    path = APPS_MAIN / "obsidian-livesync.yaml"
    sub = substitute_of(load_flux_ks(path))

    leftover = sorted(k for k in sub if k.startswith("VOLSYNC_"))
    require(
        not leftover,
        f"obsidian-livesync is VolSync-retired; {leftover} must not survive (see "
        f"docs/backups/kopiur-wave-two-retirement-2026-09-02.md)",
    )
    require(sub.get("KOPIUR_PUID") == "5984", "obsidian-livesync KOPIUR_PUID must be 5984")
    require(sub.get("KOPIUR_PGID") == "5984", "obsidian-livesync KOPIUR_PGID must be 5984")

    # Prove the default path would still be wrong if the keys were dropped -
    # otherwise the pin above asserts nothing.
    bare = {k: v for k, v in sub.items() if k not in ("KOPIUR_PUID", "KOPIUR_PGID")}
    defaulted = render(KOPIUR_BACKUP, bare)
    for pol in (x for x in defaulted if x.get("kind") == "SnapshotPolicy"):
        psc = ((pol.get("spec") or {}).get("mover") or {}).get("podSecurityContext") or {}
        require(
            str(psc.get("runAsUser")) == "1000",
            "sanity: without KOPIUR_PUID the component default must be 1000 "
            "(otherwise the pin is meaningless)",
        )

    fixed = render(KOPIUR_BACKUP, sub)
    policies = [x for x in fixed if x.get("kind") == "SnapshotPolicy"]
    require(len(policies) == 2, f"expected 2 SnapshotPolicies, got {len(policies)}")
    for pol in policies:
        name = (pol.get("metadata") or {}).get("name", "?")
        psc = ((pol.get("spec") or {}).get("mover") or {}).get("podSecurityContext") or {}
        require(
            str(psc.get("runAsUser")) == "5984" and str(psc.get("runAsGroup")) == "5984",
            f"obsidian-livesync {name} must render 5984:5984, got {psc}",
        )
    print("obsidian-livesync: default-path=1000:1000 pinned-path=5984:5984 (2 SnapshotPolicies)")


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

    run("dead_app_uid_gid_absent", test_dead_app_uid_gid_absent)
    run(
        "volsync_and_kopiur_movers_match_measured_identity",
        test_volsync_and_kopiur_movers_match_measured_identity,
    )
    run(
        "obsidian_mover_is_not_the_component_default",
        test_obsidian_mover_is_not_the_component_default,
    )

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
