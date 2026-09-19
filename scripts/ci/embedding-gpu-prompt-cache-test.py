#!/usr/bin/env python3
"""Behavioral contract for the ai/embedding-gpu host prompt-cache bound.

Pins the 2026-09-16 (second) OOM fix. `ai/embedding-gpu` was OOMKilled 18 times
against a 2Gi limit, then 5 more times in 30 minutes against the raised 4Gi one.
Raising the ceiling was never going to work: llama.cpp keeps a host-RAM prompt
cache whose DEFAULT is 8192 MiB (`-cram, --cache-ram N ... (default: 8192, -1 -
no limit, 0 - disable)`, read off the pinned image's own --help), so the cache's
own pruning cannot engage before the cgroup kills the process at any limit that
fits on talos-3.

On an embedding server that cache is WRITE-ONLY and therefore pure waste: the
single read path (`prompt_load`, tools/server/server-context.cpp:1649) sits in a
block gated on `task.type == SERVER_TASK_TYPE_COMPLETION`, while the
`--cache-idle-slots` write path (same file, :2420) carries no task-type guard and
fires on every task. Each entry costs the document's full KV state - measured
112 KiB/token (56.00 MiB KV / 512 cells) on this pinned digest - so a ~31-token
document costs ~3.4 MiB of host RAM and ~1200 documents fill 4Gi.

Why this needs a gate rather than a comment: dropping the flag restores the
8192 MiB default SILENTLY. There is no error, no warning, and no CI signal -
the pod simply starts fine and dies minutes into the next backfill. The
`bad_alloc` guard inside `server_prompt_cache::alloc` that would otherwise
self-limit is structurally unreachable under a cgroup limit, because the kernel
SIGKILLs at page-fault time and malloc never fails.

This asserts a RELATIONSHIP, not a literal, deliberately: per AGENTS.md, a gate
that freezes a past PR's exact value blocks the next legitimate change to the
same field. A future tuning pass may legitimately want a small non-zero cache;
what it may never do is leave the cache unbounded relative to the container.

What this catches
  - `--cache-ram` removed from the embedding-gpu args (restores the 8192 default)
  - `--cache-ram` set to -1 ("no limit") or to a value >= the container limit,
    either of which leaves the cgroup, not the cache, as the only bound
  - `limits.memory` removed, which would make the bound unverifiable
  - the embedder losing the lowest-priority / non-preempting placement that is
    why 23 embedder deaths have cost ai/vllm and ai/hermes nothing

What this does not catch
  - whether the chosen value is empirically sufficient under a real backfill
    (only a live sustained run proves that - see the PR body's stated prediction)
  - any OTHER host-memory growth path in llama.cpp
  - ai/vllm's own exposure to the same 8192 default. That is a CHAT server where
    prefix reuse is genuinely valuable, so its answer is a bounded non-zero
    value, not this one. It was still unset when this file was written and did
    leak, exactly as predicted - fixed 2026-09-19 and now gated by its own
    sibling, scripts/ci/vllm-prompt-cache-test.py. Do not copy THIS file's
    `--cache-ram 0` onto vllm; that gate refuses it.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/ai/embedding-gpu/app"
HR_PATH = APP / "helmrelease.yaml"
PC_PATH = APP / "priorityclass.yaml"

PRIORITY_CLASS = "embedding-gpu-low"


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
    docs = []
    for path in (HR_PATH, PC_PATH):
        docs.extend(d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict))
    return docs


def _helmrelease(docs: list[dict[str, Any]]) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == "HelmRelease" and (doc.get("metadata") or {}).get("name") == "embedding-gpu":
            return doc
    raise Failure("could not load the embedding-gpu HelmRelease")


def _container(hr: dict[str, Any]) -> dict[str, Any]:
    ctrl = hr["spec"]["values"]["controllers"]["embedding-gpu"]
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


def test_cache_ram_is_bounded_below_the_container_limit(docs: list[dict[str, Any]]) -> None:
    """--cache-ram must be present and bounded strictly below limits.memory."""
    container = _container(_helmrelease(docs))
    args = [str(a) for a in container.get("args", [])]

    assert_true(
        "--cache-ram" in args or "-cram" in args,
        "ai/embedding-gpu args must set --cache-ram. Omitting it silently restores "
        "llama.cpp's 8192 MiB default host prompt cache, which exceeds the container "
        "memory limit and OOMKills the pod minutes into a backfill with no warning.",
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
        cache_mib >= 0,
        f"{flag} is {cache_mib} - a negative value means 'no limit' to llama.cpp, "
        "which is exactly the unbounded growth this pins against.",
    )

    limits = (container.get("resources") or {}).get("limits") or {}
    assert_true(
        "memory" in limits,
        "ai/embedding-gpu must declare limits.memory - without it the prompt-cache "
        "bound cannot be checked against anything.",
    )
    limit_mib = _mib(limits["memory"])

    if cache_mib > 0:
        assert_true(
            cache_mib < limit_mib,
            f"{flag}={cache_mib} MiB is not below limits.memory ({limit_mib} MiB). "
            "A cache ceiling at or above the container limit leaves the cgroup OOM "
            "killer as the only bound, which is the original bug.",
        )


def test_requests_and_priority_keep_the_llm_safe(docs: list[dict[str, Any]]) -> None:
    """The embedder must stay the first thing yielded, never a preemptor."""
    hr = _helmrelease(docs)
    ctrl = hr["spec"]["values"]["controllers"]["embedding-gpu"]
    container = _container(hr)

    requests = (container.get("resources") or {}).get("requests") or {}
    assert_true(
        "memory" in requests,
        "ai/embedding-gpu must declare requests.memory - it drives talos-3's "
        "scheduling arithmetic (docs/talos-3-scheduling-truth.md).",
    )

    assert_true(
        (ctrl.get("pod") or {}).get("priorityClassName") == PRIORITY_CLASS,
        f"ai/embedding-gpu must stay on priorityClassName {PRIORITY_CLASS!r}.",
    )

    pcs = [d for d in docs if d.get("kind") == "PriorityClass"]
    if not pcs:
        pcs = [d for d in yaml.safe_load_all(PC_PATH.read_text()) if isinstance(d, dict)]
    pc = next((d for d in pcs if (d.get("metadata") or {}).get("name") == PRIORITY_CLASS), None)
    assert_true(pc is not None, f"PriorityClass {PRIORITY_CLASS} not found")
    assert pc is not None
    assert_true(
        pc.get("preemptionPolicy") == "Never",
        "embedding-gpu-low must keep preemptionPolicy: Never - it is why repeated "
        "embedder deaths have cost ai/vllm and ai/hermes nothing.",
    )
    assert_true(
        isinstance(pc.get("value"), int) and pc["value"] < 0,
        "embedding-gpu-low must keep a negative priority value so the embedder is "
        "admitted last and evicted first on talos-3.",
    )


def main() -> int:
    docs = _docs()
    tests = [
        test_cache_ram_is_bounded_below_the_container_limit,
        test_requests_and_priority_keep_the_llm_safe,
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
