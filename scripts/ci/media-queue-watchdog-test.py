#!/usr/bin/env python3
"""Contract test for the media-queue-watchdog CronJob.

Invariant: no Radarr/Sonarr/Lidarr queue item may sit for more than 24 hours
without the watchdog Job failing, which feeds the existing KubeJobFailed alert.

  1. ``stale_ids`` flags items strictly older than 24h and ignores fresh ones.
  2. ``run`` exits 0 when every queue is healthy, 1 when any item is stale, and
     2 when a queue cannot be read - an unreadable queue must never look green.
  3. API keys never reach stdout/stderr, including on failure.
  4. The GitOps wiring is hardened (non-root, read-only rootfs, dropped caps,
     keys only through an ExternalSecret) and registered in Flux.

The script is loaded by path and fed synthetic queue payloads with an injected
clock, so the test needs no cluster and no network.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/downloads/queue-watchdog/app"
SCRIPT = APP / "watchdog.py"
WRAPPER = ROOT / "kubernetes/apps/main/downloads/queue-watchdog.yaml"
MAIN_KUSTOMIZATION = ROOT / "kubernetes/apps/main/downloads/kustomization.yaml"

NOW = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)
SECRET_KEY = "synthetic-key-0123456789abcdef"


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def iso(delta_hours: float) -> str:
    return (NOW - timedelta(hours=delta_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(item_id: int, age_hours: float) -> dict[str, Any]:
    return {"id": item_id, "added": iso(age_hours), "status": "downloading"}


def load_module() -> Any:
    assert_true(SCRIPT.is_file(), f"missing {SCRIPT.relative_to(ROOT)}")
    spec = importlib.util.spec_from_file_location("watchdog", SCRIPT)
    assert_true(spec is not None and spec.loader is not None, "cannot load watchdog.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def services(module: Any) -> list[Any]:
    return [
        module.Service(name, f"http://{name}.invalid", prefix, SECRET_KEY)
        for name, prefix in (("radarr", "v3"), ("sonarr", "v3"), ("lidarr", "v1"))
    ]


def run_captured(module: Any, fetch: Any) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = module.run(services(module), NOW, fetch)
    return code, out.getvalue()


def test_stale_detection(module: Any) -> None:
    stale = module.stale_ids([record(1, 25), record(2, 2), record(3, 24.01)], NOW)
    assert_true(stale == [1, 3], f"expected [1, 3], got {stale}")
    assert_true(module.stale_ids([record(4, 24)], NOW) == [], "exactly 24h must not be stale")
    assert_true(module.stale_ids([], NOW) == [], "empty queue must have no stale items")
    dotnet = {"id": 9, "added": "2026-10-16T12:00:00.1234567Z"}
    assert_true(module.stale_ids([dotnet], NOW) == [9], "7-digit fractional seconds must parse")


def test_exit_codes(module: Any) -> None:
    healthy, _ = run_captured(module, lambda svc: [record(1, 1)])
    assert_true(healthy == 0, f"fresh queue should exit 0, got {healthy}")

    empty, _ = run_captured(module, lambda svc: [])
    assert_true(empty == 0, f"empty queue should exit 0, got {empty}")

    def one_stale(svc: Any) -> list[dict[str, Any]]:
        return [record(7, 30)] if svc.name == "sonarr" else [record(1, 1)]

    stale, out = run_captured(module, one_stale)
    assert_true(stale == 1, f"stale item should exit 1, got {stale}")
    assert_true("sonarr" in out and "7" in out, "output must name the service and item id")

    def broken(svc: Any) -> list[dict[str, Any]]:
        raise OSError("connection refused")

    error, _ = run_captured(module, broken)
    assert_true(error == 2, f"unreadable queue should exit 2, got {error}")


def test_secret_never_printed(module: Any) -> None:
    def leaky(svc: Any) -> list[dict[str, Any]]:
        raise OSError(f"GET http://x/api?apikey={svc.api_key} failed")

    _, out = run_captured(module, leaky)
    assert_true(SECRET_KEY not in out, "API key leaked into watchdog output")
    _, out = run_captured(module, lambda svc: [record(1, 99)])
    assert_true(SECRET_KEY not in out, "API key leaked into stale-item output")


def load_yaml(path: Path) -> list[dict[str, Any]]:
    assert_true(path.is_file(), f"missing {path.relative_to(ROOT)}")
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def test_manifests() -> None:
    release = load_yaml(APP / "helmrelease.yaml")[0]
    controller = release["spec"]["values"]["controllers"]["queue-watchdog"]
    assert_true(controller["type"] == "cronjob", "watchdog must be a cronjob controller")
    cron = controller["cronjob"]
    assert_true(cron["concurrencyPolicy"] == "Forbid", "concurrencyPolicy must be Forbid")
    assert_true(cron.get("successfulJobsHistory", 0) >= 1, "keep one success so KubeJobFailed unlatches")
    assert_true(cron.get("ttlSecondsAfterFinished", 0) > 0, "failed Jobs must age out")
    # A pod stuck pending (e.g. missing secret) neither fails nor completes; without a
    # deadline Forbid would skip every later run and KubeJobFailed would never fire.
    deadline = cron.get("activeDeadlineSeconds", 0)
    assert_true(0 < deadline <= 1800, f"activeDeadlineSeconds must be set and <= 1800, got {deadline}")

    pod_ctx = controller["pod"]["securityContext"]
    assert_true(pod_ctx["runAsNonRoot"] is True, "pod must run as non-root")
    container = controller["containers"]["app"]
    ctx = container["securityContext"]
    assert_true(ctx["readOnlyRootFilesystem"] is True, "rootfs must be read-only")
    assert_true(ctx["allowPrivilegeEscalation"] is False, "privilege escalation must be off")
    assert_true(ctx["capabilities"]["drop"] == ["ALL"], "all capabilities must be dropped")
    assert_true(container["image"]["tag"] != "latest", "image tag must be pinned")

    secret = load_yaml(APP / "externalsecret.yaml")[0]
    assert_true(secret["spec"]["secretStoreRef"]["name"] == "onepassword", "keys must come from 1Password")
    keys = {d["secretKey"] for d in secret["spec"]["data"]}
    assert_true(
        {"RADARR_API_KEY", "SONARR_API_KEY", "LIDARR_API_KEY"} <= keys,
        f"ExternalSecret must expose all three keys, got {sorted(keys)}",
    )

    wrapper = load_yaml(WRAPPER)[0]
    deps = {d["name"] for d in wrapper["spec"]["dependsOn"]}
    assert_true("onepassword-store" in deps, "Flux wrapper must depend on onepassword-store")

    registered = load_yaml(MAIN_KUSTOMIZATION)[0]["resources"]
    assert_true("./queue-watchdog.yaml" in registered, "queue-watchdog.yaml not registered in main/downloads")


def main() -> int:
    module = load_module()
    for check in (test_stale_detection, test_exit_codes, test_secret_never_printed):
        check(module)
    test_manifests()
    print("media-queue-watchdog-test: ok")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
