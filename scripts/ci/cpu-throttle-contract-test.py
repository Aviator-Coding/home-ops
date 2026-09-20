#!/usr/bin/env python3
"""Guards the 2026-09-20 fleet CPU right-sizing against silent regression.

A CPU limit is an ABSOLUTE CFS quota per 100ms period, not a share of the node.
A container with a 200m limit gets 20ms of CPU per 100ms whether its node is
idle or saturated - so node-level headroom says nothing about whether a
container is being throttled. And unlike a memory limit, a CPU limit throttles
rather than kills: the symptom is slow, late or timing-out work, never an
OOMKill, a restart or a failed probe. Nothing in this repo alerts on it.

That combination is why three real cases ran degraded unnoticed. This gate pins
the two properties that would silently undo the fix, asserting RELATIONSHIPS
rather than frozen literals (AGENTS.md NOTES: a gate that freezes a value blocks
the next legitimate change to it):

  1. The two exec-sweep guard CronJobs must keep a CPU limit at or above the
     measured saturation floor. Written as `>=`, so raising one later is fine
     and only a regression fails. Floor = the measured point at which each
     sweep ran at the same wall-clock speed as with no limit at all.

  2. k8tz must stay Guaranteed QoS. Its CPU was raised 50m -> 250m, and the
     hazard is that a future edit raises (or lowers) only one side: Guaranteed
     requires requests == limits on BOTH cpu and memory for EVERY container,
     and losing it re-opens the 2026-09-06 OOMController kills that the
     requests==limits block was added to stop. Asserted as equality across
     every container k8tz declares, including the cert-watcher sidecar patch -
     never against the number 250m.

Measurement method and evidence live at each declaration site.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

# floor = measured saturation point (wall time equal to running with no CPU
# limit at all). Raising a limit above its floor is always allowed.
SWEEP_FLOORS_MILLICORES: dict[str, int] = {
    "kubernetes/apps/base/system/pvc-writable-check/app/cronjob.yaml": 1000,
    "kubernetes/apps/base/system/pvc-mover-readable-check/app/cronjob.yaml": 1000,
}

K8TZ_HELMRELEASE = "kubernetes/apps/base/system-controller/k8tz/app/helmrelease.yaml"


class Failure(AssertionError):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def parse_cpu(value: Any) -> float:
    """Kubernetes CPU quantity -> millicores."""
    s = str(value).strip()
    if s.endswith("m"):
        return float(s[:-1])
    return float(s) * 1000.0


def parse_mem(value: Any) -> int:
    """Kubernetes memory quantity -> bytes."""
    s = str(value).strip()
    units = {
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
        "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
    }
    for suffix, mult in sorted(units.items(), key=lambda kv: -len(kv[0])):
        if s.endswith(suffix):
            return int(float(s[: -len(suffix)]) * mult)
    return int(float(s))


def load_docs(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [d for d in yaml.safe_load_all(f) if isinstance(d, dict)]


def test_sweep_cronjobs_keep_cpu_limit_above_measured_floor(
    floors: dict[str, int] | None = None,
) -> None:
    floors = SWEEP_FLOORS_MILLICORES if floors is None else floors
    for rel, floor in floors.items():
        path = ROOT / rel
        require(path.is_file(), f"{rel} is missing - update this gate if the app was retired")
        docs = load_docs(path)
        cronjobs = [d for d in docs if d.get("kind") == "CronJob"]
        require(len(cronjobs) == 1, f"{rel}: expected exactly one CronJob, got {len(cronjobs)}")
        containers = cronjobs[0]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
        require(len(containers) >= 1, f"{rel}: CronJob declares no containers")
        for c in containers:
            limits = (c.get("resources") or {}).get("limits") or {}
            require(
                "cpu" in limits,
                f"{rel}: container {c['name']} declares no cpu limit - this sweep is a "
                f"completion-deadline guard and an absent limit is not the fix for an "
                f"over-tight one; state the intended value",
            )
            got = parse_cpu(limits["cpu"])
            require(
                got >= floor,
                f"{rel}: container {c['name']} cpu limit {limits['cpu']} is below the "
                f"measured saturation floor of {floor}m. Below this the sweep is "
                f"CFS-throttled and runs several times slower with NO alert, NO restart "
                f"and NO failed probe - measured 89.1% of periods throttled at 200m. "
                f"Re-measure with the one-off-Job probe described at the declaration "
                f"before lowering it.",
            )


def _k8tz_resource_blocks(path: Path) -> list[tuple[str, dict[str, Any]]]:
    """Every resources block k8tz declares: chart values plus each kustomize patch."""
    hr = load_docs(path)[0]
    values = hr["spec"]["values"]
    blocks: list[tuple[str, dict[str, Any]]] = []

    if isinstance(values.get("resources"), dict):
        blocks.append(("values.resources (webhook container)", values["resources"]))

    def walk(node: Any, trail: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "resources" and isinstance(v, dict) and ("requests" in v or "limits" in v):
                    blocks.append((f"{trail}.{k}", v))
                else:
                    walk(v, f"{trail}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{trail}[{i}]")

    for i, patch in enumerate(hr.get("spec", {}).get("postRenderers", []) or []):
        walk(patch, f"postRenderers[{i}]")
    for i, p in enumerate(values.get("kustomize", {}).get("patches", []) or []):
        walk(p, f"values.kustomize.patches[{i}]")

    # kustomize patches are embedded as YAML strings; parse those too
    for i, patch in enumerate(_embedded_patches(hr)):
        walk(patch, f"embedded-patch[{i}]")
    return blocks


def _embedded_patches(hr: dict[str, Any]) -> list[Any]:
    out: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "patch" and isinstance(v, str):
                    try:
                        parsed = yaml.safe_load(v)
                    except yaml.YAMLError:
                        continue
                    if isinstance(parsed, dict):
                        out.append(parsed)
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(hr)
    return out


def test_k8tz_stays_guaranteed_qos(path: Path | None = None) -> None:
    path = (ROOT / K8TZ_HELMRELEASE) if path is None else path
    require(path.is_file(), f"{K8TZ_HELMRELEASE} is missing - update this gate if k8tz was retired")
    blocks = _k8tz_resource_blocks(path)
    require(
        len(blocks) >= 2,
        f"expected at least 2 k8tz resources blocks (webhook + cert-watcher sidecar), "
        f"found {len(blocks)}: {[b[0] for b in blocks]} - if the chart changed, this gate "
        f"must be updated rather than the assertion relaxed",
    )
    for where, res in blocks:
        req, lim = res.get("requests") or {}, res.get("limits") or {}
        for field, parse in (("cpu", parse_cpu), ("memory", parse_mem)):
            require(
                field in req and field in lim,
                f"k8tz {where}: Guaranteed QoS needs both requests.{field} and "
                f"limits.{field}; got requests={req} limits={lim}",
            )
            require(
                parse(req[field]) == parse(lim[field]),
                f"k8tz {where}: requests.{field}={req[field]} != limits.{field}={lim[field]}. "
                f"That silently demotes the pod from Guaranteed to Burstable, which the "
                f"Talos runtime.OOMController then ranks as a kill candidate - the exact "
                f"failure that SIGKILLed it 9 times in 33h on 2026-09-06. Raise or lower "
                f"BOTH sides together.",
            )


def main() -> int:
    passed = failures = 0
    for name, fn in (
        ("sweep_cronjobs_keep_cpu_limit_above_measured_floor",
         test_sweep_cronjobs_keep_cpu_limit_above_measured_floor),
        ("k8tz_stays_guaranteed_qos", test_k8tz_stays_guaranteed_qos),
    ):
        try:
            fn()
        except Failure as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
        else:
            passed += 1
            print(f"ok   {name}")
    print(f"\n{passed} passed, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
