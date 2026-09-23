# `ai/vllm` host prompt cache: the 2026-09-19 memory leak and its fix

**Status:** fix shipped 2026-09-19; request right-sized 12Gi 2026-09-20 from a measured 17h post-fix
steady state (see [§4b](#4b-why-the-request-moved-and-the-limit-did-not)). A longer-window
reconfirmation is still a named follow-up - see [§6](#6-how-to-tell-whether-it-worked).

> **Correction 2026-09-22 - read [`vllm-onednn-sdpa-leak.md`](vllm-onednn-sdpa-leak.md) first.**
> The growth came back with both fixes below live and working. Most of it, before and after this
> fix, was a second, independent leak: llama.cpp's never-evicted cache of compiled oneDNN SDPA
> kernels. Its 0.4-8 MiB allocations sat inside the `[heap]` that §3 attributes to cache churn.
> Both fixes here stay correct and needed. What changes is the diagnosis in §3a/§3c. And the 17h
> "steady state" in §4b was a low-traffic window (0.95M prompt tokens), not proof of a plateau. The
> leak is fixed by `GGML_SYCL_FA_ONEDNN: "0"`.

Claims are labelled `[MEASURED]` (read off this cluster), `[CONSENSUS]` (documented upstream/vendor
behaviour), `[INFERENCE]` (reasoning from measured inputs). Source analysis: the 2026-09-19 fleet
memory report, sections 0, 2 (#1) and 3.

---

## 1. What was wrong

`ai/vllm`'s args contained **no `--cache-ram` at all** `[MEASURED]`, so it inherited llama.cpp's
host-RAM prompt-cache default of **8192 MiB**. Its host working set climbed monotonically from a
**1232 MiB** post-load baseline and never plateaued:

| | value | note |
|---|---:|---|
| post-load baseline | 1,232 MiB | `[MEASURED]` |
| growth rate | **+5,622 to +6,485 MiB/day** | `[MEASURED]`, does not decay |
| previous pod generation peak | **39,531 MiB** over 6.6 days | ended by the 2026-09-15 talos-3 reboot, not by anything working as designed |
| current pod, 2026-09-19T19:12Z | **26,760 MiB** | `[MEASURED]`, 4d18h old and still climbing |
| crosses the 39Gi request | 39,936 MiB | ~2026-09-21 |
| crosses the 48Gi limit → OOMKill | 49,152 MiB | ~2026-09-23 |

This is the captain's interactive chat model and the engine `ai/hermes` depends on, so the OOMKill
is a user-visible outage on both.

Crossing the **request** matters separately from crossing the limit: that is the moment the pod
re-enters the kubelet's exceeds-set and becomes an eviction candidate again - the exact exposure
the 2026-09-14 request raise (16Gi → 39Gi) existed to close. Nothing alerted on it at the time; the
two rules already in `prometheusrule.yaml` gate on the *limit* and fire ~1 day before the OOMKill.
That gap was closed 2026-09-20 by `VLLMMemoryExceedsRequest` (see §4b).

## 2. What it was not

**Not `--no-mmap`, and not the model.** This contradicts `docs/talos-3-scheduling-truth.md` §6,
which states dropping `--no-mmap` "is the lever that would shrink the footprint". It is not; it
would free approximately nothing. At load, sampled at 2-minute resolution `[MEASURED]`:

```
              00:48    00:50    00:52  ...  01:30
working set     227     1232     1232  ...   1232
rss             177      635      635  ...    635      <- never approaches 20 GiB
page cache   18,361   22,502   22,502  ...  22,502     <- the GGUF, file-backed
```

If `--no-mmap` held the 20.6 GiB of weights in anonymous memory, RSS would step to ≥20 GiB during
load and stay there. It stays at **635 MiB** while **22,502 MiB of page cache** appears.
llama.cpp's `--no-mmap` path `read()`s each tensor into a small reusable buffer and uploads it to
VRAM; the file contents land in the page cache, which is reclaimable and explicitly *excluded* from
`container_memory_working_set_bytes` (WSS = usage − inactive_file). **The weights are already
reclaimable, file-backed memory.** `--no-mmap` is not costing host memory - leave it alone.

## 3. What it was

### 3a. One enormous heap, against a control that has none

Aggregating `/proc/1/smaps` by backing store `[MEASURED]`:

| | `ai/vllm` (default 8192) | `ai/embedding-gpu` (`--cache-ram 0`) |
|---|---:|---:|
| `[heap]` | **17,107 MiB** (1 mapping) | **317 MiB** (1 mapping) |
| `[anon]` | 8,987 MiB (167 mappings) | small |
| model file, mapped | absent (`--no-mmap`) | 276 MiB |
| all shared libs | ~340 MiB | ~200 MiB |
| growth rate | **+5,622..+6,485 MiB/day** | **+8 MiB/day** |

Same image family (both on `server-intel-b10820`), same SYCL/Level-Zero runtime, same libraries,
same node. **A 54x heap difference and a ~700x slope difference**, whose only structurally relevant
delta is `--cache-ram`.

*Honest caveat:* this is a natural experiment, not a controlled one - embedding-gpu runs a much
smaller model with a tiny context and different traffic. It isolates `--cache-ram` only if nothing
else plausibly explains a 700x slope difference.

### 3b. The server says the cache is saturated and thrashing

`[MEASURED]` - 80 instances in one retained window (59 still present 2026-09-19):

```
W srv alloc: - making room for prompt cache entry, removing oldest entry (size = 1886.xxx MiB)
```

Entry sizes **191 → 1,886 MiB**, mean **860 MiB**, totalling **67.2 GiB evicted**. The cache is
permanently at its ceiling, churning huge variable-size allocations - precisely the allocation
pattern that fragments a glibc heap.

### 3c. Why the declared bound was not honoured `[INFERENCE + CONSENSUS]`

The cache's *logical* bound is 8192 MiB and llama.cpp evicts correctly to respect it. Yet RSS
reached 39,531 MiB against a 1,232 MiB baseline - 38,299 MiB of accumulation, **4.7x the declared
bound**, all in a single `brk()`-grown `[heap]`.

Mechanism `[CONSENSUS, glibc]`: glibc's main arena grows via `brk()` and can only be trimmed **from
the top**, so any live allocation near the top pins everything below it. glibc also **raises** its
dynamic `mmap_threshold` up to 32 MiB the first time an mmap'd block is freed; after that the
sub-32 MiB per-layer KV chunks composing a cache entry come from the heap and are never returned to
the OS. Freeing an 1,886 MiB entry and allocating a 200 MiB one strands the difference. Repeat 80+
times and you get a 17 GiB heap.

### 3d. The cgroup limit is not a backstop

llama.cpp's only self-limiting guard is a `bad_alloc` handler inside `server_prompt_cache::alloc`,
and a memory limit makes it **structurally unreachable**: the kernel SIGKILLs at page-fault time,
so `malloc` never fails. This is why `ai/embedding-gpu` OOMKilled **18x at 2Gi and again at 4Gi**
before the cause was found. Do not reach for the limit as a safety net here.

## 4. The fix

Both halves, in `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`. **Shipping one without the
other is the expected mistake** - §3c is why the cache bound alone need not shrink RSS, and a
smaller cache churns *more* often.

```yaml
args:
  - --cache-ram
  - "4096"
env:
  GLIBC_TUNABLES: glibc.malloc.mmap_threshold=131072
```

Setting the tunable explicitly **disables** glibc's dynamic raise and pins the threshold at 128 KiB,
so the large churning allocations are served by `mmap` - which *is* returned to the OS on `free`.
`MALLOC_MMAP_THRESHOLD_=131072` is the equivalent older spelling; the gate accepts either.

### 4a. Why 4096 and emphatically not 0

`ai/embedding-gpu` pins `--cache-ram 0` and that is correct **there**: its cache is write-only (the
read path, `prompt_load`, sits in a block gated on `SERVER_TASK_TYPE_COMPLETION`, while the
`--cache-idle-slots` writer carries no task-type guard). `AGENTS.md` states plainly that `ai/vllm`
"must not get the same fix... it needs a bounded non-zero value".

This is a chat server and the caching earns its keep `[MEASURED]`: mean **`f_keep` = 0.975 across
1,560 slot selections**, i.e. 97.5% of prompt KV is reused, on a workload that is **88% prefill
tokens** (the `-ub 2048` rationale in the HelmRelease header). Setting 0 would trade an OOM for a
large, permanent prefill regression.

### 4b. Why the request moved and the limit did not

The pre-fix 39Gi request / 48Gi limit were deliberately left at the leak's own values until a
*measured* post-fix steady state existed - lowering either earlier would have removed the headroom
that was, at the time, the only thing preventing an eviction (see §6 for why an early reading
cannot supply that measurement).

That measurement now exists: pod `vllm-5cb8f5f6f4-zmvqq`, 17h post-fix, zero restarts, peak 5,750
MiB, oscillating 4,577-5,750 MiB rather than climbing. On 2026-09-20 the **request was cut to
12Gi** from that measurement - sized on the mechanism (baseline + 2.7x the 4096 MiB `--cache-ram`
bound) rather than on the 17h peak alone, since prefill is 88% of tokens and a 17h window need not
contain the busiest hour. Full arithmetic: the `resources.requests.memory` comment in
`kubernetes/apps/base/ai/vllm/app/helmrelease.yaml`.

The **48Gi limit is unchanged** - a limit reserves no memory, and it stays high deliberately as
headroom against an unobserved prefill spike while only 17h of post-fix history exists.
`VLLMMemoryExceedsRequest` (`prometheusrule.yaml`) is the detector if 12Gi proves wrong. Node-level
consequences of the cut: `docs/talos-3-scheduling-truth.md` §8.

## 5. What is gated

`scripts/ci/vllm-prompt-cache-test.py`. `--cache-ram`'s absence is **silent** - removing or
mistyping it restores 8192 MiB with no error, no warning and no other CI signal - so this is a gate,
not a comment. It pins both halves and asserts *relationships*, not literals, so a future tuning
pass can change the numbers without a CI edit. Mutation-proven red on all seven violations:
flag removed, value `0`, value `-1`, value ≥ `limits.memory`, env removed, wrong tunable knob, and
a threshold pinned above glibc's 32 MiB dynamic ceiling (where the pin would be inert).

## 6. How to tell whether it worked

**Success:** host working set **plateaus** instead of climbing. Because the climb is ~6.5 GiB/day,
a flat working set over **24 hours** settles it.

**Do not claim success from a reading taken minutes after the roll.** The pod starts at ~1.2 GiB
*either way*, so an early sample cannot distinguish a fix from a failure - it only re-measures the
baseline. That distinction is the entire point of the exercise.

```sh
# host working set over the window (Prometheus)
container_memory_working_set_bytes{namespace="ai",pod=~"vllm-.*",container="app"}
```

**Failure, and it is immediate and unambiguous:** a too-small cache shows up as **falling prefill
tokens/sec and rising `prompt eval time`** in the server log, both already printed per request
(`tg`/`tg_3s` decode figures alongside). If that appears, the lever is to raise `--cache-ram`, not
to remove it.

The **~9-20 GiB** `[INFERENCE]` predicted here overestimated it: the measured 17h post-fix steady
state (§4b) peaked at **5,750 MiB**, oscillating rather than climbing - well under the inference,
which assumed the full cache bound stays resident continuously rather than being reused within a
session. That measurement is what the 2026-09-20 request cut (§4b) is sized against; a longer
window is still the named follow-up (§1 status line).

## 7. Related

- `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml` - the flags, with rationale inline
- `kubernetes/apps/base/ai/embedding-gpu/app/helmrelease.yaml` - the `--cache-ram 0` sibling and
  why its answer differs
- `docs/ai/embedder-gpu-migration-analysis-2026-09-15.md` §10 - the embedder's 18x/4Gi OOM history
- `docs/talos-3-scheduling-truth.md` - the node arithmetic any request change must re-derive
- `docs/ai/b70-llm-serving-tuning.md` §6 - the `-ub 2048` / prefill-dominance measurements
