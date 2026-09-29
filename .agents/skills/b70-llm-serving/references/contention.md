# Two tenants, one card

`ai/vllm` and `ai/embedding-gpu` both request `devic.es/b70: 1`.
`count: 99` lets the scheduler place both. Nothing partitions compute
or VRAM. `tdarr-node` uses the same card through `devic.es/b70-vaapi`
(skill `intel-gpu`). ComfyUI was the previous co-tenant and is gone
(skill `ai-stack`); do not put it back beside chat.

Measured card: 32656 MiB total, chat holding 26909 MiB (about 82%).
The embedding server is sized to ~1493 MiB so a chat restart still has
a few GiB free. `--ub 2048` on the embedder fits and spends that margin.
There is no llama.cpp flag that reserves a fraction of the card.
`--gpu-memory-utilization` is the parked `vllm-embed` controller.

## Rate, not concurrency

Idle chat decode on this card is the reference the curve was measured
against (~84 t/s in that session, not a number to copy into a dashboard).

| Embedding rate | Chat decode left |
|---|---|
| 0.5 req/s | ~99% of idle. A 154,715-node backfill is on the order of a few hours |
| 1 req/s | ~70% (about a 30% cost) |
| 4 req/s | ~16% (about an 84% cost) |
| always-full queue | collapses to a few percent (~2 t/s) |

Degradation reverses when the burst stops. Idle embeddings cost nothing
measurable. At equal throughput a saturated queue cost chat about 7x
more than a paced one. Cap the **client's** request rate. Do not add a
LiteLLM rpm cap to get a clean 429: that shape was live-tested and hung
the caller, and it was not committed. The server-side bounds are
`--no-cont-batching` and `--timeout 30` (skill flags).

Continuous batching, left on, interleaves the whole queue and is what
steals the card. `--parallel 2` only caps slots.

## Chat stays llama.cpp

vLLM on this MoE OOMs during warmup (intel/llm-scaler#382). A second
card is not the fix for that. Skill `intel-gpu` for the deferral.
Buying one does not make this server a vLLM deployment.

TEI's XPU image was deployed on this card and could not create its
backend (`0xe223` is Battlemage; that IPEX build predates it). Do not
retry it as a drop-in.
