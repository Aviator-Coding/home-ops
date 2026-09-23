# `ai/vllm` oneDNN SDPA leak: the 2026-09-22 memory growth and its fix

**Status:** fix shipped 2026-09-22 (`GGML_SYCL_FA_ONEDNN: "0"` on the `vllm` container), with the
captain accepting the prefill cost (§6). Two things are **not yet measured** and are post-merge
follow-ups: confirming the leak has stopped, and the real prefill slowdown at this server's context
depths. Both have a method and a pre-merge baseline in §8.

Claims are labelled `[MEASURED]` (read off this cluster, read-only), `[CODE]` (read from the
llama.cpp source at the pinned tag `b10820`) and `[INFERENCE]` (reasoning from those).

This supersedes part of [`vllm-host-prompt-cache.md`](vllm-host-prompt-cache.md). That document's
two fixes (`--cache-ram 4096`, the glibc `mmap_threshold` pin) are correct and stay. But its
diagnosis that the 17 GiB heap was cache-churn fragmentation was mostly wrong - see §5.

---

## 1. Symptom

Pod `vllm-5f8545b44d-2x424` started 2026-09-20 14:03Z with both #1731 halves live. Its working set
climbed with no plateau. `VLLMMemoryExceedsRequest` fired 2026-09-22 09:22Z:

| | working set (3h max) | note |
|---|---:|---|
| 09-20 15:36 | 1,384 MiB | post-load |
| 09-21 09:36 | 6,999 MiB | |
| 09-22 00:36 | 8,928 MiB | |
| 09-22 09:36 | 12,457 MiB | crosses the 12Gi request |
| 09-22 18:36 | 14,815 MiB | ~+3.5 GiB/day toward the 48Gi limit |

The previous pod (`vllm-5cb8f5f6f4-zmvqq`) looked flat for its 17h life, and the 12Gi request was
sized from it. **That pod processed 0.95M prompt tokens in 18h.** The leaking pods processed 4.5-9M
per day, and this one processed 8.67M in 2.35 days (6,290 requests). `[MEASURED]` Its flatness was
low traffic, not health.

## 2. What it was not `[MEASURED]`

- **Not reclaimable page cache.** `RssAnon` was 13.3 GiB of 13.7 GiB RSS, and working set tracked
  `container_memory_rss` exactly. The GGUF sits in `container_memory_cache`, which is excluded from
  working set and was flat.
- **Not heap fragmentation.** `[heap]` was 1,701 MiB (17,107 MiB before #1731), and the 64 MiB-aligned
  glibc arena heaps totalled 139 MiB. The `mmap_threshold` pin works.
- **Not time.** Working set was perfectly flat through idle windows (09-21 04:30-16:00, 09-22
  16:30-19:30), and it climbed only with requests. In the sampled 1h52m idle window after 00:02Z on
  09-23, the process's memory map changed by **zero bytes**.
- **Not the prompt cache or the context checkpoints.** Both are visible and bounded. Cache entries
  are single 128-1,900 MiB mappings, allocated and evicted in step with the server's
  `making room for prompt cache entry` log lines. Checkpoints are exactly 62.8 MiB each (this model's
  recurrent state: 30 linear-attention layers x (2 MiB S + 96 KiB conv)). About 9 were live, and they
  are freed as they age out.

## 3. What it was

### 3a. The allocation fingerprint `[MEASURED]`

With the threshold pinned at 128 KiB, every allocation above it gets its own mapping and is
`munmap`ed on free. **So every surviving anonymous mapping is a live allocation.** The growth was
~3,900 small anonymous mappings (7.8 GiB at 2026-09-23 01:54Z), with sizes on an exact ladder:
N x 8192 bytes plus one malloc-header page, where N = 68 + 76k (2,956 mappings) or 53 + 76k (165).
The 1,764 KiB size (N = 220) alone occurred 1,693 times. Fixed-size objects in their thousands are
compiled code or descriptors, not data proportional to prompts.

### 3b. The code `[CODE]`

`ggml/src/ggml-sycl/fattn-onednn.cpp` in `b10820`:

```cpp
// compile once per (device, shape, KV strides), reuse across layers/calls.
static std::unordered_map<std::string, sdpa_partition> cache;
...
snprintf(keyb, ..., "%d:%lld:%lld:%lld:%lld:%lld:...", device, H, Hkv, q, seq, d, ...);
auto it = cache.find(keyb);
if (it == cache.end()) {
    it = cache.emplace(keyb, build_sdpa(eng, H, Hkv, q, seq, d, k_str, v_str)).first;
}
```

`build_sdpa` builds and **compiles** a oneDNN Graph SDPA partition, a GPU kernel JIT-compiled through
IGC (`libigc`, `libopencl-clang2` are both mapped in the process). The map has no capacity and is
never erased. Its key includes `q` (query tokens in the ubatch) and `seq` (the KV length, n_kv).

Dispatch (`fattn.cpp`, `ggml_sycl_get_best_fattn_kernel`) sends a flash-attention call here when
`Q->ne[1] >= 32` and the path is supported. For non-F16 KV that means one of the accepted quant
types (`q8_0` qualifies, since upstream #25874 on 2026-08-04) and `K->ne[1] >= 1024`. This server runs
`q8_0` KV at 40k-160k context. **Every prefill ubatch of 32 or more tokens takes this path.**
Decode (q = 1-4) never does.

### 3c. Why it grows with traffic, and how fast `[MEASURED + INFERENCE]`

With `kv_unified`, `seq` is the shared cache's high-water mark, padded to 256. Fresh prefills
share a 1,236-token prefix: the first prefill batch ends there in the logs. So full 2,048-token
chunks land on recurring `(q, seq)` boundaries and hit the cache. `[INFERENCE]` Conversation-specific
tail chunks (the remainder before a user-message boundary, the `n_ubatch + 4` / `4` checkpoint
splits) mostly do not. Two live requests, diffed read-only against 60s `/proc/1/maps` snapshots:

| request | new prompt tokens | new ladder mappings | ladder mappings freed |
|---|---:|---|---:|
| task 2314441, 22:24Z | 34,112 (16 repeat-shape 2,048 chunks, ~3 new tail shapes) | 3 (1,764 / 1,824 / 3,520 KiB) | 0 |
| task 2316193, 00:01Z | 17,530 | 3,520 KiB + 2 x 608 KiB + 140 KiB | 0 |

In the same diffs, cache entries and checkpoints came and went normally. Across the pod's life the
retained growth came to **~1 GiB per million prompt tokens**. Distinct `(q, seq)` keys are
effectively unbounded (q up to 2,048, seq up to 1,024 buckets), so nothing plateaus.

### 3d. Timeline `[CODE + MEASURED]`

- 2026-07-15: upstream #25222 adds oneDNN SDPA for F16 KV.
- 2026-08-04: upstream #25874 extends it to `q4_0`-`q8_0` and F32 KV. That is our KV type.
- Until 2026-09-07, `ai/vllm` ran `b9592` (2026-06-10), which predates both.
- 2026-09-07: the bump to `b10820` (PR #1621). The first recorded climb starts at that boot
  (`prometheusrule.yaml` header: 1.8Gi at 12:40Z to 37.7Gi a week later). Prometheus retention does
  not reach back to `b9592`, so there is no direct measurement of the old image.
- Upstream master at 2026-09-22 22:20Z still has the unbounded map. A search of upstream issues and
  PRs that day found none covering it.

## 4. Why the earlier pods looked the way they did `[INFERENCE from MEASURED rates]`

- **Pre-#1731 pods** (`2895c`, 8192 MiB default cache, no malloc pin) grew ~5.4 GiB/day at ~5.1M
  tokens/day, i.e. **~1.05 GiB per M tokens**.
- **This pod**, excluding its bounded cache and checkpoints: **~0.9-1.0 GiB per M tokens**.

The rate did not change when #1731 landed, so #1731 did not touch the dominant driver.

## 5. Correction to `vllm-host-prompt-cache.md`

That document attributed the pre-fix 17,107 MiB `[heap]` to prompt-cache churn fragmenting glibc's
main arena. `[INFERENCE]` Most of it was this leak. The partition allocations are 0.4-8 MiB, far under
glibc's dynamic 32 MiB threshold, so before the pin they came from, and were retained in, the brk
heap. The per-token rate above is the evidence. Both #1731 changes stay:

- **`--cache-ram 4096`**, because without it the 8192 MiB default returns on top.
- **The `mmap_threshold` pin**, because it keeps the heap at ~1.7 GiB. It is also what made this leak
  visible: one mapping per live allocation is what exposed the size ladder.

## 6. The fix, and what it costs

`kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`, `vllm` container env:

```yaml
GGML_SYCL_FA_ONEDNN: "0"
```

Long prefills then fall to the MKL XMX flash-attention path (`GGML_SYCL_ENABLE_MKL_FA`, default 1).
Its gate (gqa >= 2, head_dim a multiple of 64 up to 512, q >= 32, n_kv >= 1024) this model meets,
and it keeps no per-shape cache. Decode is unchanged.

**The value must be numeric.** ggml reads it with `sscanf(" %u")` (`ggml_sycl_get_env`), so `"false"`,
`"off"` and `"no"` fail to parse and silently keep the default of 1 - the leak stays on.

**Expected prefill cost (upstream, not measured here):** #25874 benchmarked this exact model and KV
type (Qwen3.6-35B-A3B, `q8_0`, Arc Pro B70) at 32K prefill: **1,184 t/s with oneDNN SDPA vs 834 t/s
without, -30%**. #25222's dense-model table shows oneDNN's advantage growing with depth. This server
runs deeper than 32K (typically 40-160k), so the real cost here may exceed 30%. It could not be
measured before merge without load on the live server. §8b measures it passively afterwards.

**Not changed:** `ai/embedding-gpu` runs the same image with F16 KV, so it also takes the oneDNN path.
But its key space is bounded (`--ctx-size 512`, so q <= 512 and seq in {256, 512}). Measured flat at
365 MiB over 5.5 days `[MEASURED]`. No action.

## 7. Guards

- **CI:** `scripts/ci/vllm-fa-onednn-test.py` requires the env var on the live `vllm` controller, with
  a value that `sscanf(" %u")` reads as 0. It is mutation-proven red on 7 violations: removed,
  `"1"`, `"false"`, `"off"`, `""`, misspelled, and moved to `vllm-embed`.
- **Runtime:** `VLLMMemoryRetainedAboveBound` (`prometheusrule.yaml`) fires when the working set's
  3h minimum stays above 8 GiB for 1h. The retained ceiling is baseline 1,232 Mi + `--cache-ram`
  4,096 Mi + one slot's 32 x 62.8 Mi checkpoints = ~7,338 Mi. `scripts/ci/vllm-memory-alert-test.py`
  holds the threshold at or above that ceiling (so raising `--cache-ram` alone fails CI) and below
  the limit. Replayed against live Prometheus both ways:
  - it fires on `2x424` at 12:40Z, ~46.5h after boot;
  - it stays quiet on `zmvqq` and on `2x424`'s first ~45h.

  On this leak `VLLMMemoryExceedsRequest` paged first, because the 12Gi request sits only ~1.7x
  above the ceiling. The new rule does not depend on the request. At the 39Gi request (2026-09-14..20)
  nothing paged on this leak for 4.7 days, while the same floor rule at that config's own ceiling
  would have fired 2026-09-16 09:35Z. It is also the only detector for this gate's blind spot: a
  future image that renames or ignores `GGML_SYCL_FA_ONEDNN`.

  **Corrected 2026-09-23: the rule now ANDs its floor with the live instant series.** A range
  selector keeps a pod's samples after the pod is gone (staleness markers only hide instant
  lookups), so as first shipped it kept evaluating a terminated pod for up to 3h. On the rollout of
  this very fix, the replaced pod `2x424` went pending at 03:56Z with a 12.27 GiB floor and would
  have paged ~04:56Z-06:55Z about a pod that no longer existed. A targeted Alertmanager silence
  covered that one pod until 07:00Z. The promtool case `retained_quiet_once_pod_terminated` goes red
  on the pre-fix expression. After an in-place container restart the still-live pod keeps its
  pre-restart floor for the 3h window; that is deliberate, because it has just shown the growth.
- `VLLMMemoryExceedsRequest`'s description no longer says "the request needs raising". This time the
  cause was a leak, and raising the request would only have bought time.

## 8. Post-merge verification

Everything below is read-only or passive. Nothing sends load to the server.

### 8a. The leak has stopped

After the rollout, wait for **at least 48h and at least 3M prompt tokens** (at the pre-fix rate that
would have added ~3 GiB). Then check all three:

1. The working-set floor plateaus. Expect it to rise once as the prompt cache fills in the first
   hours of traffic, then hold at or under the ~7.2 GiB retained ceiling:

   ```promql
   min_over_time(container_memory_working_set_bytes{namespace="ai", pod=~"vllm-.*", pod!~"vllm-vllm-embed-.*", container="app"}[6h])
   sum(increase(llamacpp:prompt_tokens_total{namespace="ai"}[1d]))   # traffic check
   ```

2. The small-mapping census stops growing with requests. The pre-fix pod read **3,897 regions /
   7,794 MiB** at 2d11h (2026-09-23 01:54Z). Sample at intervals; after the cache has filled, counts
   should move only by transients, not ratchet up with each busy hour:

   ```sh
   kubectl -n ai exec deploy/vllm -c app -- cat /proc/1/maps | python3 -c '
   import sys
   n = t = 0
   for line in sys.stdin:
       p = line.split()
       if len(p) == 5 and p[1] == "rw-p":
           a, b = (int(x, 16) for x in p[0].split("-"))
           if b - a < 16 << 20:
               n += 1; t += b - a
   print(f"{n} small anonymous regions, {t >> 20} MiB")'
   ```

3. `VLLMMemoryRetainedAboveBound` and `VLLMMemoryExceedsRequest` stay quiet.

**This is also the causal test.** If the floor or the census keeps ratcheting up under traffic with
`GGML_SYCL_FA_ONEDNN=0` live, this diagnosis is wrong and the investigation reopens from §2. The
falsifier not yet observed before merge was a request with no prefill ubatch of 32 or more tokens,
which should add no ladder mappings; the intervention supersedes it.

### 8b. The real prefill slowdown at our depths

`scripts/bench/vllm-prefill-by-depth.py` reads per-request timings from the server log (Loki or stdin)
and buckets prefill t/s by context depth at the end of prefill. **Pre-merge baseline**, pod `2x424`
(oneDNN SDPA on), 2026-09-20 14:00Z to 09-22 23:00Z, 6,291 requests:

| depth at end of prefill | >= 512 new tokens: n / median t/s | >= 2,048 new tokens: n / median t/s |
|---|---:|---:|
| 0-32k | 161 / 471 | 75 / 1,594 |
| 32-64k | 506 / 1,017 | 312 / 1,083 |
| 64-96k | 576 / 823 | 115 / 1,053 |
| 96-128k | 276 / 727 | 31 / 945 |
| 128k+ | 92 / 617 | 2 / (too few) |

After at least 48h of post-merge traffic, run the same script against the new pod:

```sh
kubectl -n monitoring port-forward svc/loki 13101:3100 &
python3 scripts/bench/vllm-prefill-by-depth.py --loki http://localhost:13101 \
  --pod <new vllm pod> --start <rollout time> --end <now>
```

Compare medians bucket by bucket at the same `--min-new`, trusting only buckets with a reasonable n.
The `>= 2,048` rows are the attention-heavy ones where the fallback cost shows. Upstream's -30% at
32K is the expectation; a larger drop at 64k+ would not be surprising (§6). Aggregate throughput is
a coarser second view:
`sum(rate(llamacpp:prompt_tokens_total[1d])) / sum(rate(llamacpp:prompt_seconds_total[1d]))`,
~929 t/s over the pre-merge pod's life. Decode (`tg` in the same log lines) should not move.

## 9. Lifting the pin

Only when the pinned llama.cpp tag bounds the partition cache: a capacity/LRU on `cache` in
`fattn-onednn.cpp`, or keys that no longer carry the KV length. Verify it in that tag's source, then
remove the env var and `scripts/ci/vllm-fa-onednn-test.py`'s requirement in one change, and repeat §8a
after the rollout. Also check on every image bump that `ggml-sycl.cpp` still reads
`GGML_SYCL_FA_ONEDNN`. If it is renamed, this pin silently stops working, and only
`VLLMMemoryRetainedAboveBound` would notice.

## 10. Related

- [`vllm-host-prompt-cache.md`](vllm-host-prompt-cache.md): the #1731 fixes that stay, and the
  narrative corrected in §5
- [`b70-llm-serving-tuning.md`](b70-llm-serving-tuning.md): the `b10820` bump. Its measured prefill
  gains over `b9592` likely include this path, which `b9592` did not have
- [`../talos-3-scheduling-truth.md`](../talos-3-scheduling-truth.md) §8: the 12Gi request sized from
  the low-traffic 17h window
- `kubernetes/apps/base/ai/vllm/app/{helmrelease,prometheusrule}.yaml`, with rationale inline
