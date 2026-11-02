#!/usr/bin/env python3
"""The B70 stays reachable by VA-API (tdarr) and Level Zero (llama.cpp) at once.

Prevents: a silent tdarr transcoding outage. libdrm ignores the device path it
is handed, fstat()s the fd and reopens the kernel's canonical DEVNAME
(`dri/renderD129`). A container that only has a renamed node fails before a
driver loads, while Level Zero opens any render node and stays green. That
split hid a multi-day outage after PR #1443 (fix PR #1490).

Asserts on the kustomize-built generic-device-plugin app and the tdarr values:

  1. `b70` keeps the renamed Level Zero layout (card0/renderD128). Editing its
     paths also changes all 99 device IDs and drops live allocations.
  2. `b70-vaapi` mounts the same by-path nodes at the kernel names
     (card1/renderD129).
  3. The config ConfigMap carries a name-suffix hash and is a subPath mount, so
     a config edit rolls the DaemonSet (kubelet never refreshes subPath).
  4. The plugin domain stays `devic.es`, so resource names do not change.
  5. tdarr-node requests `devic.es/b70-vaapi`, never `devic.es/b70`; the tdarr
     server takes no GPU; `transcodecpuWorkers=1` keeps a CPU fallback so a
     future name drift degrades instead of failing every job; worker counts
     match the Intel card (`healthcheckgpuWorkers=0`: the GPU health check is
     CUDA-only).

The live check after a GPU change is skill intel-gpu references/vaapi-check.md;
the mechanism is references/devices.md.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
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
            node_env.get("transcodegpuWorkers") == "2",
            f"transcodegpuWorkers must be 2, got {node_env.get('transcodegpuWorkers')!r}",
        )
        # The node's /app/configs is an emptyDir, so these env values reseed the
        # worker limits on every pod start. A cuda-only GPU health check can
        # never work on Intel, so it must stay 0.
        assert_true(
            node_env.get("healthcheckgpuWorkers") == "0",
            f"healthcheckgpuWorkers must be 0 (cuda-only), got {node_env.get('healthcheckgpuWorkers')!r}",
        )
        assert_true(
            node_env.get("healthcheckcpuWorkers") == "1",
            f"healthcheckcpuWorkers must be 1, got {node_env.get('healthcheckcpuWorkers')!r}",
        )

        evidence["result"] = "PASS"
        print(json.dumps(evidence, indent=2, sort_keys=True))
        print(
            "OK: b70-vaapi native mounts + hashed ConfigMap rollout + "
            f"tdarr-node on {B70_VAAPI_RESOURCE} with cpu fallback",
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
