#!/usr/bin/env python3
"""Semantic regression test for scoping gpu.intel.com/xe to the iGPU.

PR #1443 deferred `allowIDs: "0xa7a0"` on the Intel GpuDevicePlugin until the
iGPUs advertised xe. With that precondition closed, the HelmRelease must:

  1. Render `allowIDs: "0xa7a0"` into the chart values Flux applies.
  2. Produce a GpuDevicePlugin CR whose spec.allowIDs is exactly that value
     (chart values map 1:1 onto CR fields - confirmed by helm template).
  3. Keep media/browser consumers (jellyfin/plex/playwright) on
     gpu.intel.com/xe only, never devic.es/b70.
  4. Keep discrete B70 consumers (vllm/vllm-embed/comfyui/tdarr-node) on
     devic.es/b70 only, never gpu.intel.com/xe.

This test does not grep source text. It:
  - loads the kustomize-built HelmRelease as a structured object
  - invokes `helm template` against the published chart with those values
  - walks app HelmRelease values as nested maps and collects resource
    request/limit keys from every containers[*].resources block

Live post-merge gate remains the allocatable count check in
docs/ai-gpu-changelog.md (talos-3 xe drops 198 -> 99). Offline here we prove
the GitOps inputs that drive that outcome cannot silently re-pool the B70.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parents[2]
GPU_APP = ROOT / "kubernetes/apps/base/system/intel-device-plugin-operator/gpu"
HR_PATH = GPU_APP / "helmrelease.yaml"
CHART_OCI = "oci://ghcr.io/home-operations/charts-mirror/intel-device-plugins-gpu"
CHART_VERSION = "0.34.1"

# Raptor Lake iGPU PCI device ID (hex, as the Intel plugin expects).
IGPU_ALLOW_ID = "0xa7a0"
XE_RESOURCE = "gpu.intel.com/xe"
B70_RESOURCE = "devic.es/b70"

# Controllers that must stay on the shared xe/iGPU pool.
XE_CONSUMERS: dict[str, Path] = {
    "jellyfin": ROOT / "kubernetes/apps/base/media/jellyfin/app/helmrelease.yaml",
    "plex": ROOT / "kubernetes/apps/base/media/plex/app/helmrelease.yaml",
    "playwright": ROOT / "kubernetes/apps/base/selfhosted/rsshub/playwright/helmrelease.yaml",
}

# Controllers that must stay on the discrete B70 identity.
B70_CONSUMERS: dict[str, Path] = {
    "vllm": ROOT / "kubernetes/apps/base/ai/vllm/app/helmrelease.yaml",
    "comfyui": ROOT / "kubernetes/apps/base/ai/comfyui/app/helmrelease.yaml",
    "tdarr-node": ROOT / "kubernetes/apps/base/media/tdarr/app/helmrelease.yaml",
}


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_gpu_helmrelease() -> dict[str, Any]:
    """Load the intel-device-plugin-gpu HelmRelease Flux applies."""
    try:
        built = subprocess.run(
            [
                "kubectl",
                "kustomize",
                str(GPU_APP),
                "--load-restrictor",
                "LoadRestrictionsNone",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        for doc in yaml.safe_load_all(built):
            if (
                isinstance(doc, dict)
                and doc.get("kind") == "HelmRelease"
                and (doc.get("metadata") or {}).get("name") == "intel-device-plugin-gpu"
            ):
                return doc
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    for doc in yaml.safe_load_all(HR_PATH.read_text()):
        if (
            isinstance(doc, dict)
            and doc.get("kind") == "HelmRelease"
            and (doc.get("metadata") or {}).get("name") == "intel-device-plugin-gpu"
        ):
            return doc
    raise Failure(f"could not load HelmRelease from {HR_PATH}")


def helm_template_gpudeviceplugin(values: dict[str, Any]) -> dict[str, Any]:
    """Render the published chart with the HelmRelease values and return the CR."""
    with tempfile.TemporaryDirectory(prefix="igpu-xe-chart-") as tmp:
        tmp_path = Path(tmp)
        pull = subprocess.run(
            [
                "helm",
                "pull",
                CHART_OCI,
                "--version",
                CHART_VERSION,
                "--destination",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        if pull.returncode != 0:
            raise Failure(
                f"helm pull {CHART_OCI}:{CHART_VERSION} failed: {pull.stderr.strip()}"
            )
        charts = list(tmp_path.glob("*.tgz"))
        assert_true(len(charts) == 1, f"expected one chart tgz, got {charts}")

        # Flatten chart values the same way the HelmRelease does (top-level keys).
        set_args: list[str] = []
        for key, val in values.items():
            if isinstance(val, bool):
                rendered = "true" if val else "false"
            else:
                rendered = str(val)
            set_args.extend(["--set", f"{key}={rendered}"])

        templated = subprocess.run(
            ["helm", "template", "igpu-xe-test", str(charts[0]), *set_args],
            capture_output=True,
            text=True,
        )
        if templated.returncode != 0:
            raise Failure(f"helm template failed: {templated.stderr.strip()}")

        crs = [
            doc
            for doc in yaml.safe_load_all(templated.stdout)
            if isinstance(doc, dict) and doc.get("kind") == "GpuDevicePlugin"
        ]
        assert_true(len(crs) == 1, f"expected one GpuDevicePlugin, got {len(crs)}")
        return crs[0]


def walk_resource_maps(obj: Any) -> Iterator[dict[str, Any]]:
    """Yield every mapping nested under a `resources` key (requests/limits)."""
    if isinstance(obj, dict):
        if "resources" in obj and isinstance(obj["resources"], dict):
            yield obj["resources"]
        for value in obj.values():
            yield from walk_resource_maps(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk_resource_maps(item)


def collect_extended_resources(path: Path) -> dict[str, set[str]]:
    """Map controller/container context -> extended resource names requested.

    Returns a flat set of resource names found under any resources.requests or
    resources.limits across every document in the path, plus a per-doc index
    for evidence.
    """
    found: set[str] = set()
    by_block: list[dict[str, Any]] = []
    for doc in yaml.safe_load_all(path.read_text()):
        if not isinstance(doc, dict):
            continue
        values = ((doc.get("spec") or {}).get("values")) or doc
        for resources in walk_resource_maps(values):
            names: set[str] = set()
            for section in ("requests", "limits"):
                block = resources.get(section) or {}
                if not isinstance(block, dict):
                    continue
                for key in block:
                    # Only track device extended resources, not cpu/memory.
                    if "/" in str(key) or str(key).startswith("devic."):
                        names.add(str(key))
                        found.add(str(key))
            if names:
                by_block.append({"path": str(path), "resources": sorted(names)})
    return {"all": found, "blocks": by_block}  # type: ignore[return-value]


def controller_resource_sets(path: Path) -> dict[str, set[str]]:
    """For multi-controller HelmReleases, attribute resources per controller.

    bjw-s app-template shape: values.controllers.<name>.containers.*.resources
    Falls back to a single '_' bucket when the structure is flat.
    """
    result: dict[str, set[str]] = {}
    for doc in yaml.safe_load_all(path.read_text()):
        if not isinstance(doc, dict):
            continue
        values = ((doc.get("spec") or {}).get("values")) or {}
        controllers = values.get("controllers")
        if isinstance(controllers, dict) and controllers:
            for cname, cval in controllers.items():
                names: set[str] = set()
                for resources in walk_resource_maps(cval):
                    for section in ("requests", "limits"):
                        block = resources.get(section) or {}
                        if isinstance(block, dict):
                            for key in block:
                                if "/" in str(key) or str(key).startswith("devic."):
                                    names.add(str(key))
                result[str(cname)] = names
        else:
            names = set()
            for resources in walk_resource_maps(values or doc):
                for section in ("requests", "limits"):
                    block = resources.get(section) or {}
                    if isinstance(block, dict):
                        for key in block:
                            if "/" in str(key) or str(key).startswith("devic."):
                                names.add(str(key))
            result["_"] = names
    return result


def main() -> int:
    evidence: dict[str, Any] = {}
    try:
        hr = load_gpu_helmrelease()
        values = (hr.get("spec") or {}).get("values") or {}
        evidence["helmrelease"] = {
            "name": (hr.get("metadata") or {}).get("name"),
            "values": {
                "name": values.get("name"),
                "sharedDevNum": values.get("sharedDevNum"),
                "allowIDs": values.get("allowIDs"),
                "nodeFeatureRule": values.get("nodeFeatureRule"),
            },
        }

        assert_true(values.get("name") == "xe", f"plugin name must be xe, got {values.get('name')}")
        assert_true(
            values.get("sharedDevNum") == 99,
            f"sharedDevNum must remain 99, got {values.get('sharedDevNum')}",
        )
        assert_true(
            values.get("allowIDs") == IGPU_ALLOW_ID,
            f"allowIDs must be {IGPU_ALLOW_ID!r}, got {values.get('allowIDs')!r}",
        )

        cr = helm_template_gpudeviceplugin(values)
        cr_spec = cr.get("spec") or {}
        evidence["gpuDevicePlugin"] = {
            "apiVersion": cr.get("apiVersion"),
            "kind": cr.get("kind"),
            "metadata.name": (cr.get("metadata") or {}).get("name"),
            "spec.allowIDs": cr_spec.get("allowIDs"),
            "spec.sharedDevNum": cr_spec.get("sharedDevNum"),
            "spec.image": cr_spec.get("image"),
        }
        assert_true(
            cr.get("apiVersion") == "deviceplugin.intel.com/v1",
            f"unexpected apiVersion {cr.get('apiVersion')}",
        )
        assert_true(
            (cr.get("metadata") or {}).get("name") == "xe",
            f"CR name must be xe, got {(cr.get('metadata') or {}).get('name')}",
        )
        assert_true(
            cr_spec.get("allowIDs") == IGPU_ALLOW_ID,
            f"templated CR allowIDs must be {IGPU_ALLOW_ID!r}, got {cr_spec.get('allowIDs')!r}",
        )
        assert_true(
            cr_spec.get("sharedDevNum") == 99,
            f"templated CR sharedDevNum must be 99, got {cr_spec.get('sharedDevNum')}",
        )

        # Exclusive consumer split.
        xe_evidence: dict[str, Any] = {}
        for name, path in XE_CONSUMERS.items():
            assert_true(path.is_file(), f"missing xe consumer manifest: {path}")
            per = controller_resource_sets(path)
            # Union across controllers for the app-level assertion.
            union: set[str] = set()
            for s in per.values():
                union |= s
            xe_evidence[name] = {k: sorted(v) for k, v in per.items()}
            assert_true(
                XE_RESOURCE in union,
                f"{name} must request {XE_RESOURCE}, found {sorted(union)}",
            )
            assert_true(
                B70_RESOURCE not in union,
                f"{name} must NOT request {B70_RESOURCE}, found {sorted(union)}",
            )
        evidence["xeConsumers"] = xe_evidence

        b70_evidence: dict[str, Any] = {}
        for name, path in B70_CONSUMERS.items():
            assert_true(path.is_file(), f"missing b70 consumer manifest: {path}")
            per = controller_resource_sets(path)
            b70_evidence[name] = {k: sorted(v) for k, v in per.items()}
            # tdarr has server + node; only node (or any controller that asks
            # for a GPU) must be b70-only. Controllers with no GPU resources
            # are fine (tdarr server).
            gpu_controllers = {k: v for k, v in per.items() if v}
            assert_true(
                gpu_controllers,
                f"{name} expected at least one controller with GPU resources",
            )
            for cname, resources in gpu_controllers.items():
                assert_true(
                    B70_RESOURCE in resources,
                    f"{name}/{cname} must request {B70_RESOURCE}, found {sorted(resources)}",
                )
                assert_true(
                    XE_RESOURCE not in resources,
                    f"{name}/{cname} must NOT request {XE_RESOURCE}, found {sorted(resources)}",
                )
        evidence["b70Consumers"] = b70_evidence

        # Expected post-reconcile allocatable model (documentation contract).
        # Not live cluster state - records the intended end state the change
        # produces once Flux rolls the plugin.
        evidence["expectedAllocatableAfterReconcile"] = {
            "talos-1": {XE_RESOURCE: 99, B70_RESOURCE: 0},
            "talos-2": {XE_RESOURCE: 99, B70_RESOURCE: 0},
            "talos-3": {XE_RESOURCE: 99, B70_RESOURCE: 99},  # xe was 198 before
        }

        evidence["result"] = "PASS"
        print(json.dumps(evidence, indent=2, sort_keys=True))
        print(
            f"OK: GpuDevicePlugin allowIDs={IGPU_ALLOW_ID}; "
            f"xe consumers {sorted(XE_CONSUMERS)} exclusive of b70; "
            f"b70 consumers {sorted(B70_CONSUMERS)} exclusive of xe",
            file=sys.stderr,
        )
        return 0
    except Failure as exc:
        evidence["result"] = "FAIL"
        evidence["error"] = str(exc)
        print(json.dumps(evidence, indent=2, sort_keys=True))
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
