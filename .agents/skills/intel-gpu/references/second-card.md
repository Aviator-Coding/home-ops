# Second B70: deferred

Do not buy one on the current facts.

Chat on this card is single-stream MoE decode on llama.cpp (on the order
of 55 t/s in the measurement that framed the decision). A second card
does not meaningfully raise that number. It is a concurrency or a
bigger-model purchase.

## Buy only if

1. Production commits to a model that does not fit in 32 GB (layer
   split, for example an 80B-class MoE) **and** talos-3 can seat the
   second card at PCIe x8 or wider. That is the one capability a second
   card adds that software cannot. The chassis question is still open:
   the current card enumerates only with `pci=realloc assign-busses`
   and sits on an OCuLink-style attachment (`0000:03:00.0`, 32 GB
   ReBAR). Nobody has confirmed a second OCuLink port, an M.2 adapter
   path, x8/x8 bifurcation, or power and cooling. Resolve that before
   any purchase. A x4 link changes tensor-parallel scaling.
2. Or vLLM-XPU prefill becomes a hard requirement **and**
   intel/llm-scaler#382 is closed (a newer `llm-scaler-vllm` tag, or a
   proven `gpu-memory-utilization` / `enforce_eager` / `--max-num-seqs`
   setup that hosts the 35B MoE). Until #382 closes this is a bet.
   Skill `b70-llm-serving`: chat stays llama.cpp because vLLM OOMs this
   MoE at warmup.

Isolation alone (a second card so embeddings or a future image app do
not touch chat) is a poor purchase. The rate cap on the embedding
client is the control that exists today.

A single-card vLLM-XPU dense model can raise aggregate batched
throughput. That metric is not single-stream MoE decode. It is a
software experiment, and #382 still blocks the MoE-on-vLLM path.

## What would reopen the buy

- #382 closes.
- A concrete need for a model above 32 GB.
- talos-3 confirmed to have a second high-bandwidth attachment.
- An XMX flash-attention kernel landing in llama.cpp, which would
  remove the prefill reason to want vLLM and weaken the case further.

The dock has its own PSU. A host power cycle does not power the card.
Skill `talos-nodes` for the attended power-on order and
`pcie_port_pm=off`. That kernel arg is not a substitute for dock-PSU-first.
