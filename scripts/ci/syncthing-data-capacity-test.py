#!/usr/bin/env python3
"""Semantic regression test for the syncthing-data 15Gi right-size contract.

2026-08-31: the syncthing-data claim held 28 KB against a 100Gi request and was
recreated at 15Gi (Kubernetes cannot shrink a PVC). The four purpose folders
(documents/screenshots/camera-roll/projects) are runtime Syncthing config on
the config PVC - not GitOps - so this test pins only the GitOps half:

  1. The plain `syncthing-data` PVC Flux owns is 15Gi on ceph-block.
  2. The multi-claim VolSync Kustomization for that volume substitutes
     VOLSYNC_CAPACITY=15Gi (and VOLSYNC_CACHE_CAPACITY=5Gi, 20-50% band).
  3. Rendering `components/volsync/backup` under that substitute map produces a
     ReplicationDestination whose restic.capacity is 15Gi - proving the value
     sizes the restore destination volume, not the live app claim (the backup
     path deliberately does not create a PVC).
  4. The config volume (`syncthing`, 1Gi) stays a SEPARATE claim of its own
     size, and the HelmRelease still mounts it separately from syncthing-data.
     VolSync was retired from the config claim on 2026-09-04 (Stage 5 wave
     three, tier C), so that size now lives on KOPIUR_CAPACITY - the point this
     assertion has always been making is that the two claims do not get
     conflated, and 1Gi vs 15Gi is exactly as load-bearing under one engine as
     under two. `syncthing-data` itself is NOT retired: wave two measured it at
     5 files / 531 B with 15Gi of intended capacity behind a 5Gi cache, and
     re-measurement on 2026-09-04 found it unchanged, so it stays dual-engine.
  5. Cache stays 5Gi (33% of 15Gi).

Live folder shares, Mac pairing, and backup Success phases are cluster state
and are outside this GitOps pin.
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
PVC_PATH = REPO / "kubernetes/apps/base/selfhosted/syncthing/app/pvc.yaml"
APP_DIR = REPO / "kubernetes/apps/base/selfhosted/syncthing/app"
OVERLAY_PATH = REPO / "kubernetes/apps/main/selfhosted/syncthing.yaml"
VOLSYNC_BACKUP = REPO / "kubernetes/components/volsync/backup"

DATA_CAPACITY = "15Gi"
CONFIG_CAPACITY = "1Gi"
CACHE_CAPACITY = "5Gi"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_docs(path: Path) -> list[dict[str, Any]]:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    require(docs, f"{path} produced no YAML documents")
    return docs


def parse_gi(value: str) -> float:
    require(isinstance(value, str) and value.endswith("Gi"), f"expected Gi quantity, got {value!r}")
    return float(value[: -len("Gi")])


def _kustomize_build(path: Path) -> str:
    binary = shutil.which("kustomize")
    if binary:
        cmd = [binary, "build", str(path)]
    else:
        cmd = [
            "kubectl",
            "kustomize",
            str(path),
            "--load-restrictor",
            "LoadRestrictionsNone",
        ]
    try:
        return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as exc:
        raise Failure(f"kustomize build failed for {path}: {exc.stderr or exc}") from exc
    except FileNotFoundError as exc:
        raise Failure("neither kustomize nor kubectl is available") from exc


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _envsubst(text: str, env: dict[str, str]) -> str:
    """Flux-shaped ${VAR} / ${VAR:-default} substitution (no nested defaults needed here)."""

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        if key in env:
            return env[key]
        if default is not None:
            return default
        return match.group(0)

    return _ENV_PATTERN.sub(repl, text)


def overlay_kustomizations() -> list[dict[str, Any]]:
    docs = load_docs(OVERLAY_PATH)
    for doc in docs:
        require(doc.get("kind") == "Kustomization", f"unexpected kind in overlay: {doc.get('kind')}")
    return docs


def substitute_map(doc: dict[str, Any]) -> dict[str, str]:
    raw = ((doc.get("spec") or {}).get("postBuild") or {}).get("substitute") or {}
    require(isinstance(raw, dict), "postBuild.substitute must be a mapping")
    return {str(k): str(v) for k, v in raw.items()}


def test_data_pvc_is_right_sized() -> None:
    docs = load_docs(PVC_PATH)
    require(len(docs) == 1, f"expected one PVC document, got {len(docs)}")
    pvc = docs[0]
    require(pvc.get("kind") == "PersistentVolumeClaim", "pvc.yaml must be a PVC")
    require((pvc.get("metadata") or {}).get("name") == "syncthing-data", "PVC name must be syncthing-data")
    spec = pvc.get("spec") or {}
    require(spec.get("storageClassName") == "ceph-block", "syncthing-data must stay on ceph-block")
    require(spec.get("accessModes") == ["ReadWriteOnce"], "syncthing-data must stay RWO")
    # Plain app claim: no dataSourceRef (recreate yields empty volume, not a restore seed).
    require("dataSourceRef" not in spec, "syncthing-data PVC must not carry dataSourceRef")
    storage = ((spec.get("resources") or {}).get("requests") or {}).get("storage")
    require(storage == DATA_CAPACITY, f"syncthing-data storage must be {DATA_CAPACITY}, got {storage!r}")


def test_app_kustomize_emits_matching_pvc() -> None:
    built = _kustomize_build(APP_DIR)
    pvcs = [
        d
        for d in yaml.safe_load_all(built)
        if isinstance(d, dict) and d.get("kind") == "PersistentVolumeClaim"
    ]
    data = [p for p in pvcs if (p.get("metadata") or {}).get("name") == "syncthing-data"]
    require(len(data) == 1, f"app build must emit exactly one syncthing-data PVC, got {len(data)}")
    storage = (((data[0].get("spec") or {}).get("resources") or {}).get("requests") or {}).get("storage")
    require(storage == DATA_CAPACITY, f"built syncthing-data storage must be {DATA_CAPACITY}, got {storage!r}")

    hrs = [d for d in yaml.safe_load_all(built) if isinstance(d, dict) and d.get("kind") == "HelmRelease"]
    require(len(hrs) == 1, "app build must emit the syncthing HelmRelease")
    persistence = ((hrs[0].get("spec") or {}).get("values") or {}).get("persistence") or {}
    sync_data = persistence.get("sync-data") or {}
    require(
        sync_data.get("existingClaim") == "syncthing-data",
        "HelmRelease must mount the plain syncthing-data claim",
    )
    config = persistence.get("config") or {}
    require(
        "existingClaim" in config,
        "config volume must keep using the existing config claim (identity PVC)",
    )
    require(
        config.get("existingClaim") != "syncthing-data",
        "config volume must not be retargeted at the data claim",
    )


def test_overlay_capacity_substitutions() -> None:
    docs = overlay_kustomizations()
    by_name = {(d.get("metadata") or {}).get("name"): d for d in docs}
    require("syncthing" in by_name, "missing primary syncthing Kustomization")
    require("syncthing-data" in by_name, "missing syncthing-data VolSync Kustomization")
    require("syncthing-data-kopiur" in by_name, "missing syncthing-data-kopiur Kustomization")

    primary = substitute_map(by_name["syncthing"])
    # The config claim is kopiur-only since 2026-09-04, so its declared size
    # moved from VOLSYNC_CAPACITY to KOPIUR_CAPACITY. Asserted in both
    # directions: the size must be stated at 1Gi, and no VOLSYNC_* key may
    # survive - a leftover one would be the signature of a half-revert and
    # would also misdescribe what a rebuild provisions.
    require(
        primary.get("KOPIUR_CAPACITY") == CONFIG_CAPACITY,
        f"config vol KOPIUR_CAPACITY must stay {CONFIG_CAPACITY}, got "
        f"{primary.get('KOPIUR_CAPACITY')!r}",
    )
    leftover = sorted(k for k in primary if k.startswith("VOLSYNC_"))
    require(
        not leftover,
        f"config vol still declares {leftover} after its 2026-09-04 retirement",
    )
    # The claim name matches the app name, so KOPIUR_CLAIM is legitimately
    # absent and components/kopiur/pvc renders ${KOPIUR_CLAIM:-${APP}}. What
    # must hold is that the primary Kustomization protects `syncthing` and not
    # the data claim.
    require(
        primary.get("KOPIUR_CLAIM", primary.get("APP")) == "syncthing",
        "primary Kustomization must keep protecting the config claim named syncthing, got "
        f"{primary.get('KOPIUR_CLAIM', primary.get('APP'))!r}",
    )

    data = by_name["syncthing-data"]
    require(
        (data.get("spec") or {}).get("path") == "./kubernetes/components/volsync/backup",
        "syncthing-data path must be the backup-only volsync bundle (no PVC create)",
    )
    data_sub = substitute_map(data)
    require(data_sub.get("APP") == "syncthing-data", "syncthing-data APP must be the claim name")
    require(
        data_sub.get("VOLSYNC_CLAIM") == "syncthing-data",
        "syncthing-data VOLSYNC_CLAIM must be the data claim",
    )
    require(
        data_sub.get("VOLSYNC_CAPACITY") == DATA_CAPACITY,
        f"data VOLSYNC_CAPACITY must be {DATA_CAPACITY}, got {data_sub.get('VOLSYNC_CAPACITY')!r}",
    )
    require(
        data_sub.get("VOLSYNC_CACHE_CAPACITY") == CACHE_CAPACITY,
        f"data VOLSYNC_CACHE_CAPACITY must be {CACHE_CAPACITY}, got {data_sub.get('VOLSYNC_CACHE_CAPACITY')!r}",
    )
    cache_ratio = parse_gi(CACHE_CAPACITY) / parse_gi(DATA_CAPACITY)
    require(
        0.20 <= cache_ratio <= 0.50,
        f"cache/capacity ratio {cache_ratio:.2f} outside the repo 20-50% band",
    )

    kopiur_sub = substitute_map(by_name["syncthing-data-kopiur"])
    require(kopiur_sub.get("KOPIUR_CLAIM") == "syncthing-data", "kopiur must target syncthing-data")
    require(
        kopiur_sub.get("KOPIUR_CACHE_CAPACITY") == CACHE_CAPACITY,
        f"kopiur cache must stay {CACHE_CAPACITY}, got {kopiur_sub.get('KOPIUR_CACHE_CAPACITY')!r}",
    )


def test_rendered_volsync_destination_tracks_data_capacity() -> None:
    data_ks = next(
        d for d in overlay_kustomizations() if (d.get("metadata") or {}).get("name") == "syncthing-data"
    )
    env = substitute_map(data_ks)
    rendered = _envsubst(_kustomize_build(VOLSYNC_BACKUP), env)
    unresolved = sorted(set(re.findall(r"\$\{[A-Za-z_][^}]*\}", rendered)))
    # cluster-secrets-backed tokens may remain; capacity tokens must not.
    capacity_unresolved = [t for t in unresolved if "CAPACITY" in t or t.startswith("${APP") or "CLAIM" in t]
    require(not capacity_unresolved, f"unresolved capacity/claim tokens: {capacity_unresolved}")

    docs = [d for d in yaml.safe_load_all(rendered) if isinstance(d, dict)]
    require(docs, "volsync backup render produced no documents")

    # backup-only path must not mint a PVC - the app owns syncthing-data.
    pvcs = [d for d in docs if d.get("kind") == "PersistentVolumeClaim"]
    require(not pvcs, f"backup-only render must not create PVCs, got {[p.get('metadata') for p in pvcs]}")

    rds = [d for d in docs if d.get("kind") == "ReplicationDestination"]
    require(rds, "expected at least one ReplicationDestination")
    for rd in rds:
        name = (rd.get("metadata") or {}).get("name")
        cap = ((rd.get("spec") or {}).get("restic") or {}).get("capacity")
        require(
            cap == DATA_CAPACITY,
            f"ReplicationDestination {name} restic.capacity must be {DATA_CAPACITY}, got {cap!r}",
        )
        cache = ((rd.get("spec") or {}).get("restic") or {}).get("cacheCapacity")
        require(
            cache == CACHE_CAPACITY,
            f"ReplicationDestination {name} restic.cacheCapacity must be {CACHE_CAPACITY}, got {cache!r}",
        )


def main() -> int:
    tests = [
        test_data_pvc_is_right_sized,
        test_app_kustomize_emits_matching_pvc,
        test_overlay_capacity_substitutions,
        test_rendered_volsync_destination_tracks_data_capacity,
    ]
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            test()
        except Failure as exc:
            failed += 1
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors as failures
            failed += 1
            print(f"[FAIL] {name}: unexpected {type(exc).__name__}: {exc}")
        else:
            print(f"[PASS] {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
