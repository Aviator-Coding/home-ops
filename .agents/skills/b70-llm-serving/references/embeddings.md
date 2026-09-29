# Embedding correctness

`ai/embedding-gpu` and the CPU TEI server share one vector space
(mean cosine 0.9999992 over 12 probes, 1024 dimensions, L2-normalised).
Moving a caller between them does not need a re-index. That cosine is
**not** the NaN probe. A NaN vector can still have the right length.

## NaN with a green endpoint

The server returned 1024-wide vectors of `0xFFC00000` (quiet NaN from
`0.0/0.0`) for 202,582 tasks while `/health` was ok, slots released
cleanly, and logs had no error. Two health checks passed because they
counted non-zero elements. In Python `None != 0` is true. llama.cpp's
JSON library renders a non-finite float as bare `null`, so
`grep -q null` is the detector.

Rules:

- Assert `math.isfinite` and at least one non-zero component.
- Prove the assertion fails on a null-filled vector before trusting it.
- Do not use response length, shape, or a non-zero count.
- The shipped readiness probe embeds a fixed string and asserts values.
  Gate: `scripts/ci/embedding-values-test.py` (it executes the probe and
  is mutation-proven red).
- The probe script must not contain `$`. It is Flux-reconciled. Skill
  `flux-substitution`.

A local bisect of the pinned commit (~1850 requests) cleared
`--kv-unified`, `--parallel 2`, and `--cache-ram 0`. The cached GGUF
matched. The defect is GPU-side and still unattributed. Do not revert
`--kv-unified` to "fix" it: that halves `n_ctx_slot` to 256.

**Recovery is a pod restart.** The fault is accumulated runtime state,
not present from boot. One production recovery was a probe-driven
SIGTERM (`exitCode 0`, `reason Completed`), not an OOMKill (`137` /
`OOMKilled`).

## Large-batch OOM (still open)

`batch_size=4` completed a 154,715-node backfill with zero GPU failures.
Larger batches OOM the pod. That fault is separate from NaN drift and
from the host prompt cache. Do not raise the client's batch to chase
throughput, and do not send vmcp's 449-text catalog here (skill
`ai-stack`).

## What the limit is not

Request 1024Mi, limit 4Gi, `priorityClassName: embedding-gpu-low`.
The 4Gi limit did not stop the prompt-cache OOM (the default cache is
8192 MiB). `--cache-ram 0` is the bound. Steady state with the cache
off is near the idle baseline (~0.4 GiB). 4Gi is headroom so a new
growth path alerts instead of crash-looping. Do not raise the request
without redoing the talos-3 arithmetic (skill `node-scheduling`). The
request is what the scheduler reserves. The limit is not.

Strategy is `Recreate`: the old pod must release the GPU before the new
one claims it. `devic.es/b70` will schedule both.

## Sysfs

Inside the container `/dev/dri/card0` is the B70 and
`/sys/class/drm/card0` is the host iGPU `0xa7a0`. Read
`/sys/bus/pci/devices/0000:03:00.0`. The `xe` driver publishes no VRAM
counters. Skill `intel-gpu` for what Prometheus can see.
