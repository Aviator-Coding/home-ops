#!/usr/bin/env python3
"""Offline test for the B70 serving harness depth sweep and compare tool.

Intent: the A/B of KV precision and other llama-server flags needs decode
t/s at context depth (the regime where quantized KV decays), a labelled
result file per variant, and a table that puts variants side by side. This
test pins the pure helpers that carry that, with no server and no network:

  1. parse_depths: "2048,8192" -> [2048, 8192]; rejects junk and non-positive.
  2. result_path: <out_dir>/<label>-<YYYYMMDD-HHMMSS>.json; rejects labels
     that could escape out_dir.
  3. summarize_depth: per-depth medians from raw request timings.
  4. flatten_metrics / render_table: one row per metric, one column per run,
     missing metrics shown as "-" rather than dropped or zeroed.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "scripts/bench"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BENCH / filename)
    require(spec is not None and spec.loader is not None, f"cannot load {filename}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def raises(fn, *args) -> bool:
    try:
        fn(*args)
    except ValueError:
        return True
    return False


def test_parse_depths(h) -> None:
    require(h.parse_depths("2048,8192") == [2048, 8192], "parse_depths basic")
    require(h.parse_depths(" 512 , 1024 ") == [512, 1024], "parse_depths whitespace")
    require(raises(h.parse_depths, "abc"), "parse_depths must reject non-numeric")
    require(raises(h.parse_depths, "0,512"), "parse_depths must reject zero")
    require(raises(h.parse_depths, "-1"), "parse_depths must reject negatives")
    require(raises(h.parse_depths, ""), "parse_depths must reject empty")


def test_result_path(h) -> None:
    now = datetime(2026, 10, 24, 13, 5, 9)
    p = h.result_path("f16-kv", Path("/tmp/out"), now)
    require(p == Path("/tmp/out/f16-kv-20261024-130509.json"), f"result_path wrong: {p}")
    for bad in ("../x", "a/b", "", "has space"):
        require(raises(h.result_path, bad, Path("/tmp/out"), now), f"label {bad!r} must be rejected")


def test_summarize_depth(h) -> None:
    runs = [
        {"prompt_n": 2000, "prompt_ps": 1500.0, "pred_ps": 60.0},
        {"prompt_n": 2000, "prompt_ps": 1700.0, "pred_ps": 64.0},
        {"prompt_n": 2000, "prompt_ps": 1600.0, "pred_ps": 62.0},
    ]
    s = h.summarize_depth(2048, runs)
    require(s["depth"] == 2048, "summarize_depth depth")
    require(s["prefill_median"] == 1600.0, f"prefill median wrong: {s['prefill_median']}")
    require(s["decode_median"] == 62.0, f"decode median wrong: {s['decode_median']}")
    require(s["n"] == 3, "summarize_depth n")
    empty = h.summarize_depth(2048, [])
    require(empty["prefill_median"] is None and empty["decode_median"] is None, "empty -> None, not 0")


def test_compare(c) -> None:
    base = {
        "label": "q8_0",
        "phases": {
            "P2_pure_decode": {"median": 62.4},
            "P4_concurrent": {"wall_s": 76.3, "aggregate_decode_ps": 13.4},
            "P5_depth_sweep": [
                {"depth": 2048, "prefill_median": 1500.0, "decode_median": 60.0, "n": 3},
                {"depth": 32768, "prefill_median": 900.0, "decode_median": 35.0, "n": 3},
            ],
        },
    }
    variant = {
        "label": "f16",
        "phases": {"P2_pure_decode": {"median": 63.0}},
    }
    flat = c.flatten_metrics(base)
    require(flat["P2 decode t/s"] == 62.4, "flatten P2")
    require(flat["P4 wall s"] == 76.3, "flatten P4 wall")
    require(flat["depth 32768 decode t/s"] == 35.0, "flatten depth decode")
    require(flat["depth 2048 prefill t/s"] == 1500.0, "flatten depth prefill")
    table = c.render_table([base, variant])
    lines = table.splitlines()
    require(lines[0].startswith("| metric") and "q8_0" in lines[0] and "f16" in lines[0], f"header wrong: {lines[0]}")
    row = next(ln for ln in lines if ln.startswith("| depth 32768 decode t/s"))
    require(row.rstrip().endswith("| - |"), f"missing metric must render '-': {row}")
    require(any("P2 decode t/s" in ln and "63.0" in ln for ln in lines), "variant value present")


def main() -> int:
    print("==> load harness and compare tool")
    h = load("b70_harness", "b70-serving-harness.py")
    c = load("b70_compare", "b70-compare.py")
    for fn, arg in (
        (test_parse_depths, h),
        (test_result_path, h),
        (test_summarize_depth, h),
        (test_compare, c),
    ):
        print(f"==> {fn.__name__}")
        fn(arg)
    print("PASS: b70 bench harness depth sweep and compare satisfy contracts")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
