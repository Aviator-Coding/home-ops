#!/usr/bin/env python3
"""
Passive ai/vllm prefill throughput by context depth, read from llama-server logs.

Sends nothing to the server: every number comes from the per-request lines
llama-server already logs at its default verbosity:

    slot print_timing: ... | task N | prompt eval time = X ms / P tokens (...)
    slot print_timing: ... | task N |        eval time = Y ms / G tokens (...)
    slot      release: ... | task N | stop processing: n_tokens = T, ...

For each finished request, prefill t/s = P / X, and the context depth at the
end of prefill = T - G. Requests are bucketed by that depth and filtered to
those with at least --min-new newly processed prompt tokens, so short
cache-hit follow-ups do not swamp the attention-bound long prefills. Compare
two runs only at the same --min-new, and only medians with a reasonable n.

Written to measure the prefill cost of GGML_SYCL_FA_ONEDNN=0 (2026-09-22) at
this server's real depths, against the pre-change baseline recorded in
docs/ai/vllm-onednn-sdpa-leak.md. It works for any before/after comparison.

Usage:
    # full pod history from Loki (kubectl logs keeps only the last few hours)
    kubectl -n monitoring port-forward svc/loki 13101:3100 &
    python3 scripts/bench/vllm-prefill-by-depth.py --loki http://localhost:13101 \
        --pod vllm-5f8545b44d-2x424 --start 2026-09-20T14:00:00Z --end 2026-09-22T23:00:00Z

    # or any saved log on stdin
    kubectl -n ai logs deploy/vllm -c app | python3 scripts/bench/vllm-prefill-by-depth.py
"""
import argparse, json, re, statistics, sys, urllib.parse, urllib.request
from datetime import datetime

BUCKETS = [(0, 32768), (32768, 65536), (65536, 98304), (98304, 131072), (131072, None)]

RE_PROMPT = re.compile(r"task (\d+) \| prompt eval time =\s+([\d.]+) ms /\s+(\d+) tokens")
RE_EVAL = re.compile(r"task (\d+) \|\s+eval time =\s+([\d.]+) ms /\s+(\d+) tokens")
RE_RELEASE = re.compile(r"task (\d+) \| stop processing: n_tokens = (\d+)")


def loki_lines(url, pod, start, end, namespace="ai"):
    """Page forward through Loki query_range for one pod's full log."""
    def ns(t):
        return int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() * 1e9)
    lo, hi = ns(start), ns(end)
    while True:
        q = urllib.parse.urlencode({
            "query": f'{{namespace="{namespace}", pod="{pod}"}}',
            "start": lo, "end": hi, "limit": 5000, "direction": "forward",
        })
        data = json.load(urllib.request.urlopen(f"{url}/loki/api/v1/query_range?{q}"))
        batch = sorted((int(ts), line) for s in data["data"]["result"] for ts, line in s["values"])
        yield from (line for _, line in batch)
        if len(batch) < 5000:
            return
        lo = batch[-1][0] + 1


def collect(lines):
    tasks = {}
    for line in lines:
        for rx, keys in ((RE_PROMPT, ("pt", "pn")), (RE_EVAL, (None, "gn")), (RE_RELEASE, ("nt",))):
            m = rx.search(line)
            if not m:
                continue
            t = tasks.setdefault(m.group(1), {})
            if rx is RE_PROMPT:
                t["pt"], t["pn"] = float(m.group(2)), int(m.group(3))
            elif rx is RE_EVAL:
                t["gn"] = int(m.group(3))
            else:
                t["nt"] = int(m.group(2))
            break
    return [t for t in tasks.values() if {"pt", "pn", "gn", "nt"} <= t.keys() and t["pt"] > 0]


def report(done, min_new):
    print(f"requests with >= {min_new} new prompt tokens: prefill t/s by context depth at end of prefill")
    print(f"  {'depth':<10} {'n':>5} {'median':>7} {'p25':>6} {'p75':>6}")
    for lo, hi in BUCKETS:
        label = f"{lo // 1024}-{hi // 1024 if hi else 'max'}k"
        vals = [t["pn"] / t["pt"] * 1000 for t in done
                if t["pn"] >= min_new and lo <= t["nt"] - t["gn"] < (hi or float("inf"))]
        if len(vals) < 3:
            print(f"  {label:<10} {len(vals):>5}   (too few)")
            continue
        q = statistics.quantiles(vals, n=4)
        print(f"  {label:<10} {len(vals):>5} {statistics.median(vals):>7.0f} {q[0]:>6.0f} {q[2]:>6.0f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--loki", help="Loki base URL; omit to read log lines from stdin")
    ap.add_argument("--pod")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--min-new", type=int, action="append",
                    help="minimum new prompt tokens per request (repeatable; default 512 and 2048)")
    a = ap.parse_args()
    if a.loki and not (a.pod and a.start and a.end):
        ap.error("--loki needs --pod, --start and --end")
    lines = loki_lines(a.loki, a.pod, a.start, a.end) if a.loki else sys.stdin
    done = collect(lines)
    print(f"{len(done)} finished requests parsed")
    for min_new in a.min_new or [512, 2048]:
        report(done, min_new)


if __name__ == "__main__":
    main()
