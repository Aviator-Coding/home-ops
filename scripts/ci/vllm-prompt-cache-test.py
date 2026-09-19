#!/usr/bin/env python3
"""Behavioral contract for the ai/vllm host prompt-cache bound.

Pins the 2026-09-19 fix for a live memory leak. `ai/vllm` carried NO
`--cache-ram` at all, so it inherited llama.cpp's 8192 MiB host prompt-cache
default. Its host working set climbed monotonically from a 1232 MiB post-load
baseline at +5622..+6485 MiB/day and never plateaued: the previous pod
generation reached 39531 MiB over 6.6 days and was rescued only by an unrelated
talos-3 reboot, and the pod that replaced it was at 26760 MiB and still climbing
when this fix shipped - on track to cross its 39Gi request ~2026-09-21 and
OOMKill at its 48Gi limit ~2026-09-23. That takes down the captain's
interactive chat model and the engine `ai/hermes` depends on.

Why this needs a gate rather than a comment: `--cache-ram`'s absence is SILENT.
Removing or mistyping the flag restores 8192 MiB with no error, no warning and
no CI signal - the pod starts fine and leaks for days. The `bad_alloc` guard
inside `server_prompt_cache::alloc` that would otherwise self-limit is
structurally unreachable under a cgroup limit, because the kernel SIGKILLs at
page-fault time and malloc never fails. `limits.memory` is therefore NOT a
backstop here, which is why this file never asserts that it is one.

Two halves, both pinned, because shipping half the fix is the expected mistake:

  1. `--cache-ram` bounded and NON-ZERO. This is a CHAT server where prefix
     reuse is real - mean f_keep = 0.975 across 1560 slot selections, i.e. 97.5%
     of prompt KV reused, against a workload that is 88% prefill tokens. The
     sibling `ai/embedding-gpu` correctly pins `--cache-ram 0` because ITS cache
     is write-only (the read path is gated on SERVER_TASK_TYPE_COMPLETION), and
     copying that value here would trade an OOM for a large permanent prefill
     regression. AGENTS.md states this directly: vllm "must not get the same
     fix... it needs a bounded non-zero value".
  2. `GLIBC_TUNABLES=glibc.malloc.mmap_threshold=` (or the older
     `MALLOC_MMAP_THRESHOLD_`) pinning the threshold low. The process overshot
     its own declared 8192 MiB bound by 4.7x into a single brk()-grown `[heap]`,
     because glibc's main arena trims only from the top and its dynamic
     mmap_threshold rises to 32 MiB after the first mmap'd free - after which
     the per-layer KV chunks come from the heap and are never returned to the
     OS. So bounding the cache logically need not shrink RSS at all, and a
     smaller cache churns MORE often. A future tuning pass may legitimately
     change the `--cache-ram` value; what it may never do is drop this env and
     assume the cache bound alone holds.

This asserts RELATIONSHIPS and SHAPE, not literals, deliberately: per AGENTS.md,
a gate that freezes a past PR's exact value blocks the next legitimate change to
the same field (`falkordb-lan-browser-access-test.py` did exactly that). So the
cache value is checked as "present, integer, > 0, below the container limit",
and the tunable as "present, threshold set, and small enough to actually route
the large churning allocations through mmap" - neither pins the number shipped.

What this catches
  - `--cache-ram` removed from the vllm args (restores the 8192 MiB default)
  - `--cache-ram 0`, i.e. the embedding-gpu value copied onto the chat server
  - `--cache-ram -1` ("no limit"), or a value >= the container memory limit,
    either of which leaves the unreachable cgroup as the only bound
  - the allocator tunable dropped, misspelled, or raised back into the range
    where the dynamic threshold defeats the fix - i.e. shipping half the fix
  - `limits.memory` removed, which would make the cache bound uncheckable

What this does not catch
  - whether 4096 MiB is empirically the right size under real chat traffic.
    Only a live run proves that: the stated success signal is the host working
    set PLATEAUING instead of climbing, measured over ~24h (the climb is
    ~6.5 GiB/day, and the pod starts at ~1.2 GiB either way, so a reading taken
    minutes after a roll cannot distinguish a fix from a failure). The stated
    failure signal is falling prefill tokens/sec and rising `prompt eval time`
    in the server log, which is immediate and unambiguous.
  - whether the request/limit are right-sized afterwards. They are deliberately
    left at their pre-fix values here; that is a follow-up needing a measured
    steady state (docs/talos-3-scheduling-truth.md).
  - any OTHER host-memory growth path in llama.cpp.
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

# glibc raises its dynamic mmap_threshold up to 32 MiB (DEFAULT_MMAP_THRESHOLD_MAX
# on 64-bit) once an mmap'd block is freed. Any explicitly pinned value disables
# that raise, but the pin only helps if it sits below the allocation sizes that
# actually churn. The observed prompt-cache entries are 191..1886 MiB and their
# per-layer KV chunks are sub-32 MiB, so require a pin comfortably under 32 MiB.
MAX_USEFUL_MMAP_THRESHOLD = 32 * 1024 * 1024


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


def _container(hr: dict[str, Any]) -> dict[str, Any]:
    """The chat server container - NOT vllm-embed, which is replicas: 0."""
    ctrl = hr["spec"]["values"]["controllers"]["vllm"]
    return ctrl["containers"]["app"]


def _mib(quantity: str) -> int:
    """Parse a Kubernetes memory quantity into MiB."""
    text = str(quantity).strip()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([EPTGMK]i?|m)?", text)
    assert_true(match is not None, f"unparseable memory quantity {quantity!r}")
    assert match is not None
    value = float(match.group(1))
    unit = match.group(2) or ""
    factors = {
        "": 1 / 1024**2, "m": 1 / (1000 * 1024**2),
        "Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024**2, "Pi": 1024**3, "Ei": 1024**4,
        "K": 1000 / 1024**2, "M": 1000**2 / 1024**2, "G": 1000**3 / 1024**2,
        "T": 1000**4 / 1024**2, "P": 1000**5 / 1024**2, "E": 1000**6 / 1024**2,
    }
    assert_true(unit in factors, f"unknown memory unit in {quantity!r}")
    return int(value * factors[unit])


def test_cache_ram_is_bounded_non_zero_and_below_the_limit(docs: list[dict[str, Any]]) -> None:
    """--cache-ram must be present, > 0, and strictly below limits.memory."""
    container = _container(_helmrelease(docs))
    args = [str(a) for a in container.get("args", [])]

    assert_true(
        "--cache-ram" in args or "-cram" in args,
        "ai/vllm args must set --cache-ram. Omitting it SILENTLY restores "
        "llama.cpp's 8192 MiB default host prompt cache - no error, no warning, "
        "no other CI signal - which is the leak that put this server on track to "
        "OOMKill at its 48Gi limit (~6485 MiB/day from a 1232 MiB baseline). The "
        "cgroup limit is not a backstop: llama.cpp's bad_alloc guard is "
        "structurally unreachable under one, because the kernel SIGKILLs at "
        "page-fault time and malloc never fails.",
    )

    flag = "--cache-ram" if "--cache-ram" in args else "-cram"
    idx = args.index(flag)
    assert_true(idx + 1 < len(args), f"{flag} is present but has no value")
    raw = args[idx + 1]
    assert_true(
        re.fullmatch(r"-?\d+", raw) is not None,
        f"{flag} value {raw!r} is not an integer number of MiB",
    )
    cache_mib = int(raw)

    assert_true(
        cache_mib > 0,
        f"{flag} is {cache_mib}. ai/vllm needs a bounded NON-ZERO cache: a "
        "negative value means 'no limit' to llama.cpp, and 0 disables the cache "
        "entirely - that is ai/embedding-gpu's correct value (its cache is "
        "write-only) and the wrong one here. This is a chat server with mean "
        "f_keep = 0.975 across 1560 slot selections on a workload that is 88% "
        "prefill tokens, so 0 trades an OOM for a large permanent prefill "
        "regression. AGENTS.md: vllm 'must not get the same fix'.",
    )

    limits = (container.get("resources") or {}).get("limits") or {}
    assert_true(
        "memory" in limits,
        "ai/vllm must declare limits.memory - without it the prompt-cache bound "
        "cannot be checked against anything.",
    )
    limit_mib = _mib(limits["memory"])

    assert_true(
        cache_mib < limit_mib,
        f"{flag}={cache_mib} MiB is not below limits.memory ({limit_mib} MiB). A "
        "cache ceiling at or above the container limit leaves the cgroup OOM "
        "killer as the only bound, and that bound is unreachable-by-design here.",
    )


def test_allocator_tunable_pins_the_mmap_threshold(docs: list[dict[str, Any]]) -> None:
    """The second half of the fix: large frees must go back to the OS."""
    container = _container(_helmrelease(docs))
    env = container.get("env") or {}
    if isinstance(env, list):  # tolerate the k8s-native list shape
        env = {e.get("name"): e.get("value") for e in env if isinstance(e, dict)}

    tunables = env.get("GLIBC_TUNABLES")
    legacy = env.get("MALLOC_MMAP_THRESHOLD_")

    assert_true(
        tunables is not None or legacy is not None,
        "ai/vllm must set GLIBC_TUNABLES=glibc.malloc.mmap_threshold=<bytes> (or "
        "MALLOC_MMAP_THRESHOLD_). This is the SECOND HALF of the prompt-cache fix "
        "and carries comparable weight - do not ship one without the other. "
        "--cache-ram bounds the cache LOGICALLY but need not shrink RSS: the "
        "process overshot its declared 8192 MiB bound 4.7x into a single "
        "brk()-grown [heap], because glibc trims the main arena only from the top "
        "and raises its dynamic mmap_threshold to 32 MiB after the first mmap'd "
        "free, after which the churning KV chunks never return to the OS.",
    )

    if legacy is not None and tunables is None:
        raw = str(legacy)
        assert_true(
            re.fullmatch(r"\d+", raw) is not None,
            f"MALLOC_MMAP_THRESHOLD_={raw!r} is not an integer number of bytes",
        )
        threshold = int(raw)
    else:
        raw_tunables = str(tunables)
        match = re.search(r"glibc\.malloc\.mmap_threshold=(\d+)", raw_tunables)
        assert_true(
            match is not None,
            f"GLIBC_TUNABLES={raw_tunables!r} does not set "
            "glibc.malloc.mmap_threshold=<bytes>. Without that specific tunable "
            "the dynamic threshold still rises to 32 MiB and the fix is inert.",
        )
        assert match is not None
        threshold = int(match.group(1))

    assert_true(
        0 < threshold <= MAX_USEFUL_MMAP_THRESHOLD,
        f"mmap_threshold is {threshold} bytes. It must be > 0 and at most "
        f"{MAX_USEFUL_MMAP_THRESHOLD} bytes (32 MiB, glibc's dynamic ceiling): a "
        "pin at or above that ceiling changes nothing, because the sub-32 MiB "
        "per-layer KV chunks that compose a prompt-cache entry would still be "
        "served from the unreturnable heap.",
    )


def main() -> int:
    docs = _docs()
    tests = [
        test_cache_ram_is_bounded_non_zero_and_below_the_limit,
        test_allocator_tunable_pins_the_mmap_threshold,
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
