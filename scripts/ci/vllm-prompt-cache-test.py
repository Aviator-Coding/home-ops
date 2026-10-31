#!/usr/bin/env python3
"""Every llama.cpp server in the repo keeps its host-memory pins.

Prevents: an outage of chat (`ai/vllm`) or embeddings (`ai/embedding-gpu`) from
a host-RAM leak that no limit catches. Each pin below is silent when dropped:
the pod starts and serves normally, then leaks for days. `limits.memory` is not
a backstop, because the kernel SIGKILLs at page fault and llama.cpp's
`bad_alloc` handler never runs (ai/vllm reached 39 GiB in 6.6 days, 2026-09-19;
ai/embedding-gpu was OOMKilled 23 times, 2026-09-16).

Generic over every container whose image is `*/llama.cpp` in a HelmRelease
under kubernetes/apps, so a new llama.cpp workload is covered without editing
this file. For each one:

  1. `--cache-ram` is set to an integer >= 0 (absent restores the 8192 MiB
     default; -1 means no limit), `limits.memory` is declared, and a non-zero
     cache sits below it.
  2. A non-zero `--cache-ram` comes with `GLIBC_TUNABLES=glibc.malloc.
     mmap_threshold=<bytes>` (or `MALLOC_MMAP_THRESHOLD_`) at most 32 MiB.
     glibc otherwise raises its threshold to 32 MiB after the first large free
     and the cache's sub-32 MiB chunks are never returned (4.7x overshoot).
  3. A server without `--embedding` sets `GGML_SYCL_FA_ONEDNN` to a value that
     ggml's `sscanf(" %u")` reads as 0. The oneDNN SDPA partition cache is never
     evicted (~1 GiB per million prompt tokens). "false" and "off" do not parse
     and leave it on. Embeddings are exempt: ctx 512 bounds the key space.

Values (4096, 0, 128 KiB) are not pinned; relationships are. Why each pin
exists and when the oneDNN pin may be lifted: skill b70-llm-serving
(references/memory.md). Not caught: llama.cpp renaming the env var in a new
image (VLLMMemoryRetainedAboveBound is the runtime detector, proven by
vllm-memory-alert-test.py).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parents[2]
APPS = ROOT / "kubernetes/apps"
ONEDNN = "GGML_SYCL_FA_ONEDNN"
# glibc's dynamic mmap_threshold ceiling on 64-bit (DEFAULT_MMAP_THRESHOLD_MAX).
MAX_USEFUL_MMAP_THRESHOLD = 32 * 1024 * 1024
# The workloads this file was written for; finding fewer means the walk broke.
EXPECTED_AT_LEAST = {"base/ai/vllm/app:vllm/app", "base/ai/embedding-gpu/app:embedding-gpu/app"}

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


def sscanf_u(value: Any) -> int | None:
    """Mirror ggml_sycl_get_env: sscanf(value, " %u") or None (default 1, ON)."""
    if value is None:
        return None
    match = re.match(r"\s*([+-]?)(\d+)", str(value))
    if match is None:
        return None
    number = int(match.group(2))
    return -number if match.group(1) == "-" else number


def mib(quantity: Any) -> float | None:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([EPTGMK]i?)?", str(quantity).strip())
    if match is None:
        return None
    factors = {
        "": 1 / 1024**2, "Ki": 1 / 1024, "Mi": 1, "Gi": 1024, "Ti": 1024**2,
        "K": 1000 / 1024**2, "M": 1000**2 / 1024**2, "G": 1000**3 / 1024**2,
        "T": 1000**4 / 1024**2,
    }
    unit = match.group(2) or ""
    return float(match.group(1)) * factors[unit] if unit in factors else None


def image_repo(image: Any) -> str:
    if isinstance(image, dict):
        return str(image.get("repository") or "")
    return str(image or "").split("@")[0].rsplit(":", 1)[0]


def env_map(container: dict[str, Any]) -> dict[str, Any]:
    env = container.get("env") or {}
    if isinstance(env, list):
        return {e.get("name"): e.get("value") for e in env if isinstance(e, dict)}
    return env


def llama_containers() -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted(APPS.rglob("*.yaml")):
        text = path.read_text()
        if "llama.cpp" not in text or "HelmRelease" not in text:
            continue
        for doc in yaml.safe_load_all(text):
            if not isinstance(doc, dict) or doc.get("kind") != "HelmRelease":
                continue
            controllers = ((doc.get("spec") or {}).get("values") or {}).get("controllers") or {}
            for cname, ctrl in controllers.items():
                for kname, container in ((ctrl or {}).get("containers") or {}).items():
                    if image_repo((container or {}).get("image")).endswith("/llama.cpp"):
                        yield f"{path.parent.relative_to(APPS)}:{cname}/{kname}", container


def flag_value(args: list[str], *names: str) -> str | None:
    for name in names:
        if name in args:
            idx = args.index(name)
            return args[idx + 1] if idx + 1 < len(args) else ""
    return None


def check_container(name: str, container: dict[str, Any]) -> None:
    args = [str(a) for a in container.get("args") or []]
    env = env_map(container)
    raw = flag_value(args, "--cache-ram", "-cram")
    check(raw is not None, f"{name}: sets --cache-ram (absent = silent 8192 MiB host cache)")
    if raw is None:
        return
    check(re.fullmatch(r"\d+", raw) is not None,
          f"{name}: --cache-ram {raw!r} is an integer >= 0 (-1 means no limit)")
    if re.fullmatch(r"\d+", raw) is None:
        return
    cache = int(raw)
    limit = mib(((container.get("resources") or {}).get("limits") or {}).get("memory", ""))
    check(limit is not None, f"{name}: declares a parseable limits.memory to bound the cache against")
    if cache > 0 and limit is not None:
        check(cache < limit, f"{name}: --cache-ram {cache} MiB is below limits.memory ({limit:.0f} MiB)")

    if cache > 0:
        tunables, legacy = env.get("GLIBC_TUNABLES"), env.get("MALLOC_MMAP_THRESHOLD_")
        match = re.search(r"glibc\.malloc\.mmap_threshold=(\d+)", str(tunables or ""))
        threshold = int(match.group(1)) if match else (
            int(legacy) if re.fullmatch(r"\d+", str(legacy or "")) else None)
        check(threshold is not None and 0 < threshold <= MAX_USEFUL_MMAP_THRESHOLD,
              f"{name}: non-zero --cache-ram pins glibc mmap_threshold at 1..32 MiB (got {threshold})")

    if "--embedding" not in args and "--embeddings" not in args:
        value = env.get(ONEDNN)
        check(sscanf_u(value) == 0,
              f"{name}: {ONEDNN}={value!r} reads as 0 under sscanf(\" %u\") (oneDNN SDPA cache off)")


def check_sscanf_mirror() -> None:
    cases = {"0": 0, " 0": 0, "00": 0, "0 # off": 0, "1": 1, "2": 2,
             "false": None, "off": None, "no": None, "": None, "disabled": None}
    bad = {v: sscanf_u(v) for v, want in cases.items() if sscanf_u(v) != want}
    check(not bad, f"sscanf mirror agrees with ggml on the traps (disagrees on {bad})")


def main() -> int:
    check_sscanf_mirror()
    found = dict(llama_containers())
    check(EXPECTED_AT_LEAST <= set(found),
          f"found the llama.cpp containers (missing {sorted(EXPECTED_AT_LEAST - set(found))})")
    for name, container in found.items():
        check_container(name, container)
    print(f"\n{len(failures)} failure(s) over {len(found)} llama.cpp container(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
