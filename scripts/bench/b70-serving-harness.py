#!/usr/bin/env python3
"""
B70 llama.cpp serving harness.

Reproduces the production traffic shape (prompt-heavy, ~7.5:1 prompt:completion,
concurrent slots) and isolates the measured mixed-batch prefill penalty.

Phases:
  P1 pure-prefill  : 1 client, big prompt, n_predict=1  -> prefill t/s with no decode sharing
  P2 pure-decode   : 1 client, tiny prompt, long gen    -> decode t/s alone
  P3 mixed         : decode client running WHILE prefill client submits -> both rates under sharing
  P4 concurrent    : N clients, production-shaped       -> aggregate loaded throughput

Every number comes from the server's own per-request `timings` block, not wall clock.
Ambient production load is sampled from /metrics around each phase and reported,
because this runs against the live server and there is no reliable idle window:
a phase that ran during a quiet period will beat the same config during a busy
one by several times, so compare only runs with comparable ambient, or use P4,
whose self-generated concurrency dominates.

Usage:
    kubectl -n ai port-forward deploy/vllm 18000:8000 &
    python3 scripts/bench/b70-serving-harness.py --phases 1234 --reps 2 \
        --prompt-tokens 16384 --concurrency 4 --out result.json

Evidence trail and the matrix this produced: docs/ai/b70-llm-serving-tuning.md
"""
import argparse, json, random, statistics as st, string, sys, threading, time
import urllib.request

BASE = "http://127.0.0.1:18000"  # overridden by --base

def post(path, payload, timeout=1800):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())

def get(path, timeout=30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.loads(r.read())

_WORDS = None
def words():
    global _WORDS
    if _WORDS is None:
        random.seed(1234)
        _WORDS = ["".join(random.choices(string.ascii_lowercase, k=random.randint(3, 9)))
                  for _ in range(20000)]
    return _WORDS

_TPW = None
def tok_per_word():
    """Calibrate tokens-per-word against the server's own tokenizer, once."""
    global _TPW
    if _TPW is None:
        import random as _r
        _rp = _r.Random(99)
        probe = " ".join(_rp.choice(words()) for _ in range(2000))
        n = len(post("/tokenize", {"content": probe})["tokens"])
        _TPW = n / 2000.0
        print(f"# tokenizer calibration: {_TPW:.3f} tokens/word", flush=True)
    return _TPW

def make_prompt(approx_tokens, rng):
    """Unique, non-cacheable prompt of ~approx_tokens tokens (sized via /tokenize)."""
    w = words()
    n = max(1, int(approx_tokens / tok_per_word()))
    return " ".join(rng.choice(w) for _ in range(n))

def completion(prompt, n_predict, cache=False):
    """Return the server's own timings for one /completion request."""
    r = post("/completion", {
        "prompt": prompt,
        "n_predict": n_predict,
        "temperature": 0,
        "ignore_eos": n_predict > 1,
        "cache_prompt": cache,
    })
    t = r["timings"]
    return {
        "prompt_n": t.get("prompt_n"),
        "prompt_ps": t.get("prompt_per_second"),
        "prompt_ms": t.get("prompt_ms"),
        "pred_n": t.get("predicted_n"),
        "pred_ps": t.get("predicted_per_second"),
        "pred_ms": t.get("predicted_ms"),
    }

def ambient():
    """Sample production load on the shared server."""
    try:
        txt = urllib.request.urlopen(BASE + "/metrics", timeout=10).read().decode()
        out = {}
        for line in txt.splitlines():
            if line.startswith("#"):
                continue
            for k in ("requests_processing", "requests_deferred", "n_busy_slots_per_decode"):
                if k in line:
                    out[k] = float(line.split()[-1])
        return out
    except Exception as e:
        return {"err": str(e)}

def props():
    p = get("/props")
    return {"n_ctx": p["default_generation_settings"]["n_ctx"],
            "total_slots": p["total_slots"],
            "build": p["build_info"],
            "spec": p["default_generation_settings"]["params"].get("speculative.types")}

def run(args):
    rng = random.Random(args.seed)
    res = {"props": props(), "phases": {}}
    print(f"# server: {res['props']}", flush=True)

    if args.warmup_req:
        print("# warmup request (discarding: first prefill after restart is cold)", flush=True)
        completion(make_prompt(4096, random.Random(1)), 1)

    def note(tag):
        a = ambient()
        print(f"#   ambient@{tag}: {a}", flush=True)
        return a

    # ---- P1: pure prefill -------------------------------------------------
    if "1" in args.phases:
        note("P1-start")
        runs = []
        for i in range(args.reps):
            p = make_prompt(args.prompt_tokens, rng)
            r = completion(p, 1)
            runs.append(r)
            print(f"P1 rep{i}: prompt_n={r['prompt_n']} prefill={r['prompt_ps']:.1f} t/s", flush=True)
        res["phases"]["P1_pure_prefill"] = {
            "prefill_ps": [r["prompt_ps"] for r in runs],
            "median": st.median([r["prompt_ps"] for r in runs]),
            "prompt_n": [r["prompt_n"] for r in runs],
        }
        note("P1-end")

    # ---- P2: pure decode --------------------------------------------------
    if "2" in args.phases:
        note("P2-start")
        runs = []
        for i in range(args.reps):
            r = completion("Once upon a time", args.decode_tokens)
            runs.append(r)
            print(f"P2 rep{i}: pred_n={r['pred_n']} decode={r['pred_ps']:.2f} t/s", flush=True)
        res["phases"]["P2_pure_decode"] = {
            "decode_ps": [r["pred_ps"] for r in runs],
            "median": st.median([r["pred_ps"] for r in runs]),
        }
        note("P2-end")

    # ---- P3: mixed batch (the measured production penalty) ----------------
    if "3" in args.phases:
        note("P3-start")
        decode_out, prefill_out = [], []
        stop = threading.Event()

        def decoder():
            # long generation so it is still decoding while prefills land
            while not stop.is_set():
                try:
                    r = completion("Once upon a time", args.mixed_decode_tokens)
                    decode_out.append(r)
                except Exception as e:
                    print(f"P3 decoder err {e}", flush=True)
                    break

        th = threading.Thread(target=decoder, daemon=True)
        th.start()
        time.sleep(args.warmup)          # let the decoder get resident
        for i in range(args.reps):
            p = make_prompt(args.prompt_tokens, rng)
            r = completion(p, 1)
            prefill_out.append(r)
            print(f"P3 rep{i}: prompt_n={r['prompt_n']} prefill={r['prompt_ps']:.1f} t/s "
                  f"(while decoding)", flush=True)
        stop.set()
        th.join(timeout=args.mixed_decode_tokens)  # let the in-flight decode finish
        pf = [r["prompt_ps"] for r in prefill_out]
        dc = [r["pred_ps"] for r in decode_out if r.get("pred_ps")]
        res["phases"]["P3_mixed"] = {
            "prefill_ps": pf, "prefill_median": st.median(pf) if pf else None,
            "decode_ps": dc, "decode_median": st.median(dc) if dc else None,
        }
        for r in decode_out:
            print(f"P3 decoder: pred_n={r['pred_n']} decode={r['pred_ps']:.2f} t/s", flush=True)
        note("P3-end")

    # ---- P4: concurrent production-shaped load ----------------------------
    if "4" in args.phases:
        note("P4-start")
        out, lock = [], threading.Lock()
        def client(cid):
            r_ = random.Random(args.seed + cid * 977)
            for j in range(args.p4_reqs):
                p = make_prompt(args.prompt_tokens, r_)
                try:
                    r = completion(p, args.p4_predict)
                except Exception as e:
                    print(f"P4 c{cid} err {e}", flush=True); return
                with lock:
                    out.append(r)
                    print(f"P4 c{cid}#{j}: prompt_n={r['prompt_n']} "
                          f"prefill={r['prompt_ps']:.1f} decode={r['pred_ps']:.2f}", flush=True)
        t0 = time.time()
        ths = [threading.Thread(target=client, args=(c,)) for c in range(args.concurrency)]
        [t.start() for t in ths]; [t.join() for t in ths]
        wall = time.time() - t0
        pt = sum(r["prompt_n"] for r in out); dt_ = sum(r["pred_n"] for r in out)
        res["phases"]["P4_concurrent"] = {
            "concurrency": args.concurrency, "requests": len(out), "wall_s": wall,
            "prompt_tokens": pt, "decode_tokens": dt_,
            "aggregate_prefill_ps": pt / wall, "aggregate_decode_ps": dt_ / wall,
            "per_req_prefill_median": st.median([r["prompt_ps"] for r in out]) if out else None,
            "per_req_decode_median": st.median([r["pred_ps"] for r in out]) if out else None,
        }
        note("P4-end")

    print("\n===JSON===")
    print(json.dumps(res, indent=2))
    if args.out:
        open(args.out, "w").write(json.dumps(res, indent=2))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="1234")
    ap.add_argument("--prompt-tokens", type=int, default=16384)
    ap.add_argument("--decode-tokens", type=int, default=128)
    ap.add_argument("--mixed-decode-tokens", type=int, default=600)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--warmup", type=float, default=6.0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--p4-reqs", type=int, default=1)
    ap.add_argument("--p4-predict", type=int, default=256)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--warmup-req", type=int, default=1,
                    help="discard one request first: the first prefill after a restart is cold")
    ap.add_argument("--base", default=BASE, help="server base URL (port-forward target)")
    ap.add_argument("--out", default=None)
    _a = ap.parse_args()
    BASE = _a.base
    run(_a)
