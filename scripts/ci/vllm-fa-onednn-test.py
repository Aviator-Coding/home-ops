#!/usr/bin/env python3
"""Behavioral contract: ai/vllm must keep llama.cpp's oneDNN SDPA path OFF.

Pins the 2026-09-22 fix for the memory growth that came back after PR #1731
(`--cache-ram 4096` + the glibc mmap_threshold pin), with both of those halves
live and working. Full evidence: docs/ai/vllm-onednn-sdpa-leak.md.

The cause is in llama.cpp's SYCL backend, not in the server's caches.
`ggml/src/ggml-sycl/fattn-onednn.cpp` compiles one oneDNN Graph SDPA partition
per (ubatch query length, current KV length) and keeps it in a function-static
`std::unordered_map` that is never evicted. With this server's q8_0 KV cache,
every prefill ubatch of >= 32 tokens over n_kv >= 1024 takes that path (enabled
for q8_0 by upstream #25874 on 2026-08-04 - b9592 predates it, b10820 has it).
Distinct shapes are effectively unbounded, so each new prompt shape pins another
compiled kernel set for the life of the process. Measured live: ~1 GiB of
retained anonymous memory per million prompt tokens, ~+3.5 GiB/day at real
traffic, as thousands of fixed-size 0.4-8 MiB mappings, and still climbing toward
the 48Gi limit. Upstream master is still unbounded as of 2026-09-22.

`GGML_SYCL_FA_ONEDNN=0` routes long prefills to the MKL XMX flash-attention
path instead (`GGML_SYCL_ENABLE_MKL_FA`, default on), which keeps no per-shape
cache. That costs prefill speed; upstream measured this exact model and KV type
on a B70 at 32K at -30% (1184 -> 834 t/s). The captain accepted that trade on
2026-09-22.

Why this needs a gate rather than a comment: the leak is SILENT. Dropping the
env var, or setting it to a value ggml cannot parse, starts the pod normally,
serves normally, and leaks for days. ggml reads the variable with
`sscanf(" %u")` (`ggml_sycl_get_env`, ggml/src/ggml-sycl/common.cpp), so
"false", "off" and "no" do NOT parse and silently keep the default of 1 (ON).
The gate therefore accepts only values that sscanf would read as 0.

What this catches
  - GGML_SYCL_FA_ONEDNN removed from the vllm container env
  - the name misspelled (the real key is then absent)
  - any value that enables the path ("1", "2", ...)
  - a value that reads as "off" to a human but not to sscanf
    ("false", "off", "no", "", "disabled") - all of which leave oneDNN ON
  - the env var moved to the replicas: 0 vllm-embed controller instead of
    the live chat server

What this does not catch
  - llama.cpp renaming or dropping the variable in a future image. Then the
    pin is inert and the leak returns while this gate stays green. The runtime
    detector for that is the VLLMMemoryRetainedAboveBound alert
    (prometheusrule.yaml). Before bumping the image, check the new tag's
    ggml/src/ggml-sycl/ggml-sycl.cpp still reads GGML_SYCL_FA_ONEDNN.
  - whether the prefill regression at this server's real depths is acceptable.
    That is measured post-merge from the server log (the method and the
    pre-merge baseline are in docs/ai/vllm-onednn-sdpa-leak.md).

Lifting it: only once the pinned llama.cpp tag bounds that partition cache
(an LRU or capacity limit on the `cache` map in fattn-onednn.cpp, or keys that
no longer include the KV length). Verify that in the tag's source, then delete
the env var together with this file's REQUIRED check, in one reviewed change.
Then confirm post-deploy that the working set stays flat under traffic.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/ai/vllm/app"
HR_PATH = APP / "helmrelease.yaml"
ENV_NAME = "GGML_SYCL_FA_ONEDNN"


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def _docs() -> list[dict[str, Any]]:
    """Render the app the way Flux builds it, falling back to the raw file."""
    try:
        built = subprocess.run(
            ["kubectl", "kustomize", str(APP), "--load-restrictor", "LoadRestrictionsNone"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        docs = [d for d in yaml.safe_load_all(built) if isinstance(d, dict)]
        if docs:
            return docs
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return [d for d in yaml.safe_load_all(HR_PATH.read_text()) if isinstance(d, dict)]


def _helmrelease(docs: list[dict[str, Any]]) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == "HelmRelease" and (doc.get("metadata") or {}).get("name") == "vllm":
            return doc
    raise Failure("could not load the vllm HelmRelease")


def _env(container: dict[str, Any]) -> dict[str, Any]:
    env = container.get("env") or {}
    if isinstance(env, list):  # tolerate the k8s-native list shape
        env = {e.get("name"): e.get("value") for e in env if isinstance(e, dict)}
    return env


def sscanf_u(value: Any) -> int | None:
    """Mirror ggml_sycl_get_env: sscanf(value, " %u", &n) == 1 ? n : default.

    Returns the parsed number, or None when sscanf would fail and ggml would
    fall back to the variable's default (1, i.e. the leaking path ON). `%u`
    skips leading whitespace, accepts an optional sign and then needs at least
    one digit; anything after the digits is ignored.
    """
    if value is None:
        return None
    match = re.match(r"\s*([+-]?)(\d+)", str(value))
    if match is None:
        return None
    number = int(match.group(2))
    return -number if match.group(1) == "-" else number


def test_chat_server_disables_onednn_sdpa(docs: list[dict[str, Any]]) -> None:
    """The live vllm controller's container must set GGML_SYCL_FA_ONEDNN to 0."""
    hr = _helmrelease(docs)
    controllers = hr["spec"]["values"]["controllers"]
    container = controllers["vllm"]["containers"]["app"]
    env = _env(container)

    assert_true(
        ENV_NAME in env,
        f"ai/vllm must set {ENV_NAME}=\"0\". Without it llama.cpp's SYCL backend "
        "routes every q8_0 prefill ubatch of >= 32 tokens through oneDNN SDPA, "
        "whose compiled-partition cache (fattn-onednn.cpp, a function-static "
        "unordered_map keyed on query length AND KV length) is never evicted. "
        "Measured 2026-09-22: ~1 GiB retained per million prompt tokens, "
        "~+3.5 GiB/day, silently, toward an OOMKill. "
        "See docs/ai/vllm-onednn-sdpa-leak.md.",
    )

    raw = env[ENV_NAME]
    parsed = sscanf_u(raw)
    assert_true(
        parsed is not None,
        f"{ENV_NAME}={raw!r} does not parse as a number. ggml reads it with "
        "sscanf(\" %u\"), so this value is IGNORED and the default (1, oneDNN "
        "SDPA ON, leaking) stays in force. Use \"0\".",
    )
    assert_true(
        parsed == 0,
        f"{ENV_NAME}={raw!r} parses to {parsed}, which ENABLES the oneDNN SDPA "
        "path and its unbounded partition cache. It must read as 0. Re-enable "
        "only once the pinned llama.cpp tag bounds that cache - see this file's "
        "docstring for the lift procedure.",
    )


def test_sscanf_mirror_matches_ggml(docs: list[dict[str, Any]]) -> None:
    """The parser above must agree with ggml's sscanf(" %u") on the traps."""
    del docs
    cases = {
        "0": 0,
        " 0": 0,
        "00": 0,
        "0 # off": 0,
        "1": 1,
        "2": 2,
        "false": None,
        "off": None,
        "no": None,
        "": None,
        "disabled": None,
    }
    for value, expected in cases.items():
        got = sscanf_u(value)
        assert_true(
            got == expected,
            f"sscanf mirror disagrees with ggml on {value!r}: got {got}, expected {expected}",
        )


def main() -> int:
    docs = _docs()
    tests = [
        test_chat_server_disables_onednn_sdpa,
        test_sscanf_mirror_matches_ggml,
    ]
    failed = 0
    for test in tests:
        try:
            test(docs)
        except Failure as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
