# Host memory on `ai/vllm`

Two independent leaks, both host RAM, neither bounded by `limits.memory`.

## Prompt cache (PR #1731)

llama.cpp's prompt cache defaults to 8192 MiB. Chat prefix reuse is real
(mean `f_keep` 0.975 over 1560 slot selections), so the pin is `4096`,
not `0`. Embeddings use `0` because reads are gated on
`SERVER_TASK_TYPE_COMPLETION`.

The logical cap is not enough. glibc raises `mmap_threshold` up to
32 MiB after the first large free, then sub-32 MiB chunks come from the
main arena and are never returned. `GLIBC_TUNABLES` pins the threshold
at 128 KiB so those chunks are `mmap`'d and `munmap`'d. Keep both.

`--no-mmap` of the GGUF is not this leak. At load, RSS stayed ~635 MiB
while the file-backed page cache held the weights. Do not debug a climb
by toggling it.

Request is **12Gi**. It was 39Gi while the cache was unbounded. The 12Gi
figure is baseline ~1232 MiB plus 2.7 times the 4096 MiB cache bound.
Limit stays **48Gi** so a spike can surface as the alert below instead
of an immediate kill. Do not cut the limit to "match" the request, and
do not raise the request to hide a climb. Skill `node-scheduling` for
why the request is the reservation.

## oneDNN SDPA partition cache

With `q8_0` KV, every prefill ubatch of at least 32 tokens over
`n_kv >= 1024` compiles an SDPA partition and stores it in a
never-evicted map (one per query-length, KV-length pair). That growth
came back after PR #1731 with `--cache-ram` and the malloc pin both
working. Measured about 1 GiB retained anonymous memory per million
prompt tokens.

`GGML_SYCL_FA_ONEDNN: "0"` falls long prefills back to MKL flash
attention (no per-shape cache). Decode is unchanged (the oneDNN path
does not run below 32 query tokens). Upstream's cost on this model and
KV type at 32K prefill is about 30% (1184 t/s vs 834 t/s). Deeper
contexts may cost more. §8b is how to read the real cost.

The value must be numeric. `"false"` does not parse.

Embeddings share the image and take the oneDNN path (F16 KV) but the
key space is `--ctx-size 512`. Working set stayed flat (~365 MiB over
days). No pin there.

### Lift the pin only when

The pinned tag bounds `cache` in `fattn-onednn.cpp` (a capacity or LRU,
or keys that no longer include KV length). Confirm in that tag's
source. Then remove the env var and the requirement in
`scripts/ci/vllm-fa-onednn-test.py` in one change, and repeat §8a.
On every image bump, confirm `ggml-sycl` still reads
`GGML_SYCL_FA_ONEDNN`. A rename leaves the leak on with CI still green.
`VLLMMemoryRetainedAboveBound` is the detector for that blind spot.

## Alerts

`VLLMMemoryRetainedAboveBound`: 3h minimum of working set above **8 GiB**
for **1h**, AND the live instant series on
`(namespace, pod, container)`.

Retained ceiling is ~7338 MiB: baseline ~1232 + `--cache-ram` 4096 + one
slot's checkpoints ~2010 (32 x 62.8 MiB). The 8 GiB floor sits above
that ceiling. `scripts/ci/vllm-memory-alert-test.py` fails if
`--cache-ram` is raised without the threshold. A range selector keeps
samples after the pod is gone, so the live-series `and` is required.
Without it a terminated pod pages for up to 3h. After an in-place
container restart the still-live pod keeps its pre-restart floor for
the 3h window on purpose.

`VLLMMemoryExceedsRequest`: working set / memory **request** > 1 for
30m. Firing means the 12Gi sizing is wrong or something is leaking.
If the retained-memory alert is also firing, raising the request only
buys time.

`VLLMMemoryApproachingLimit` is `predict_linear` over 6h and does not
see a prefill spike. `VLLMMemoryCriticalLimit` is a static ratio > 0.85
for 15m. Do not replace the retained-memory rule with either.

Alert annotations name this file. A path change here has to update
`scripts/ci/vllm-memory-alert-test.py` in the same commit. The rendered
PrometheusRule diff should show only that annotation text.

## §8a. The leak has stopped

Read-only. After a rollout that touches this pin, wait until the pod
has served **at least 48h and at least 3M prompt tokens**. Then all
three:

1. Working-set floor plateaus at or under ~7.2 GiB after the prompt
   cache fills. It may rise once. It must not ratchet with traffic.

   ```promql
   min_over_time(container_memory_working_set_bytes{namespace="ai", pod=~"vllm-.*", pod!~"vllm-vllm-embed-.*", container="app"}[6h])
   sum(increase(llamacpp:prompt_tokens_total{namespace="ai"}[1d]))
   ```

2. Small anonymous mappings stop growing with requests. Pre-fix census
   was 3897 regions / 7794 MiB. After the cache has filled, counts move
   only by transients.

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

3. `VLLMMemoryRetainedAboveBound` and `VLLMMemoryExceedsRequest` stay
   quiet.

If the floor or the census keeps ratcheting with
`GGML_SYCL_FA_ONEDNN=0` set, the diagnosis is wrong. Reopen from the
smaps census, not from the request value.

## §8b. Prefill cost at our depths

`scripts/bench/vllm-prefill-by-depth.py` buckets prefill t/s by context
depth. It reads logs. It does not send load.

Pre-merge medians (oneDNN on, 6291 requests), `>= 512` new tokens /
`>= 2048` new tokens:

| Depth at end of prefill | n / median t/s (>=512) | n / median t/s (>=2048) |
|---|---|---|
| 0-32k | 161 / 471 | 75 / 1594 |
| 32-64k | 506 / 1017 | 312 / 1083 |
| 64-96k | 576 / 823 | 115 / 1053 |
| 96-128k | 276 / 727 | 31 / 945 |
| 128k+ | 92 / 617 | 2 / too few |

Compare a post-change window bucket by bucket at the same `--min-new`.
Trust buckets with a real `n`. The `>= 2048` rows are where the fallback
cost shows. Decode (`tg` in the same lines) should not move. Aggregate
prefill over the pre-merge pod's life was about 929 t/s
(`sum(rate(llamacpp:prompt_tokens_total[1d])) / sum(rate(llamacpp:prompt_seconds_total[1d]))`).
