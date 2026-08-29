#!/usr/bin/env python3
"""Semantic regression test for the talos-3 Arc Pro B70 PCIe runtime-PM mitigation.

docs/hardware-incidents.md [2026-08-24]: the B70's root port (00:01.0) can
runtime-suspend to D3hot before a late-training card finishes link-up. With
HotPlugCapable=0 the port never rediscovers it. The mitigation is the kernel
argument pcie_port_pm=off on the Image Factory schematic (not machine config).

This test does not grep comment text. It:

  1. Renders talos/schematic.yaml.j2 the same way CI/talos recipes do
     (minijinja-cli + .minijinja.toml).
  2. Parses the YAML into a structured object and asserts the active
     extraKernelArgs list carries pcie_port_pm=off plus the load-bearing
     GPU-path args that must stay (pcie_aspm=off, pci=realloc,
     pci=assign-busses).
  3. POSTs the rendered schematic to factory.talos.dev and GETs it back,
     asserting the factory-stored schematic still contains pcie_port_pm=off
     (the real consumer of this file at upgrade-node time).

Offline here we prove the GitOps input upgrade-node will bake into the
installer image. The live activation remains the attended
`just talos upgrade-node talos-3` runbook in docs/hardware-incidents.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCHEMATIC_TEMPLATE = ROOT / "talos/schematic.yaml.j2"
MINIJINJA_CONFIG = ROOT / ".minijinja.toml"
FACTORY_SCHEMATICS = "https://factory.talos.dev/schematics"

# Kernel args the B70 path requires. pcie_port_pm=off is the mitigation under
# test; the rest are load-bearing and must not be dropped while adding it.
REQUIRED_KERNEL_ARGS = (
    "pcie_port_pm=off",
    "pcie_aspm=off",
    "pci=realloc",
    "pci=assign-busses",
)

# factory.talos.dev is the real upgrade-node consumer, but the self-hosted runner
# has seen intermittent TLS handshake timeouts on otherwise-successful POSTs
# (python-tests job on PR #1487). Retry transient transport failures only -
# HTTP 4xx from the factory still fails immediately.
FACTORY_HTTP_ATTEMPTS = 5
FACTORY_HTTP_TIMEOUT_S = 30.0
FACTORY_HTTP_BACKOFF_S = (1.0, 2.0, 4.0, 8.0)


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def render_schematic() -> str:
    """Render schematic.yaml.j2 exactly as the offline CI path does."""
    assert_true(SCHEMATIC_TEMPLATE.is_file(), f"missing {SCHEMATIC_TEMPLATE}")
    assert_true(MINIJINJA_CONFIG.is_file(), f"missing {MINIJINJA_CONFIG}")
    env = os.environ.copy()
    env["MINIJINJA_CONFIG_FILE"] = str(MINIJINJA_CONFIG)
    try:
        proc = subprocess.run(
            ["minijinja-cli", str(SCHEMATIC_TEMPLATE)],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(ROOT),
        )
    except FileNotFoundError as exc:
        raise Failure(
            "minijinja-cli not on PATH (python-tests must install "
            "aqua:mitsuhiko/minijinja; locally: mise install / PATH via mise bin-paths)"
        ) from exc
    if proc.returncode != 0:
        raise Failure(
            f"minijinja-cli failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    assert_true(proc.stdout.strip(), "rendered schematic was empty")
    return proc.stdout


def parse_extra_kernel_args(rendered: str) -> list[str]:
    docs = list(yaml.safe_load_all(rendered))
    assert_true(len(docs) >= 1 and docs[0] is not None, "schematic YAML parsed empty")
    doc: dict[str, Any] = docs[0]
    try:
        args = doc["customization"]["extraKernelArgs"]
    except (KeyError, TypeError) as exc:
        raise Failure(f"schematic missing customization.extraKernelArgs: {exc}") from exc
    assert_true(isinstance(args, list), "extraKernelArgs is not a list")
    assert_true(all(isinstance(a, str) for a in args), "extraKernelArgs must be strings")
    return args


def assert_required_args(args: list[str]) -> None:
    present = set(args)
    missing = [a for a in REQUIRED_KERNEL_ARGS if a not in present]
    assert_true(
        not missing,
        f"extraKernelArgs missing required entries {missing}; have={args}",
    )
    # pcie_port_pm=off must not be the only way a value appears (e.g. a
    # commented sibling cannot satisfy this - yaml parse drops comments).
    assert_true(
        args.count("pcie_port_pm=off") == 1,
        f"expected exactly one pcie_port_pm=off, found {args.count('pcie_port_pm=off')}",
    )


def _is_transient_factory_error(exc: BaseException) -> bool:
    """True for transport/timeouts/5xx that are worth retrying."""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500
    if isinstance(exc, urllib.error.URLError):
        # URLError.reason is often another exception (timeout, SSL, gaierror)
        # or a string; both mean the request never got a useful HTTP answer.
        return True
    # ssl.SSLError and OSError subclasses can surface outside URLError on some
    # Python builds when the handshake aborts mid-request.
    if isinstance(exc, OSError):
        return True
    return False


def _factory_http(label: str, request: urllib.request.Request) -> bytes:
    """GET/POST factory.talos.dev with retries on transient transport failures."""
    attempts = FACTORY_HTTP_ATTEMPTS
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(
                request, timeout=FACTORY_HTTP_TIMEOUT_S
            ) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - classified below
            last_exc = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise Failure(
                    f"factory.talos.dev {label} failed permanently: "
                    f"HTTP {exc.code} {exc.reason}"
                ) from exc
            if not _is_transient_factory_error(exc):
                raise Failure(f"factory.talos.dev {label} failed: {exc}") from exc
            if attempt + 1 >= attempts:
                break
            sleep_s = FACTORY_HTTP_BACKOFF_S[
                min(attempt, len(FACTORY_HTTP_BACKOFF_S) - 1)
            ]
            print(
                f"warn: factory.talos.dev {label} attempt {attempt + 1}/{attempts} "
                f"failed ({exc}); retrying in {sleep_s:.0f}s",
                file=sys.stderr,
            )
            time.sleep(sleep_s)
    raise Failure(
        f"factory.talos.dev {label} failed after {attempts} attempts: {last_exc}"
    ) from last_exc


def factory_roundtrip(rendered: str) -> str:
    """POST schematic to Image Factory and GET the stored body back."""
    post_req = urllib.request.Request(
        FACTORY_SCHEMATICS,
        data=rendered.encode(),
        method="POST",
        headers={"Content-Type": "application/yaml"},
    )
    body = json.loads(_factory_http("POST", post_req).decode())
    schematic_id = body.get("id")
    assert_true(
        isinstance(schematic_id, str) and len(schematic_id) == 64,
        f"factory POST returned unexpected id payload: {body!r}",
    )

    get_req = urllib.request.Request(
        f"{FACTORY_SCHEMATICS}/{schematic_id}",
        method="GET",
    )
    stored = _factory_http(f"GET {schematic_id}", get_req).decode()

    stored_docs = list(yaml.safe_load_all(stored))
    assert_true(stored_docs and stored_docs[0] is not None, "factory GET body empty")
    try:
        stored_args = stored_docs[0]["customization"]["extraKernelArgs"]
    except (KeyError, TypeError) as exc:
        raise Failure(f"factory schematic missing extraKernelArgs: {exc}") from exc
    assert_true(
        "pcie_port_pm=off" in stored_args,
        f"factory-stored schematic {schematic_id} lacks pcie_port_pm=off; args={stored_args}",
    )
    for arg in REQUIRED_KERNEL_ARGS:
        assert_true(
            arg in stored_args,
            f"factory-stored schematic {schematic_id} missing {arg}",
        )
    return schematic_id


def main() -> int:
    try:
        rendered = render_schematic()
        args = parse_extra_kernel_args(rendered)
        assert_required_args(args)
        schematic_id = factory_roundtrip(rendered)
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "OK: schematic renders with pcie_port_pm=off and load-bearing GPU args; "
        f"factory id {schematic_id[:8]}… stores the same contract"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
