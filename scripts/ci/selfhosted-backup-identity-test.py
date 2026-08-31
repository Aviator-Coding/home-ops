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

  2. Latent defect fix (top-billed): obsidian-livesync's VolSync movers must
     render as 5984:5984, matching its CouchDB workload and kopiur movers. The
     prior 1000:1000 component default only "worked" because every file on that
     claim is mode 644/755 - the same latent-trap shape that broke
     changedetection when files were mode 0600.

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
APPS: list[tuple[str, str, str, str]] = [
    ("n8n", "n8n", "1000", "1000"),
    ("obsidian-livesync", "obsidian-livesync", "5984", "5984"),
    ("paperless-ngx", "paperless-ngx", "1000", "1000"),
    ("ntfy", "ntfy", "1000", "1000"),
    ("syncthing", "syncthing", "1000", "1000"),
    ("linkwarden", "linkwarden", "1000", "1000"),
    # Precedent - must stay without APP_UID/APP_GID and with matching movers.
    ("changedetection", "changedetection-config", "1000", "1000"),
]

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
    # Primary app Kustomization is the first doc that carries volsync/kopiur
    # components (second-claim docs may share the file for paperless/syncthing).
    for d in flux:
        comps = [c for c in ((d.get("spec") or {}).get("components") or []) if isinstance(c, str)]
        if any(c.rstrip("/").endswith("components/volsync") for c in comps):
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
    for stem, claim, uid, gid in APPS:
        path = APPS_MAIN / f"{stem}.yaml"
        d = load_flux_ks(path)
        sub = substitute_of(d)
        # Claim resolution mirrors the components.
        vs_claim = sub.get("VOLSYNC_CLAIM", sub.get("APP", claim))
        kp_claim = sub.get("KOPIUR_CLAIM", sub.get("APP", claim))
        require(vs_claim == claim, f"{stem}: VOLSYNC claim {vs_claim!r} != {claim!r}")
        require(kp_claim == claim, f"{stem}: KOPIUR claim {kp_claim!r} != {claim!r}")

        vs_docs = render(VOLSYNC_BACKUP, sub)
        sources = [x for x in vs_docs if x.get("kind") == "ReplicationSource"]
        require(len(sources) == 3, f"{stem}: expected 3 ReplicationSources, got {len(sources)}")
        for src in sources:
            name = (src.get("metadata") or {}).get("name", "?")
            msc = ((src.get("spec") or {}).get("restic") or {}).get("moverSecurityContext") or {}
            got = [str(msc.get("runAsUser")), str(msc.get("runAsGroup")), str(msc.get("fsGroup"))]
            require(
                got == [uid, gid, gid],
                f"{stem} VolSync {name}: mover {got} != [{uid}, {gid}, {gid}]",
            )
            require(
                (src.get("spec") or {}).get("sourcePVC") == claim,
                f"{stem} {name}: sourcePVC mismatch",
            )

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
            f"volsync=3x{uid}:{gid} kopiur=2x{uid}:{gid} dead_keys=absent"
        )

    # Surface a stable summary line for evidence capture.
    print("identity_matrix:")
    for row in rows:
        print(f"  {row}")


def test_obsidian_volsync_is_not_the_component_default() -> None:
    """Explicit guard on the latent defect: 5984 must not collapse to 1000."""
    path = APPS_MAIN / "obsidian-livesync.yaml"
    sub = substitute_of(load_flux_ks(path))
    require(sub.get("VOLSYNC_PUID") == "5984", "obsidian-livesync VOLSYNC_PUID must be 5984")
    require(sub.get("VOLSYNC_PGID") == "5984", "obsidian-livesync VOLSYNC_PGID must be 5984")
    require(sub.get("KOPIUR_PUID") == "5984", "obsidian-livesync KOPIUR_PUID must be 5984")
    require(sub.get("KOPIUR_PGID") == "5984", "obsidian-livesync KOPIUR_PGID must be 5984")

    # Prove the default path would still be wrong if the keys were dropped.
    bare = {k: v for k, v in sub.items() if k not in ("VOLSYNC_PUID", "VOLSYNC_PGID")}
    defaulted = render(VOLSYNC_BACKUP, bare)
    for src in (x for x in defaulted if x.get("kind") == "ReplicationSource"):
        msc = ((src.get("spec") or {}).get("restic") or {}).get("moverSecurityContext") or {}
        require(
            str(msc.get("runAsUser")) == "1000",
            "sanity: without VOLSYNC_PUID the component default must be 1000 "
            "(otherwise the pin is meaningless)",
        )

    fixed = render(VOLSYNC_BACKUP, sub)
    for src in (x for x in fixed if x.get("kind") == "ReplicationSource"):
        name = (src.get("metadata") or {}).get("name", "?")
        msc = ((src.get("spec") or {}).get("restic") or {}).get("moverSecurityContext") or {}
        require(
            str(msc.get("runAsUser")) == "5984" and str(msc.get("runAsGroup")) == "5984",
            f"obsidian-livesync {name} must render 5984:5984 after the fix, got {msc}",
        )
    print("obsidian-livesync: default-path=1000:1000 fixed-path=5984:5984 (3 ReplicationSources)")


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
        "obsidian_volsync_is_not_the_component_default",
        test_obsidian_volsync_is_not_the_component_default,
    )

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
