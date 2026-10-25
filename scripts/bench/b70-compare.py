#!/usr/bin/env python3
"""
Side-by-side table of b70-serving-harness.py result files.

One column per run (labelled by the run's --label, else its filename), one
row per metric. A metric a run did not measure shows "-", never 0, so a
phase that was skipped cannot read as a regression.

Usage:
    python3 scripts/bench/b70-compare.py scripts/bench/results/q8_0-*.json \
        scripts/bench/results/f16-*.json

Compare only runs taken under comparable ambient load: every result file
records the server's requests_processing at each phase boundary.
"""
import argparse, json
from pathlib import Path

PHASE_METRICS = (
    ("P1_pure_prefill", "median", "P1 prefill t/s"),
    ("P2_pure_decode", "median", "P2 decode t/s"),
    ("P3_mixed", "prefill_median", "P3 mixed prefill t/s"),
    ("P3_mixed", "decode_median", "P3 mixed decode t/s"),
    ("P4_concurrent", "wall_s", "P4 wall s"),
    ("P4_concurrent", "aggregate_prefill_ps", "P4 aggregate prefill t/s"),
    ("P4_concurrent", "aggregate_decode_ps", "P4 aggregate decode t/s"),
)


def flatten_metrics(run):
    """One flat {metric name: value} dict from a harness result."""
    phases = run.get("phases", {})
    flat = {}
    for phase, key, name in PHASE_METRICS:
        value = phases.get(phase, {}).get(key)
        if value is not None:
            flat[name] = value
    for row in phases.get("P5_depth_sweep", []):
        for key, kind in (("prefill_median", "prefill"), ("decode_median", "decode")):
            if row.get(key) is not None:
                flat[f"depth {row['depth']} {kind} t/s"] = row[key]
    return flat


def render_table(runs):
    """Markdown table: metrics as rows in first-seen order, runs as columns."""
    flats = [flatten_metrics(r) for r in runs]
    names = [r.get("label") or f"run{i}" for i, r in enumerate(runs)]
    metrics = list(dict.fromkeys(m for f in flats for m in f))
    lines = ["| metric | " + " | ".join(names) + " |",
             "|---|" + "---|" * len(names)]
    for m in metrics:
        cells = [f"{f[m]:.1f}" if m in f else "-" for f in flats]
        lines.append(f"| {m} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path, help="harness result JSON files")
    runs = []
    for path in ap.parse_args().files:
        run = json.loads(path.read_text())
        run.setdefault("label", path.stem)
        runs.append(run)
    print(render_table(runs))


if __name__ == "__main__":
    main()
