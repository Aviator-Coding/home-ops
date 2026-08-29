#!/usr/bin/env python3
"""Semantic regression for the B70 VA-API restore (tdarr + generic-device-plugin).

After PR #1443 the Arc B70 was re-exposed via generic-device-plugin under
renamed DRM nodes (card0/renderD128). That rename is fatal to VA-API: libdrm
ignores the path it is handed, fstat()s the fd, reads
/sys/dev/char/<major>:<minor>/uevent, re-derives the canonical DEVNAME, and
reopens that path. A container that only has the renamed node fails before any
driver loads. Tdarr had no CPU fallback, so every GPU job failed.

This change restores transcoding by:

  1. Keeping the Level Zero `b70` group byte-identical (renamed paths).
  2. Adding a SEPARATE `b70-vaapi` group that mounts the same by-path nodes at
     the kernel's own names (card1/renderD129).
  3. Pointing tdarr-node at `devic.es/b70-vaapi`.
  4. Setting `transcodecpuWorkers=1` so a future name drift degrades instead of
     causing a total outage.
  5. Enabling the configMapGenerator name-suffix hash so a config-only edit
     actually rolls the DaemonSet (config is a subPath mount).

This test does not grep source text. It:

  - runs `kustomize build` on the generic-device-plugin app and parses the
    emitted ConfigMap + HelmRelease as structured objects
  - walks the tdarr HelmRelease values as nested maps for resource keys and
    env entries
  - simulates the libdrm DEVNAME reopen trap against a fake /dev + /sys tree
    (A-B-A: renamed-only fails, name-faithful succeeds, removing the canonical
    name fails again)

Live post-merge gates (vainfo / av1_qsv / real library file) remain the
commands in docs/media-stack.md; offline here we prove the GitOps inputs that
drive those outcomes cannot silently regress.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parents[2]
GDP_APP = ROOT / "kubernetes/apps/base/system/generic-device-plugin/app"
TDARR_HR = ROOT / "kubernetes/apps/base/media/tdarr/app/helmrelease.yaml"

B70_RESOURCE = "devic.es/b70"
B70_VAAPI_RESOURCE = "devic.es/b70-vaapi"
B70_PCI = "0000:03:00.0"
B70_BY_PATH_CARD = f"/dev/dri/by-path/pci-{B70_PCI}-card"
B70_BY_PATH_RENDER = f"/dev/dri/by-path/pci-{B70_PCI}-render"

# Level Zero consumers keep the renamed mount layout.
B70_RENAMED_MOUNTS = {
    B70_BY_PATH_CARD: "/dev/dri/card0",
    B70_BY_PATH_RENDER: "/dev/dri/renderD128",
}
# VA-API consumers need the kernel-canonical names.
B70_NATIVE_MOUNTS = {
    B70_BY_PATH_CARD: "/dev/dri/card1",
    B70_BY_PATH_RENDER: "/dev/dri/renderD129",
}

# DRM char major used by the kernel for /dev/dri/* on this host.
DRM_MAJOR = 226
B70_RENDER_MINOR = 129
B70_CANONICAL_DEVNAME = "dri/renderD129"


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def kustomize_build(path: Path) -> list[dict[str, Any]]:
    """Invoke the real kustomize consumer and return parsed documents."""
    try:
        proc = subprocess.run(
            [
                "kustomize",
                "build",
                str(path),
                "--load-restrictor",
                "LoadRestrictionsNone",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        proc = subprocess.run(
            [
                "kubectl",
                "kustomize",
                str(path),
                "--load-restrictor",
                "LoadRestrictionsNone",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    docs = [
        doc
        for doc in yaml.safe_load_all(proc.stdout)
        if isinstance(doc, dict)
    ]
    assert_true(bool(docs), f"kustomize build of {path} produced no documents")
    return docs


def device_mount_map(device: dict[str, Any]) -> dict[str, str | None]:
    """host path -> mountPath for every path entry in a device group."""
    out: dict[str, str | None] = {}
    for group in device.get("groups") or []:
        for entry in group.get("paths") or []:
            host = entry.get("path")
            if not host:
                continue
            out[str(host)] = entry.get("mountPath")
    return out


def walk(obj: Any) -> Iterator[Any]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk(item)


def controller_env(values: dict[str, Any], controller: str) -> dict[str, str]:
    """Flatten env list/dict for one app-template controller into name->value."""
    controllers = values.get("controllers") or {}
    ctrl = controllers.get(controller) or {}
    containers = ctrl.get("containers") or {}
    env_out: dict[str, str] = {}
    for cval in containers.values():
        env = (cval or {}).get("env")
        if isinstance(env, dict):
            for k, v in env.items():
                env_out[str(k)] = "" if v is None else str(v)
        elif isinstance(env, list):
            for item in env:
                if not isinstance(item, dict) or "name" not in item:
                    continue
                if "value" in item:
                    env_out[str(item["name"])] = str(item["value"])
    return env_out


def controller_extended_resources(values: dict[str, Any], controller: str) -> set[str]:
    """Extended resource names under one controller's resources blocks."""
    controllers = values.get("controllers") or {}
    ctrl = controllers.get(controller) or {}
    found: set[str] = set()
    for node in walk(ctrl):
        if not isinstance(node, dict) or "resources" not in node:
            continue
        resources = node.get("resources") or {}
        if not isinstance(resources, dict):
            continue
        for section in ("requests", "limits"):
            block = resources.get(section) or {}
            if not isinstance(block, dict):
                continue
            for key in block:
                k = str(key)
                if "/" in k or k.startswith("devic."):
                    found.add(k)
    return found


def simulate_libdrm_reopen(has_canonical: bool) -> dict[str, Any]:
    """Reproduce the libdrm name-faithfulness trap against a fake device tree.

    Mirrors the A-B-A proof from the incident: open the RENAMED path, fstat it,
    read uevent for DEVNAME, and try to reopen the canonical path. When the
    container only has the renamed node, reopen fails; when the canonical name
    is also present (even as a hard link / same inode), reopen succeeds.
    """
    with tempfile.TemporaryDirectory(prefix="b70-vaapi-libdrm-") as tmp:
        root = Path(tmp)
        dri = root / "dev" / "dri"
        dri.mkdir(parents=True)
        sysfs = root / "sys" / "dev" / "char" / f"{DRM_MAJOR}:{B70_RENDER_MINOR}"
        sysfs.mkdir(parents=True)

        renamed = dri / "renderD128"
        # Regular file standing in for a device node; inode identity is enough.
        renamed.write_bytes(b"b70-render")
        os.chmod(renamed, 0o666)

        # uevent reports the kernel-canonical DEVNAME, not the container path.
        (sysfs / "uevent").write_text(
            f"MAJOR={DRM_MAJOR}\nMINOR={B70_RENDER_MINOR}\nDEVNAME={B70_CANONICAL_DEVNAME}\n"
        )

        canonical = root / "dev" / B70_CANONICAL_DEVNAME
        if has_canonical:
            # Same inode under the canonical name - what a name-faithful mount does.
            os.link(renamed, canonical)

        # --- libdrm-shaped sequence ---
        fd = os.open(renamed, os.O_RDWR)
        try:
            st = os.fstat(fd)
            # Real DRM nodes carry major/minor via st_rdev; our stand-in uses the
            # known B70 render minor the plugin exposes. The lookup key is what
            # matters for the trap.
            major, minor = DRM_MAJOR, B70_RENDER_MINOR
            assert_true(
                stat.S_ISREG(st.st_mode) or stat.S_ISCHR(st.st_mode),
                "fixture node must be openable",
            )
            uevent_path = root / "sys" / "dev" / "char" / f"{major}:{minor}" / "uevent"
            uevent = uevent_path.read_text()
            match = re.search(r"^DEVNAME=(.+)$", uevent, re.MULTILINE)
            assert_true(match is not None, "uevent must carry DEVNAME")
            devname = match.group(1).strip()
            assert_true(
                devname == B70_CANONICAL_DEVNAME,
                f"DEVNAME must be {B70_CANONICAL_DEVNAME}, got {devname}",
            )
            reopen_path = root / "dev" / devname
            reopen_ok = False
            reopen_error: str | None = None
            try:
                fd2 = os.open(reopen_path, os.O_RDWR)
                os.close(fd2)
                reopen_ok = True
            except OSError as exc:
                reopen_error = f"{type(exc).__name__}: {exc.errno} {exc.strerror}"
        finally:
            os.close(fd)

        return {
            "opened": str(renamed.relative_to(root)),
            "devname": B70_CANONICAL_DEVNAME,
            "canonicalPresent": has_canonical,
            "reopenOk": reopen_ok,
            "reopenError": reopen_error,
        }


def main() -> int:
    evidence: dict[str, Any] = {}
    try:
        docs = kustomize_build(GDP_APP)
        cms = [
            d
            for d in docs
            if d.get("kind") == "ConfigMap"
            and str((d.get("metadata") or {}).get("name", "")).startswith(
                "generic-device-plugin"
            )
        ]
        hrs = [
            d
            for d in docs
            if d.get("kind") == "HelmRelease"
            and (d.get("metadata") or {}).get("name") == "generic-device-plugin"
        ]
        assert_true(len(cms) == 1, f"expected one generic-device-plugin ConfigMap, got {len(cms)}")
        assert_true(len(hrs) == 1, f"expected one generic-device-plugin HelmRelease, got {len(hrs)}")
        cm = cms[0]
        hr = hrs[0]
        cm_name = (cm.get("metadata") or {}).get("name") or ""

        # Hash must be enabled: static name would be exactly "generic-device-plugin".
        assert_true(
            cm_name != "generic-device-plugin",
            "configMapGenerator hash is disabled; subPath mounts would never roll out",
        )
        assert_true(
            bool(re.fullmatch(r"generic-device-plugin-[a-z0-9]{5,10}", cm_name)),
            f"ConfigMap name must carry a content hash suffix, got {cm_name!r}",
        )

        cfg_raw = (cm.get("data") or {}).get("config.yaml")
        assert_true(isinstance(cfg_raw, str) and cfg_raw, "ConfigMap missing config.yaml")
        cfg = yaml.safe_load(cfg_raw)
        devices = {d["name"]: d for d in (cfg.get("devices") or []) if "name" in d}
        evidence["configMap"] = {
            "name": cm_name,
            "deviceNames": sorted(devices),
        }

        assert_true("b70" in devices, "b70 device group missing from built config")
        assert_true(
            "b70-vaapi" in devices,
            "b70-vaapi device group missing from built config",
        )

        b70_mounts = device_mount_map(devices["b70"])
        vaapi_mounts = device_mount_map(devices["b70-vaapi"])
        evidence["b70Mounts"] = b70_mounts
        evidence["b70VaapiMounts"] = vaapi_mounts

        assert_true(
            b70_mounts == B70_RENAMED_MOUNTS,
            f"b70 group must stay on renamed Level Zero layout {B70_RENAMED_MOUNTS}, got {b70_mounts}",
        )
        assert_true(
            vaapi_mounts == B70_NATIVE_MOUNTS,
            f"b70-vaapi must expose kernel-canonical names {B70_NATIVE_MOUNTS}, got {vaapi_mounts}",
        )
        # Same physical card (identical host by-path nodes); only mountPath differs.
        assert_true(
            set(b70_mounts) == set(vaapi_mounts) == {B70_BY_PATH_CARD, B70_BY_PATH_RENDER},
            "both groups must bind the same B70 by-path host nodes",
        )
        assert_true(
            b70_mounts[B70_BY_PATH_RENDER] != vaapi_mounts[B70_BY_PATH_RENDER],
            "b70 and b70-vaapi render mountPaths must differ (rename vs native)",
        )

        # Built HelmRelease must reference the hashed ConfigMap and keep subPath.
        values = (hr.get("spec") or {}).get("values") or {}
        persistence = values.get("persistence") or {}
        config_vol = persistence.get("config") or {}
        evidence["helmreleasePersistence"] = {
            "type": config_vol.get("type"),
            "name": config_vol.get("name"),
            "globalMounts": config_vol.get("globalMounts"),
        }
        assert_true(
            config_vol.get("type") == "configMap",
            f"config persistence type must be configMap, got {config_vol.get('type')}",
        )
        assert_true(
            config_vol.get("name") == cm_name,
            f"HelmRelease must reference hashed ConfigMap {cm_name}, got {config_vol.get('name')}",
        )
        mounts = config_vol.get("globalMounts") or []
        assert_true(len(mounts) >= 1, "config volume needs a mount")
        sub_paths = {m.get("subPath") for m in mounts if isinstance(m, dict)}
        assert_true(
            "config.yaml" in sub_paths,
            f"config must be a subPath mount of config.yaml (kubelet never refreshes subPath); got {sub_paths}",
        )

        # Domain stays devic.es so resource names are devic.es/<group>.
        args = (
            ((values.get("controllers") or {}).get("generic-device-plugin") or {})
            .get("containers", {})
            .get("app", {})
            .get("args")
            or []
        )
        evidence["pluginArgs"] = args
        assert_true(
            any(a == "--domain=devic.es" for a in args),
            f"plugin domain must be devic.es so resources are {B70_RESOURCE}/…, args={args}",
        )

        # --- tdarr-node consumer contract ---
        assert_true(TDARR_HR.is_file(), f"missing {TDARR_HR}")
        tdarr_docs = [
            d for d in yaml.safe_load_all(TDARR_HR.read_text()) if isinstance(d, dict)
        ]
        assert_true(len(tdarr_docs) >= 1, "tdarr helmrelease empty")
        tdarr_values = (tdarr_docs[0].get("spec") or {}).get("values") or {}
        node_env = controller_env(tdarr_values, "tdarr-node")
        node_resources = controller_extended_resources(tdarr_values, "tdarr-node")
        server_resources = controller_extended_resources(tdarr_values, "tdarr")
        evidence["tdarrNode"] = {
            "env": {
                k: node_env.get(k)
                for k in (
                    "transcodecpuWorkers",
                    "transcodegpuWorkers",
                    "healthcheckcpuWorkers",
                    "healthcheckgpuWorkers",
                )
            },
            "extendedResources": sorted(node_resources),
            "serverExtendedResources": sorted(server_resources),
        }

        assert_true(
            B70_VAAPI_RESOURCE in node_resources,
            f"tdarr-node must request {B70_VAAPI_RESOURCE}, found {sorted(node_resources)}",
        )
        assert_true(
            B70_RESOURCE not in node_resources,
            f"tdarr-node must NOT request renamed {B70_RESOURCE}, found {sorted(node_resources)}",
        )
        assert_true(
            not server_resources,
            f"tdarr server must not take a GPU resource, found {sorted(server_resources)}",
        )
        assert_true(
            node_env.get("transcodecpuWorkers") == "1",
            f"transcodecpuWorkers must be 1 (CPU fallback), got {node_env.get('transcodecpuWorkers')!r}",
        )
        assert_true(
            node_env.get("transcodegpuWorkers") == "1",
            f"transcodegpuWorkers must stay 1, got {node_env.get('transcodegpuWorkers')!r}",
        )

        # --- libdrm A-B-A mechanism simulation ---
        phase_a = simulate_libdrm_reopen(has_canonical=True)
        phase_b = simulate_libdrm_reopen(has_canonical=False)
        phase_a2 = simulate_libdrm_reopen(has_canonical=True)
        evidence["libdrmTrapSimulation"] = {
            "A_nameFaithful": phase_a,
            "B_renamedOnly": phase_b,
            "A2_restored": phase_a2,
        }
        assert_true(phase_a["reopenOk"] is True, f"A (canonical present) must succeed: {phase_a}")
        assert_true(
            phase_b["reopenOk"] is False,
            f"B (renamed only) must fail reopen of {B70_CANONICAL_DEVNAME}: {phase_b}",
        )
        assert_true(phase_a2["reopenOk"] is True, f"A2 (restored) must succeed again: {phase_a2}")

        evidence["result"] = "PASS"
        print(json.dumps(evidence, indent=2, sort_keys=True))
        print(
            "OK: b70-vaapi native mounts + hashed ConfigMap rollout + "
            f"tdarr-node on {B70_VAAPI_RESOURCE} with cpu fallback; "
            "libdrm DEVNAME reopen A-B-A holds",
            file=sys.stderr,
        )
        return 0
    except Failure as exc:
        evidence["result"] = "FAIL"
        evidence["error"] = str(exc)
        print(json.dumps(evidence, indent=2, sort_keys=True))
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        evidence["result"] = "FAIL"
        evidence["error"] = f"command failed: {exc.stderr or exc}"
        print(json.dumps(evidence, indent=2, sort_keys=True))
        print(f"FAIL: {evidence['error']}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
