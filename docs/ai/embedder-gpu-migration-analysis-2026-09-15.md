# Embedder on the B70

The migration analysis moved to skill `b70-llm-serving` (`references/embeddings.md`, `references/contention.md`, `references/memory.md`).

`embedding-gpu` is the live embedder. Throttle it by request rate. `batch_size=4` is the measured-safe point; larger batches OOM and that is still open. A green health check can still return NaN vectors; that root cause is still open, and a restart clears the episode.
