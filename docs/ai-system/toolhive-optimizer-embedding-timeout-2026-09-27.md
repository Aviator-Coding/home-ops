# Hermes could not reach ToolHive (2026-09-14 to 2026-09-27)

## Symptom

Hermes' `toolhive` MCP server (`http://vmcp-mcp-gateway-internal.ai.svc.cluster.local:4483/mcp`)
failed on every connect:

```
Connecting to MCP server 'toolhive' timed out after 30s (bounded by connect_timeout; an OAuth login also by oauth.timeout)
```

In Hermes' runtime logs the same failure shows as
`Failed to connect to MCP server 'toolhive': CancelledError`, followed by
`MCP: registered 0 tool(s) from 0 server(s) (1 failed: toolhive ...)`.

## Cause

| role | what |
|---|---|
| trigger | #1681 (merged 2026-09-14 00:21 EDT) swapped the `mcp-tools-embedding` TEI server from `all-MiniLM-L6-v2` to `Qwen/Qwen3-Embedding-0.6B`, a model ~26x larger, running on CPU |
| mechanism | vmcp's optimizer re-embeds the **whole** aggregated tool catalog on **every new session**, inside the `initialize` handshake: 449 tool texts as 15 sequential `/embed` chunks of 32, each with a 30s HTTP timeout (toolhive v0.42.1 `pkg/vmcp/server/serve_optimizer.go` `optimizerSessionTools` -> `toolstore.UpsertTools` -> `similarity/tei_client.go` `EmbedBatch`) |
| mask | the only Gatus check on the gateway probed vmcp `/health`, which answers 200 while the process is alive. The TEI pod was Ready, vmcp was Ready, all 6 backends were healthy. Nothing measured whether a session could open |
| symptom | `initialize` never answered in time: every session failed with `build session optimizer: ... TEI request failed ... context deadline exceeded`, and Hermes' 30s (CLI) / 60s (runtime) connect budget expired first |

A load-amplifying loop kept it that way: Hermes retried about once a minute, and each retry
queued another full-catalog embed behind the ones already running, so the live TEI pod sat
at its 4-core limit (30% of CFS periods throttled) with 8-20s of queue time per chunk.

The embedding server outlived its reason: #1681 chose Qwen3 for LiteLLM's document traffic
(32768-token context), and that traffic moved to `ai/embedding-gpu` on 2026-09-15. From then
on the only consumer was vmcp's tool index, whose texts are short (max 399 tokens).

## Evidence

**Reproduction from the Hermes app container** (same URL, streamable-HTTP `initialize`, no
auth, as `mcp_servers.toolhive` configures): DNS resolved to the Service ClusterIP, TCP
connected in 5ms, `GET /health` returned 200 in 3ms, and the `initialize` POST returned
**0 bytes in 40s**.

**vmcp logs, 2026-09-26 13:26 to 2026-09-27 15:27:** 167 of 168 sessions failed with
`failed to list core tools for session ... TEI request failed ... Client.Timeout exceeded`.
The single success had `tool_count: 2`.

**History:** Hermes' oldest retained log line (`errors.log.2`, 2026-09-14 02:50 EDT, 2.5h after
#1681 merged) already shows toolhive failing, and every rotated log since shows the same
failure continuously. Hermes' `state.db` shows `mcp_toolhive_find_tool` succeeding in June
(MiniLM era). Prometheus retention starts 2026-09-12; vmcp exposes no session metrics (its
`:8080` serves controller-runtime metrics only), so there is no finer signal.

**Model counterfactual.** Two scratch pods ran the same image (`cpu-latest@sha256:1bd34a71...`)
and the same `4` CPU limit on uncontended talos-2. They embedded the real 449 tool texts,
collected from each backend's `tools/list`, using vmcp's exact chunking (32 per request,
`truncate: true`):

| model | one session's catalog | worst chunk | vectors |
|---|---|---|---|
| `Qwen/Qwen3-Embedding-0.6B` (`--max-batch-tokens 384`) | **487.5s** | 65.0s | 449/449 finite |
| `BAAI/bge-small-en-v1.5` (CRD default, TEI defaults) | **6.0s** | 1.0s | 449/449 finite |

So Qwen3 on CPU could not finish at any CPU allocation this pod gets. Even its worst single
chunk exceeded vmcp's per-request timeout.

**Live counterfactual** (suspend `ai/toolhive-config`, patch the `EmbeddingServer` model, then
resume). This ran from the Hermes container against the real gateway:

- `initialize`: **200 in 7.0s and 7.2s** (was 0 bytes after 40s)
- `hermes mcp test toolhive`: **Connected (7959ms), Tools discovered: 2** (`find_tool`, `call_tool`)
- `find_tool` picked the right backend for each query. "list pods in a kubernetes namespace"
  returned `kubectl_get_pods` first. The flux, github, grafana-mcp and radarr queries likewise
  returned `flux_reconcile_flux_kustomization`, `github_search_pull_requests`,
  `grafana-mcp_query_prometheus` and `arr_radarr_*`.
- tool text lengths under bge-small's tokenizer: p50 44, p95 136, max 399 tokens. **0 of 449**
  exceed its 512-token input, so none are truncated. Qwen3 at `--max-batch-tokens 384`
  truncated one.
- footprint: Ready 34s after start, memory peak 719Mi, one full-catalog embed ~23.5 CPU
  core-seconds

Resuming the Kustomization restored Qwen3 from `main`, and the new Gatus check went red, as
intended.

## Ruled out

- **Today's Hermes image change (#1811, `-desktop` tag) and the LiteLLM pass-through lockdowns
  (#1812, #1814).** The failure is continuous from 2026-09-14, two weeks before either. Also,
  the LiteLLM change cannot matter here: Hermes connects to vmcp directly, not through LiteLLM.
- **DNS, Service, endpoints, NetworkPolicy.** The name resolved, TCP connected, `/health`
  answered, and no NetworkPolicy selects the vmcp or TEI pods.
- **The MCP backends.** All 6 `MCPServer`s are Ready, and vmcp logged
  `successfully queried backends 6/6` on every aggregation.
- **Auth.** `incomingAuth: anonymous`. The handshake never reached an auth step.
- **ToolHive or vmcp version.** The operator was last bumped 2026-09-06 (0.42.1), and toolhive
  was working after that date.

## Fix

- `kubernetes/apps/base/ai/toolhive/config/embeddingserver.yaml`: model is now
  `BAAI/bge-small-en-v1.5`, the CRD default. The Qwen-only `--max-batch-tokens 384` override is
  dropped. Resources are resized to the measurement: requests 1 CPU / 1Gi, limits 4 CPU / 2Gi.
  The 1-CPU request keeps one session's ~23.5 core-seconds inside the 30s budget even when the
  node is saturated. The manifest header carries the tripwire: this model sits on the MCP
  connect path, so size it by per-session latency.
- `kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml`: new
  `ToolHive MCP Session` check (group `ai`, pages via `GatusServiceDown`). It POSTs a real
  `initialize` to the same URL Hermes uses, with a 25s client timeout (under Hermes' 30s), and
  asserts on vmcp's `serverInfo`. Both directions ran on a scratch pod with the live Gatus
  image: `success=true` at 8.1s against bge-small, `success=false` at 25.0s against Qwen3.
- `scripts/ci/toolhive-session-probe-test.py`: pins that check's shape so it cannot drift back
  into a check that cannot fail.

## Considered and rejected: pointing vmcp at `ai/embedding-gpu`

`ai/embedding-gpu` serves the same Qwen3-Embedding-0.6B from the B70. vmcp v0.42.1 can reach
it: `optimizer.embeddingProvider: openai` POSTs to `<base>/embeddings`, and llama.cpp serves
`/v1/embeddings`. It was measured on 2026-09-27 and rejected:

- **Batch-size OOM risk.** vmcp's OpenAI client sends the whole catalog in ONE request
  (`openAIMaxBatchSize = 2048` in `similarity/openai_client.go`), so every session would be a
  449-input request. Larger batch sizes are the still-unexplained OOM shape recorded for this
  pod: batch 4 is the measured-safe point, and it was OOMKilled 2x (31 restarts) in the 12
  days before this measurement. That request was not sent, so the live service was not
  risked.
- **NaN drift.** At the safe batch of 4, one of three full-catalog passes returned null
  (NaN) components. This is the known, unexplained `embedding-gpu` fault. Go's JSON decoder
  turns `null` into `0` for a float32 without an error, so tool search would degrade silently.
- **Shared card.** The pod runs at priorityClass `embedding-gpu-low` (-10, never preempts)
  on the B70 it shares with `ai/vllm` chat. Every MCP session, including Hermes' ~1/min
  retries while failing, would put a 449-text burst on that card. That is the request-rate
  chat-throttling tradeoff described in AGENTS.md.
- **Coupling.** The operator rejects `openai` together with `embeddingServerRef`. The switch
  would drop the managed reference and hardcode the URL.
- **Speed is not the difference.** At batch 4 with sequential requests, the GPU took 5.4s for
  all 449 texts, against 6.0s for bge-small on CPU. The Qwen tokenizer puts every tool text
  at 339 tokens or fewer, so llama.cpp's 512-token context would reject none.
- **Quality is not the difference.** On the same five queries (kubectl pods, flux
  kustomization, github PRs, grafana prometheus, radarr add movie), Qwen3's semantic-only
  top-2 held the right tool 5/5. bge-small through the real hybrid `find_tool` did too; its
  only miss was ranking `arr_radarr_add_movie` 2nd rather than 1st. vmcp blends BM25 over
  very lexical tool names with the semantic score, and it sends queries without Qwen3's
  instruct prefix, so Qwen3's edge here is marginal.

## Known gap

When the embedding call fails **fast** instead of slowly, vmcp still answers `initialize` with
200 and then terminates the session. This was seen live while TEI refused connections during
its restart. The client's next request (`tools/list`) then fails. The Gatus check cannot catch
this: it would need to replay the `Mcp-Session-Id` response header, and Gatus v5.36 suites can
only store values from the body. A TEI pod that is down is still covered by its readiness probe
(`KubePodNotReady`). A TEI pod that is up but returns errors is not covered.

## Re-measuring before a model change

Collect the tool texts from each backend proxy (`http://mcp-<name>-proxy.ai.svc.cluster.local:8080/mcp`,
`initialize` then paginated `tools/list`, formatted as `name: <backend>_<tool> description: <desc>`
as vmcp does). Start a scratch TEI pod with the candidate model and this manifest's
resources. Then time `POST /embed {"inputs": <32 texts>, "truncate": true}` sequentially over
the whole list. The total must stay well under 30s, and every vector must be finite and
non-zero (see AGENTS.md on NaN embeddings).
