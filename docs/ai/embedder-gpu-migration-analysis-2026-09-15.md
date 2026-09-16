# Moving the embedder onto the Arc Pro B70

> **Date:** 2026-09-15. **Card:** single Intel Arc Pro B70, 32656 MiB, `talos-3`, PCI
> `0000:03:00.0`, device `0xe223`, driver `xe`.
> **Outcome:** shipped. `ai/embedding-gpu` serves `Qwen/Qwen3-Embedding-0.6B` from the B70.
> **Companion evidence:** [`b70-llm-serving-tuning.md`](./b70-llm-serving-tuning.md) sections 4 and 6,
> [`../talos-3-scheduling-truth.md`](../talos-3-scheduling-truth.md),
> [`../ai-system/embedding-truncation-followup-2026-09-14.md`](../ai-system/embedding-truncation-followup-2026-09-14.md).

Everything below was measured against the live cluster on 2026-09-15 unless it says otherwise.
The live LLM (`ai/vllm`) was never touched: `restarts=0` and an unchanged `startTime` of
`2026-09-15T00:47:01Z` across the whole exercise.

## The three questions, answered in order

**Does it work?** Yes, and the vectors are provably the same ones the CPU server produces
(mean cosine **0.9999992**). It also fixes a defect: over-length input now fails loud instead
of being silently truncated.

**How much faster?** At batch 32, **190.9 embeddings/sec against the CPU path's 2.1** - about
**91x**. The 154,715-node backfill goes from ~8 hours to **under 10 minutes** unthrottled, or
**~2.6 hours throttled to a rate that leaves chat at 99% of its idle speed**.

**Can we trust the output?** Yes, on the evidence in section 4 - which was collected
specifically because a fast embedder returning plausible garbage is the worst available
outcome, and because there is a documented SYCL regression on sibling Arc silicon that
produces exactly that.

## 1. Corrected VRAM figures

**These replace an earlier reading that was wrong.** The `--gpu-memory-utilization=0.20` and
"~5GB VRAM" strings in `kubernetes/apps/base/ai/vllm/app/helmrelease.yaml` belong to the
**`vllm-embed` controller, which is `replicas: 0` and has no pod**. The live chat server is the
`vllm` controller and it is **llama.cpp, which has no such flag at all**. The card is not
mostly free.

Read directly off the Level Zero device at the moment a second workload attached to the card:

```
SYCL0 : Intel(R) Arc(TM) Pro B70 Graphics (32656 MiB, 5747 MiB free)
```

| | MiB | share |
|---|---:|---:|
| card total | 32656 | 100% |
| held by the live LLM | **26909** | **82.4%** |
| free | **5747** | 17.6% |

That is the number to use. For comparison, the figure derived from the 2026-09-07 documented
breakdown (20.10 weights + 2.66 KV + 0.25 recurrent + 1.73 compute = ~24.7 GiB) implied ~7.2 GiB
free; the live reading is ~1.6 GiB tighter, so even the careful doc-derived estimate was
optimistic.

### What the embedder adds, and why `-ub 512`

llama.cpp reports its own projection before allocating. Both configurations were deployed and
measured on the live card:

| config | model buf (SYCL0) | compute buf | **total** | **leaves free** |
|---|---:|---:|---:|---:|
| `-ub 2048` | 1136.48 MiB | 1224.96 MiB | **3257 MiB** | 2490 MiB |
| **`-ub 512` (shipped)** | 1136.48 MiB | 300.74 MiB | **1493 MiB** | **4254 MiB** |

The KV buffer reads **0.00 MiB** in both. **RETRACTED 2026-09-16 - see section 10.**
That line is the *reserve* pass of a two-pass KV init; the same banner goes on to print
`llama_kv_cache: size = 56.00 MiB ( 512 cells, 28 layers, 2/1 seqs)` and then
`CPU KV buffer size = 56.00 MiB`. There **is** a KV cache, 112 KiB per token, and it is the
unit the host prompt cache serialises - i.e. the mechanism behind the second OOM.
A further 296.23 MiB of the
model sits in host RAM, not on the card.

`-ub 512` was chosen because it costs nothing measurable. At the real input length (~31 tokens,
the live TEI server's lifetime mean) throughput is the same within noise - 190.9 vs 176.4 emb/s
at batch 32, 269.2 vs 280.7 at 4 concurrent - so the 1764 MiB is bought back for free.

**Why that margin matters, concretely:** if `ai/vllm` ever restarts while the embedder is
resident, it must still fit. It needs 26909 MiB. At 1493 MiB resident it restarts with
**4254 MiB to spare**; at 3257 MiB that margin is 2490 MiB. Both work, so this is margin, not
a rescue - but on a card already at 82.4% the wider one is worth having for free.

## 2. Serving path: one candidate was eliminated by deploying it

| path | verdict |
|---|---|
| **TEI `xpu-ipex-latest`** | **Deployed on this card and FAILED.** It is not a config error: the router starts, downloads the weights in 40s, then `Starting Python backend` -> `ERROR Could not start Python backend: Python backend failed to start` -> `Error: Could not create backend`. The IPEX build inside the image predates B-series silicon; our card reports `0xe223`. |
| **vLLM (`intel/llm-scaler-vllm`)** | **Rejected without deploying.** It pre-allocates `--gpu-memory-utilization` as a fraction of the card's **total** memory, so even the existing `0.20` would claim 6.4 GiB of the 5747 MiB actually free. It also needs a wrapper to present a TEI-shaped endpoint. |
| **llama.cpp SYCL** | **Shipped.** Already the proven stack on this exact card, so it reuses a known-good image tag (`server-intel-b10820`, the same one `ai/vllm` runs). Qwen publishes an official GGUF and an official command line. |

## 3. Throughput

Method: batch `POST /v1/embeddings`, median of 3 runs per cell, after warmup (the first
request of a cold server is several times slower and was discarded - it is a real effect worth
knowing, not noise to hide). `MED` is ~31 tokens, matching the live TEI server's lifetime mean
input length (836,596 tokens / 26,588 requests = 31.5), so it represents the actual corpus
rather than a flattering short string.

### Like-for-like against the CPU path

| | CPU (prior measurement) | **GPU (this change)** | speed-up |
|---|---:|---:|---:|
| batch 8, s/request | 1.5 s | **0.053 s** | **28x** |
| batch 8, embeddings/sec | 5.3 | **107.1** | **20x** |
| batch 32, s/request | 15.4 s | **0.171 s** | **90x** |
| batch 32, embeddings/sec | 2.1 | **190.9** | **91x** |
| parallel requests | **no benefit** | **+41%** (190.9 -> 269.2 at 4 concurrent) | - |

The parallelism row is the one that changes how the backfill should be written: on CPU the
server was effectively single-threaded so concurrency bought nothing, and on the GPU it is worth
about 41%. Past 4 concurrent it flattens (263.2 at 8), so **4 concurrent batch-32 requests is
the efficient shape**.

### Throughput varies with input length - quote the right row

| input | tokens | batch 32, embeddings/sec |
|---|---:|---:|
| SHORT | 13 | 246.5 |
| **MED (representative)** | **31** | **190.9** |
| LONG | ~190 | 53.9 |

Larger client batches do not help much beyond 32 at this input length: 64 -> 197.2,
128 -> 201.8, 256 -> 234.8 embeddings/sec.

### Extrapolation to 154,715 nodes

Sample size is 3 runs per cell over a ~100s window per configuration, on a server whose
counters confirmed no other traffic. The extrapolation assumes the corpus resembles MED; if the
real graph nodes are closer to LONG, divide by ~3.5.

| how it is run | embeddings/sec | **154,715 nodes** |
|---|---:|---:|
| unthrottled, chat idle | ~269 | **~9.6 minutes** |
| throttled 4 req/s | 127.6 | ~20 minutes |
| throttled 2 req/s | 65.0 | ~40 minutes |
| throttled 1 req/s | 32.6 | ~1.3 hours |
| throttled 0.5 req/s | 16.8 | ~2.6 hours |
| **CPU, for reference** | 2.1-5.3 | **~8 hours** |

## 4. Correctness

A fixed 12-text probe set: 4 known-similar pairs (paraphrase / same referent) and 4
known-dissimilar pairs built from the **same** left-hand sentences, so a degenerate embedder
that scores everything alike is caught by the contrast rather than by an absolute threshold.

**Not degenerate:**

| check | result |
|---|---|
| dimension | **1024** (as Qwen3-Embedding-0.6B should give) |
| NaN / Inf | none |
| all-zero vectors | 0 of 12 |
| distinct vectors | **12 of 12** |
| L2 norms | all exactly 1.0000 |

**Semantically sane** - similar pairs must rank above dissimilar ones, and they do, with a
clear gap and no overlap:

| | similar pairs | dissimilar pairs |
|---|---|---|
| values | 0.7256, 0.7941, 0.7552, 0.8077 | 0.2156, 0.3562, 0.2177, 0.1652 |
| mean | **0.7706** | **0.2387** |
| worst case | min similar **0.7256** | max dissimilar **0.3562** |

`min(similar) > max(dissimilar)`, so the two classes are **fully separated**.

**Consistent with the CPU path - and this is the strongest result here.** Both servers run the
same weights, so a correct GPU implementation should not merely rank the probes the same way,
it should land on nearly the same vectors. It does:

```
cos(GPU vector, CPU TEI vector) = 0.9999992 mean over 12 probes   (min 0.9999992)
L2 distance on unit vectors     = 0.00124
```

The residual is fully explained by f16 on the GPU against TEI's `model_dtype: float32`. The two
were verified to be genuinely different servers (llama.cpp reporting the GGUF path and TEI
reporting `version 1.9.4`), and the vectors are not bit-identical - so this is agreement, not an
accident of pointing both probes at one endpoint.

**Consequence: this is not a re-index event.** The GPU and CPU endpoints share one vector space,
so a caller can move between them freely. The 384 -> 1024 dimension break people may remember is
the **2026-09-13 model swap** (PR #1681), which had already landed before this work started.

### Two traps checked and closed

**The EOS trap is stale - do NOT append `<|endoftext|>` by hand.** Qwen3-Embedding uses
last-token pooling, and upstream advice (`ggml-org/llama.cpp#14234`, closed) was to append the
EOS token manually. Current llama.cpp already does it: appending it by hand measured **worse**
agreement with TEI (0.9910, min 0.9777) than sending raw text (0.9999992). Manual EOS
double-appends.

**Over-length input now fails loud, which is a genuine defect fix.** The CPU TEI server has
`auto_truncate: true` and its OpenAI-compatible route hardcodes it with no per-request override,
so an over-length input returns `200 OK` with a silently truncated vector - the defect
[`embedding-truncation-followup-2026-09-14.md`](../ai-system/embedding-truncation-followup-2026-09-14.md)
concluded could not be closed on TEI at any affordable setting. llama.cpp returns:

```
HTTP 400  {"error":{"code":400,"message":"request (1952 tokens) exceeds the available
context size (512 tokens), try increasing it","type":"exceed_context_size_error",
"n_prompt_tokens":1952,"n_ctx":512}}
```

The input ceiling also rises from **384 to 512** tokens, so this is not a capability regression.

## 5. The sustainable-rate curve

The previously known figure was a **38x** chat collapse under a sustained flood at ~17 req/s.
That is one point. Here is the rest of the curve, measured by driving the embedder at a
controlled rate while sampling chat decode with a bounded fixed probe (`n_predict=48`,
`temperature=0`, `cache_prompt=false`, reading the server's own
`timings.predicted_per_second` - the method [`b70-llm-serving-tuning.md`](./b70-llm-serving-tuning.md)
specifies). Requests are batch-32. All embedding load stopped between points so no row inherits
the previous row's contention.

| offered rate | achieved | embedding | **chat decode** | chat vs idle | 154,715 nodes |
|---|---:|---:|---:|---:|---:|
| **idle** | 0 | 0 | **83.65 t/s** | 100% | - |
| **0.5 req/s** | 0.53 | 16.8 e/s | **82.91 t/s** | **99%** | ~2.6 h |
| 1 req/s | 1.02 | 32.6 e/s | 58.62 t/s | 70% | ~1.3 h |
| 2 req/s | 2.03 | 65.0 e/s | 44.90 t/s | 54% | ~40 min |
| 4 req/s | 3.99 | 127.6 e/s | 13.41 t/s | 16% | ~20 min |
| 8 req/s | 4.02 | 128.7 e/s | **1.83 t/s** | 2% (**aborted**) | - |
| saturated | 4.12 | 131.8 e/s | 13.40 t/s | 16% | - |
| **idle again** | 0 | 0 | **83.43 t/s** | **100%** | - |

**The answer to "does he need a special window": no, if he throttles.** At **0.5 req/s the
backfill takes ~2.6 hours and chat is at 99% of idle** - still three times faster than the CPU
path, at no cost to chat. At 1 req/s it is ~1.3 hours for a 30% chat slowdown. The knee is
between 2 and 4 req/s; past that chat is unusable.

Three things worth knowing about this table:

- **The degradation is fully reversible.** The last row is the same idle probe after the worst
  point, and chat returned to 83.43 t/s. Nothing is left degraded.
- **Duty cycle matters, not just throughput.** The 4 req/s and 8 req/s rows achieved the *same*
  embedding throughput (127.6 vs 128.7 e/s) but chat differed 7x (13.41 vs 1.83 t/s). At 8 req/s
  the queue is never empty, so chat gets no gaps to slot into. **Throttle the backfill by rate,
  and do not simply hand it unlimited concurrency.**
- **The 83 t/s idle figure is this instrument's reference, not a contradiction of the
  documented ~61 t/s.** The probe is a 4-token prompt with a 48-token completion, the cheapest
  possible decode. Every row uses the same probe, so the ratios are what matter.

## 6. Idle safety

The endpoint ships **idle-safe**, and "idle" is the normal state: the only steady consumers are
ToolHive's tool-selection index (which stays on CPU, see section 7) and the `embedding-external`
key, which still has no live consumer. What idle actually costs:

- **GPU compute: none measurable.** The `idle` and `idle again` rows above bracket the entire
  benchmark, and chat sat at 83.65 and 83.43 t/s - unchanged.
- **VRAM: 1493 MiB**, leaving 4254 MiB free and leaving room for `ai/vllm` to restart.
- **Host memory: this reading was wrong.** 839 MiB, measured identical at idle and under a
  4-concurrent flood (460m CPU), was read as proof host memory does not track load because
  the work happens on the card. Real sustained backfill traffic disproved that - see
  [section 9](#9-2026-09-16-the-shipped-memory-limit-was-undersized-and-the-fix-is-pinned-slots--a-wider-limit)
  for the corrected figures (cgroup peak 1.57 GiB, 18 OOMKills at the old 2Gi limit) and the
  fix (`--parallel 2` + `--kv-unified` + `limits.memory: 4Gi`).
- **`talos-3` capacity:** allocatable is 93,604 Mi, and talos-3's total committed memory
  oscillates in a band with ephemeral CI runner pods - measured live 88,002 Mi (quiet, 1
  runner) to 92,610 Mi (busy, 9-10 runners), the same busy state
  [`../talos-3-scheduling-truth.md`](../talos-3-scheduling-truth.md) already measured as
  92,926 Mi. Sizing this pod against a single instantaneous reading would be wrong in either
  direction; what matters is the **permanent (non-CI-runner) headroom**, which that document
  is canonical for. The shipped request is **1,024 Mi**, which the canonical doc's section 7
  accounts for against that permanent headroom (6,489 Mi -> 5,465 Mi, roughly two fewer CI
  runner slots) - see that section for the full arithmetic and for why this does not put
  `ai/vllm` at any added risk. The pod also ships at the lowest priority on the node
  (`priorityClassName: embedding-gpu-low`, `preemptionPolicy: Never`), so it yields under
  pressure rather than displacing anything.
- **The LLM's Helm release is never touched.** This ships as its own HelmRelease rather than a
  third controller inside `ai/vllm`, because that release owns the live chat server and uses
  `strategy: Recreate` with a ~21 GB model load - any pod-template churn there is a
  multi-minute outage.

## 7. What ships, and what deliberately does not

**Ships:** `ai/embedding-gpu`, and the LiteLLM alias plus the agentgateway `embedding-local`
backend repointed at it. The alias is **renamed `embedding-local-cpu` -> `embedding-local`**,
since it is no longer on a CPU and a neutral name survives the next move. The gateway routes
embeddings on "not a vendor slug", not on an exact id, so a caller still sending the old name
reaches the same backend.

**Does not ship: the CPU TEI server is kept**, narrowed to ToolHive's tool-selection index. This
is not a shortcut - it cannot follow:

1. `VirtualMCPServer` binds it by `embeddingServerRef.name`, a **CR reference, not a URL**.
2. ToolHive's vmcp client is a **TEI** client: it calls TEI's native `/embed` and hardcodes
   `Truncate: true`. llama.cpp implements neither that endpoint nor that schema.
3. The `EmbeddingServer` CRD's `resources` block accepts **only `cpu` and `memory`** - there is
   no field for an extended resource, so `devic.es/b70` cannot be requested through it at all
   (checked against the live CRD).

That workload is ~959 vectors rebuilt in memory on vmcp restart, so CPU is right for it anyway,
and since both servers produce the same vectors nothing downstream has to know which answered.

**The backfill did not run.** Producing the 154,715 embeddings is a separate operation.

## 8. A trap found on the way

**Inside a `devic.es/b70` container, `/dev/dri/card0` is the B70 but `/sys/class/drm/card0` is
the iGPU.** The device plugin re-exposes the card's *device node* at `card0`/`renderD128`;
sysfs is not renamed, so `/sys/class/drm/card0/device/device` reads `0xa7a0` (Raptor Lake-P
iGPU), not the B70's `0xe223`. Anything deriving a sysfs path from the device-node name reads
the wrong card. Use `/sys/bus/pci/devices/0000:03:00.0`. This is a third instance of the same
device-node-rename hazard already documented for VA-API.

Separately, the `xe` driver exposes **no** VRAM counters in sysfs at all, and this cluster has
no Level Zero GPU exporter, so the only way to read GPU memory is from a process that attaches
to the card - which is why the figures in section 1 come from llama.cpp's own device banner.

## 9. 2026-09-16: the shipped memory limit was undersized, and the fix is pinned slots + a wider limit

> **This section's fix DID NOT HOLD, and parts of its reasoning are retracted.** The pod went
> straight back to CrashLoopBackOff against the raised 4Gi limit. The real cause was never
> slot-buffer high-water marks - it is llama.cpp's host prompt cache, whose default ceiling
> (8192 MiB) is larger than any limit that fits on talos-3. **Read section 10 before acting on
> anything here.** Section 9 is kept as the record of a wrong diagnosis, not as guidance.

**The 839Mi RSS figure in section 1 and the shipped `helmrelease.yaml` was measured wrong, not
just conservatively.** Under real sustained backfill traffic the pod was OOMKilled **18 times**
against the shipped `limits.memory: 2Gi` (`requests.memory` stayed `1024Mi` throughout - only
the limit was wrong). `lastState` on one kill: `reason: OOMKilled, exitCode: 137`, the incarnation
having lived only 94 seconds (`startedAt` 06:35:16Z, `finishedAt` 06:36:50Z, 2026-09-16). Read
straight off the cgroup on the next (healthy, still-running) incarnation, with the captain's
backfill agent already stopped - i.e. under **less** than full load:

```
memory.peak    1,680,678,912 B   = 1.57 GiB = 78.3% of the old 2Gi limit
memory.current 1,558,724,608 B   = 1.45 GiB = 72.6% of the old 2Gi limit
memory.max     2,147,483,648 B   = 2Gi (confirms the shipped limit)
```

### Why the original benchmark missed it

Section 1's throughput benchmark measured RSS at **839Mi, "identical idle and under a
4-concurrent batch-32 flood"** and concluded host memory does not track load - the GPU holds the
work, so why would it. That conclusion does not survive the live evidence: RSS observed in
production on an otherwise near-idle incarnation (one logged request in 5h51m of uptime) sat at
1.45-1.57 GiB, roughly **double** the benchmark figure. The likely mechanism, not fully isolated
because isolating it needs exactly the sustained instrumented load this fix is not allowed to
run: llama.cpp/SYCL host-side memory is a **high-water mark that is not returned to the OS**
between bursts (ordinary glibc-arena behaviour under concurrent allocation/free from multiple
slot threads). The original benchmark's "idle" and "under load" readings were taken back-to-back
in the same process lifetime without a restart between them, so both readings were already
sitting on whatever the arena had ratcheted up to from earlier warmup requests in that same
session - "identical" is exactly what a retained high-water mark looks like, not evidence that
load doesn't matter. Real production traffic is longer-running and more varied (up to the full
512-token ceiling, not just the ~31-token MED probe) than a ~100s synthetic window, so it had far
more opportunity to ratchet the mark upward before the limit was reached.

### The fix

Three changes, in `kubernetes/apps/base/ai/embedding-gpu/app/helmrelease.yaml`:

1. **`--parallel 2`**, pinned down from the auto-selected `n_slots = 4` (confirmed live from the
   startup banner: `load_model: initializing, n_slots = 4, n_ctx_slot = 512, kv_unified = 'true'`
   - llama.cpp does not print this unless the server logs it at startup, so it cannot be read off
   the args list alone). Fewer concurrent slots directly bounds how many simultaneous per-slot
   host-side batch/tokenisation buffers can be resident at once, which is the actual OOM driver -
   VRAM is not. (**The parenthetical that stood here - "the KV buffer is 0.00 MiB, no KV cache
   at all" - is RETRACTED; see section 10.** There is a 56 MiB / 512-cell pool. `--parallel 2`
   remains correct, but on measured grounds: the banner reports `n_slots = 2,
   n_ctx_slot = 512, kv_unified = 'true'`.) This is deliberately
   **different** from `ai/vllm`'s "do NOT pin `--parallel`/`--kv-unified`" rule
   (`docs/ai/b70-llm-serving-tuning.md` section 3): that rule exists because pinning mis-sizes the
   shared `kv_unified` KV-cache pool on `b9592`, collapsing chat decode to ~0.5 t/s. There is no
   KV pool here to mis-size, so that failure mode cannot apply.
2. **`--kv-unified`**, added alongside it after a local test caught a regression the first cut of
   this fix missed. llama.cpp only defaults `kv_unified=true` when `n_slots` is auto (`-1`) - once
   `--parallel` pins the slot count, that default silently flips off. Confirmed by pulling the
   exact pinned image (`ghcr.io/ggml-org/llama.cpp:server-intel-b10820`) and running the real
   Qwen3-Embedding-0.6B-GGUF model locally: `--parallel 2` alone reported `n_ctx_slot = 256` at
   startup - half of `--ctx-size 512`, and **below** the CPU TEI server's 384-token ceiling this
   endpoint is supposed to match, which would have been a real capability regression despite the
   unchanged `--ctx-size 512`. Adding `--kv-unified` restored `n_ctx_slot = 512` with `n_slots`
   still pinned at 2. Safe for the same reason (1) is safe: no shared KV pool exists here to
   mis-size (**retracted - see section 10**; the pool exists and is correctly sized here, which
   the banner shows directly), so `ai/vllm`'s pinning warning does not transfer.
3. **`limits.memory: 2Gi -> 4Gi`**. Sized to sit well above the highest confirmed real peak
   (1.57 GiB), not merely above it: 4Gi leaves **2.43 GiB (61%) of headroom** over that peak, i.e.
   the peak is only 39% of the new limit. This is deliberately generous rather than a tight
   recompute, because the peak that was measured came from **partial** load at the **old**
   4-slot concurrency - a defensible tighter number for 2-slot full saturation is not available
   without running the load test this fix is forbidden from running (see below).

**What did NOT change, and why:**

- **`requests.memory` stays `1024Mi`.** Requests drive talos-3's scheduling arithmetic
  (`../talos-3-scheduling-truth.md` section 7); raising only the limit does not touch it. Real
  usage now provably exceeds this request under load, which puts the pod in the kubelet's
  usage-exceeds-request eviction set - that is this pod's **intended** failure mode:
  `priorityClassName: embedding-gpu-low` (`value: -10`, `preemptionPolicy: Never`) makes it the
  lowest-priority pod on the node, and priority is the kubelet's tiebreaker among exceeds-set pods
  (`../talos-3-scheduling-truth.md` section 1) - so it is always evicted before `ai/vllm` or
  anything else regardless of how far over its request it sits. Moving the request would need the
  section 7 arithmetic redone; nothing measured here shows that is warranted.
- **`priorityClassName`/`preemptionPolicy` are untouched.** This is the property that made 18
  embedder OOMKills cost `ai/vllm` nothing (`restarts=0` across the same 35-hour window).
- **`--ctx-size`/`-b`/`-ub` stay `512`.** Unrelated to this failure - that triple governs the
  VRAM-side compute buffer and the input-length ceiling (section 1), not host RSS.

**What this does NOT prove, and what would:** this was fixed from configuration and the cgroup
peak, not from a new load test - the brief this fix shipped under forbids driving sustained load
at the embedder, because a sustained flood measurably degrades the captain's live chat model
(`docs/ai/b70-llm-serving-tuning.md` section 4). So neither "`--parallel 2` is sufficient
concurrency" nor "4Gi is enough under full sustained saturation" is validated - only that the
previous configuration provably was not. **The first real sustained backfill run under this
config is the actual test**: watch `kubectl get pod -n ai -l app.kubernetes.io/name=embedding-gpu
-o wide` for restarts, and read `memory.peak` off the cgroup (`kubectl exec ... -- cat
/sys/fs/cgroup/memory.peak`) during and after it. If it OOMs again at 4Gi, the next move is
`--parallel 1` before raising the limit further - the limit was already widened well past the
highest confirmed peak once.

## 10. 2026-09-16 (second pass): the real cause is llama.cpp's host prompt cache

Section 9's fix did not hold. Within hours of shipping `--parallel 2` + `--kv-unified` +
`limits.memory: 4Gi`, `ai/embedding-gpu` was back in **CrashLoopBackOff, 5 restarts in 30
minutes, still `reason: OOMKilled, exitCode: 137`** - now against the raised 4Gi limit. One
incarnation lived **106 seconds** (`startedAt` 19:52:15Z, `finishedAt` 19:54:01Z).

### The shape of the growth rules out everything section 9 proposed

`container_memory_working_set_bytes` for the container, 30s step, across the load window:

```
19:09-19:39   0.397 GiB   idle, flat for 30 minutes
19:39:58      0.683 GiB   \
19:41:28      0.683 GiB   / flat for 90s while idle
19:42:28      1.317 GiB
19:42:58      2.747 GiB   -> OOMKilled
```

and the same curve on every subsequent incarnation (`0.39 -> 1.75 -> 3.32`, `0.41 -> 1.66 ->
2.92`, `0.38 -> 1.54 -> 3.39`). Three things follow immediately:

- **It is not a high-water mark that fails to return to the OS** (section 9's hypothesis). A
  ratchet does not reset; this resets to 0.397 GiB on every restart and climbs only while
  requests arrive, flat otherwise.
- **It is not request size.** The last slot releases before the kill logged `n_tokens = 25` and
  `n_tokens = 92`, at task ids around 3,567.
- **It is request volume.** ~3,569 tasks in 106 seconds. A bigger ceiling buys proportionally
  more requests and nothing else - which is exactly what 2Gi -> 4Gi bought.

### The cause

llama.cpp keeps a **host-RAM prompt cache**, and its default ceiling is **8192 MiB** - twice the
container's 4Gi limit. Read straight off the pinned image's own `--help`
(`ghcr.io/ggml-org/llama.cpp@sha256:3503755...`, tag `server-intel-b10820`):

```
-cram, --cache-ram N   set the maximum cache size in MiB
                       (default: 8192, -1 - no limit, 0 - disable)
--cache-idle-slots, --no-cache-idle-slots
                       save idle slots to the prompt cache on new task, and clear
                       them when using unified KV (default: enabled, requires cache-ram)
```

Because that ceiling exceeds the cgroup limit, **`server_prompt_cache::update()` can never prune
before the kernel kills the process**. Nothing else bounds it either:

- The token-based cap is not a second line of defence: `update()` *recomputes* it from the byte
  limit (`limit_tokens_cur = limit_size / size_per_token`), which a local run showed resolving to
  **74,873 tokens** against a nominal `limit_tokens` of 512.
- The only adaptive guard is a `std::bad_alloc` handler in `server_prompt_cache::alloc` that cuts
  `limit_size` to 40% on allocation failure. Under a **cgroup** memory limit that handler is
  **structurally unreachable**: the kernel SIGKILLs at page-fault time and `malloc` never fails.
  That is why the pod dies at exit 137 rather than logging `failed to allocate memory for prompt
  cache state`.

**So no value of `limits.memory` can fix this.** It is not a ceiling that was too low; it is a
workload whose only self-limiting mechanism is keyed to a signal cgroups never deliver.

### On an embedding server that cache is write-only

This is the part that makes the fix free rather than a tradeoff. In
`tools/server/server-context.cpp` at this build:

- **Reads** happen at exactly one call site - `prompt_load`, line 1649 - inside a block gated on
  `update_cache = update_cache && task.type == SERVER_TASK_TYPE_COMPLETION` (line 1639).
- **Writes** happen via the `--cache-idle-slots` path at line 2420, which carries **no task-type
  guard at all** and so fires on every task launch.

Embedding requests are `SERVER_TASK_TYPE_EMBEDDING`. They can therefore never hit the read path,
while hitting the write path on every request. Every entry the backfill paid for was one nothing
could ever read. `alloc()` dedups only on prefix-containment, so a stream of distinct documents
never dedups either.

### What each entry costs

Measured locally on the pinned digest with the exact production arg set:

```
srv prompt_save:  - saving prompt with length 28, total state size = 3.063 MiB
srv      update:  - cache state: 1 prompts, 3.063 MiB (limits: 8192.000 MiB, 512 tokens, 74874 est)
srv prompt_save:  - saving prompt with length 23, total state size = 2.517 MiB
srv      update:  - cache state: 2 prompts, 5.580 MiB (limits: 8192.000 MiB, 512 tokens, 74872 est)
srv prompt_save:  - saving prompt with length 28, total state size = 3.063 MiB
srv      update:  - cache state: 3 prompts, 8.644 MiB (limits: 8192.000 MiB, 512 tokens, 74873 est)
```

An entry is the document's **full KV state**: 28 tokens x 112 KiB = 3.0625 MiB, matching the
logged 3.063 MiB exactly. The 112 KiB/token comes straight off the banner -
`llama_kv_cache: size = 56.00 MiB ( 512 cells, ...)`, i.e. 56 MiB / 512 cells. At the live
workload's ~31-token mean that is **~3.4 MiB of host RAM per document**, so **~1,200 documents
fill 4Gi** - seconds of backfill, monotonic, never pruned.

**This also retracts section 1's "the KV buffer is 0.00 MiB, embedding has no KV cache".** That
read the reserve pass of a two-pass init. The full local banner:

```
llama_kv_cache:        CPU KV buffer size =     0.00 MiB     <- reserve pass
llama_kv_cache: size =   56.00 MiB (   512 cells,  28 layers,  2/1 seqs), K (f16): 28.00 MiB, V (f16): 28.00 MiB
llama_kv_cache:        CPU KV buffer size =    56.00 MiB     <- actual
```

The false premise mattered: it was the stated justification for pinning `--parallel`/`--kv-unified`
in section 9 ("there is no KV pool to mis-size"). Those flags are still correct, but on **measured**
grounds - the banner reports `n_slots = 2, n_ctx_slot = 512, kv_unified = 'true'` - not on that
argument.

### The fix

One line in `kubernetes/apps/base/ai/embedding-gpu/app/helmrelease.yaml`: **`--cache-ram 0`**.

`0` rather than a small non-zero value, for three reasons:

1. It is the only setting that also force-disables the writer. Confirmed in the local banner:
   `srv init: --cache-idle-slots requires --cache-ram, disabling`.
2. It never constructs the cache at all, so there is no continuous allocate/free churn leaving a
   retained allocator arena behind - the failure mode a small cap would still risk.
3. The cache is write-only here, so a non-zero cap would buy exactly nothing.

Verified locally against the pinned digest, request-matched, 2 concurrent:

| | requests | cache entries | cached bytes | container RSS |
|---|---|---|---|---|
| default (8192 MiB) | 6 | 3 | 8.644 MiB, growing | 1.422 -> 1.495 GB |
| `--cache-ram 0` | 6 | **0** | **none - cache never constructed** | 1.386 -> 1.458 GB |

**Read that RSS column honestly: at 6 requests it does not discriminate** (+73 MB vs +72 MB - the
8.6 MiB of cache is lost in qemu-emulation noise at that sample size). The discriminating local
evidence is the cache accounting itself, which is exact; the *magnitude* comes from the live
cluster curve above and the per-token arithmetic. A local run cannot reproduce the 34 req/s that
makes this fatal in minutes.

### The whole banner on the final arg set

Per the lesson from section 9 - where pinning `--parallel` silently halved `n_ctx_slot` and
nothing errored - the pinned image was run locally with the **exact** final argument set and the
entire startup banner re-read, not just the value being set:

```
print_info: pooling type          = 3
llama_context: n_seq_max             = 2
llama_context: n_ctx                 = 512
llama_context: n_ctx_seq             = 512
llama_context: n_batch               = 512
llama_context: n_ubatch              = 512
llama_context: kv_unified            = true
llama_kv_cache: size =   56.00 MiB (   512 cells,  28 layers,  2/1 seqs), K (f16): 28.00 MiB, V (f16): 28.00 MiB
srv    load_model: initializing, n_slots = 2, n_ctx_slot = 512, kv_unified = 'true'
srv    load_model: prompt cache is disabled - use `--cache-ram N` to enable it
srv          init: --cache-idle-slots requires --cache-ram, disabling
srv  llama_server: model loaded
```

`n_slots`, `n_ctx_slot`, `kv_unified`, `pooling type`, and the batch triple are all unchanged from
the intended values - `--cache-ram` re-derives nothing. (The local run is CPU-backend: the image's
SYCL backend aborts with `can not find preferred GPU platform` on a machine with no Arc card, so
it was suppressed by bind-mounting an empty file over `/app/libggml-sycl.so`. Slot, context and
cache accounting are backend-independent; only the buffer *placement* lines differ from
production.)

### What did not change, and why

- **`limits.memory` stays 4Gi.** With the cache off the expected steady state is near the
  0.397 GiB idle baseline, so 4Gi is now pure headroom - retained deliberately, so that if some
  *other* growth path exists it surfaces as an alert rather than a third crashloop. Lowering it
  buys the node nothing anyway, because limits do not enter talos-3's scheduling arithmetic
  (`../talos-3-scheduling-truth.md` section 7); requests do.
- **`requests.memory`, `priorityClassName`, `preemptionPolicy` are untouched.** This is the
  property that has made 23 embedder deaths cost the captain's language model nothing: `ai/vllm`
  and `ai/hermes` both sat at `restarts=0` throughout.
- **`--parallel 2` / `--kv-unified` / the `--ctx-size`/`-b`/`-ub` triple are untouched.** They are
  correct; only section 9's *justification* for the first two was wrong.

### `ai/vllm` has the same default and must NOT get the same fix

`ai/vllm` runs the **same pinned image and tag**, so it also carries the 8192 MiB default against
`limits.memory: 48Gi` with `requests.memory: 39Gi` - a much wider margin, but not an unlimited
one. It is deliberately **not** changed here, and `--cache-ram 0` would be the wrong answer for it:
it is a chat server, where the read path *is* `SERVER_TASK_TYPE_COMPLETION` and prefix reuse across
multi-turn conversation is genuinely valuable. Its answer is a bounded non-zero value, which is
tracked as its own decision.

### What the next sustained backfill proves

This fix removes a mechanism that was **proven** to grow without bound, by an instrument that
counts it exactly. What it does **not** prove is that no other growth path exists - the local
reproduction runs at a fraction of production's request rate, and no sustained load test was run
against the live card (a flood degrades the captain's chat decode up to 38x,
`docs/ai/b70-llm-serving-tuning.md` section 4).

**Section 9 made a prediction of this same shape and it was wrong**, so calibrate accordingly. The
difference in kind, not just in confidence: section 9 widened a ceiling against an unquantified
mechanism, while this removes a quantified one and CI now refuses to let it come back
(`scripts/ci/embedding-gpu-prompt-cache-test.py`).

The next real backfill is the test. Watch:

```
kubectl -n ai get pod -l app.kubernetes.io/name=embedding-gpu -o wide     # restarts must stay 0
kubectl -n ai exec deploy/embedding-gpu -- cat /sys/fs/cgroup/memory.peak # expect ~0.4-0.6 GiB
```

Expected: working set stays near the 0.397 GiB idle baseline and does **not** track request count.
If it OOMs again, the curve shape is the discriminator - a flat-then-climb that tracks volume means
another per-request retention path (capture `memory.peak` and the slot logs before restarting it);
a slow climb that plateaus is the arena high-water mark section 9 hypothesised, and *that* one is
legitimately a limit-sizing question.
