# Embedding truncation follow-up (2026-09-14)

## What this ships

**The embedding server's memory was under-provisioned and is fixed here:**
`requests.memory` 2Gi -> 3Gi, `limits.memory` 4Gi -> 5Gi. The 2026-09-13
sizing assumed the model's ~1.2GB on-disk (bf16) size tracks its resident
RAM size, but TEI loads Qwen3-Embedding-0.6B in float32 on CPU
(`GET /info` reports `model_dtype: float32`), so actual resident weights
are ~0.6B params * 4 bytes = ~2.2GiB - confirmed by live-measured process
RSS (steady-state 2.69-2.95GiB including framework overhead and warmup
buffer). The old 2Gi request was already ~700Mi below the real floor.
`limits.cpu` is unchanged (4 cores, external-caller-burst reasoning from
#1681 still applies).

**`--max-batch-tokens` stays at 384, deliberately unchanged.** The rest of
this document is why: raising it looked like the obvious fix for the
silent-truncation defect #1681 left in place, and was tried - but tracing
TEI's actual request path showed it would not have fixed that defect, only
paid a real, steeply-scaling warm-up cost for capacity neither live
consumer needs today. See
`kubernetes/apps/base/ai/toolhive/config/embeddingserver.yaml` for the
condensed version of this reasoning inline.

## What was still broken

Verified against the live server after #1681 merged:

```
GET /info
  model_id         : Qwen/Qwen3-Embedding-0.6B     <- swapped correctly
  max_input_length : 384                            <- NOT 32768
  max_batch_tokens : 384
  auto_truncate    : True                           <- still silently truncating

POST /embed with 600 tokens -> 200 OK, 1024-dim vector returned (silently truncated)
```

Root cause: `spec.args` on the `EmbeddingServer` already carried
`--max-batch-tokens 384`, sized for MiniLM before the swap. TEI's
`--max-batch-tokens` doubles as the router's effective per-request
`max_input_length` regardless of what model is loaded - `auto_truncate`
silently truncates anything past it. #1681 changed `spec.model` and left
this argument untouched.

## First pass: raise the ceiling (reverted - see below)

The obvious fix was to raise `--max-batch-tokens`. That was tried, measured,
and ultimately reverted, because two deeper findings changed what "fixed"
actually means for this endpoint. In order:

### 1. The ceiling and the silent/loud decision are separate, and both were checked

Raising `--max-batch-tokens` narrows how often the cliff is hit; it does not
make hitting it loud. Whether TEI can be told to *error* on an over-long
input instead of silently truncating was checked directly.

**TEI has two embedding routes with different truncate handling** (traced
in TEI v1.9.3 source, `router/src/http/server.rs` and
`router/src/http/types.rs`):

- Native `/embed`: `EmbedRequest.truncate: Option<bool>`, resolved as
  `req.truncate.unwrap_or(info.auto_truncate)`. A per-request
  `{"truncate": false}` on an over-long input gets a clean `422`:
  `Input validation error: 'inputs' must have less than 384 tokens. Given: 601`
  (live-tested against the running production server). This is exactly the
  "fail loud" behavior the swap was supposed to deliver.
- OpenAI-compatible `/v1/embeddings` (`openai_embed` handler): resolves
  truncation as `let truncate = info.auto_truncate;` - a hardcoded read of
  the server-wide default. `OpenAICompatRequest` (the request struct) has
  no `truncate` field at all. No per-request override reaches this route,
  ever, regardless of what a caller sends in the body.

**`/v1/embeddings` is the route that matters.** LiteLLM's
`embedding-local-cpu` model (`kubernetes/apps/base/ai/litellm/app/models/
embedding-local-cpu.yaml`) talks to TEI's `/v1/embeddings`, and every
external caller through the `embedding-external` virtual key goes through
LiteLLM. So the one per-request lever that works is structurally
unreachable for the traffic this whole change exists to serve.

The other live consumer, ToolHive's `mcp-gateway-internal` vmcp pod, does
call the native `/embed` endpoint - but its Go client hardcodes
`Truncate: true` unconditionally
(`github.com/stacklok/toolhive/pkg/vmcp/optimizer/internal/similarity/tei_client.go`):

```go
// Truncate tells the TEI server to silently truncate input texts that
// exceed the model's maximum token length instead of returning an error.
// We always set this to true because tool descriptions may exceed model
// limits and we prefer embedding a truncated description over a request failure.
Truncate bool `json:"truncate"`
```

That is a deliberate upstream design choice in a project this repo does not
own, not a config knob. It is a real constraint, not a preference we could
override by editing the `EmbeddingServer` CRD.

**Conclusion: per-request opt-out to a loud failure exists in TEI, but is
not reachable from either of this endpoint's actual callers.**

### 2. Flipping the server-wide default is blocked by TEI itself, independent of concurrency

The other lever is the server-wide `--auto-truncate false`, which would at
least make *unspecified*-truncate requests on `/embed` fail loud (it would
not touch `/v1/embeddings`, which does not consult a per-request value at
all - the server default IS what it uses). This was checked and rejected:

TEI refuses to even start with `--auto-truncate false` unless
`--max-batch-tokens >= the model's own max_input_length` (32768 for this
model, intrinsic to its tokenizer config) - live-reproduced:

```
Error: The maximum input length is `32768` which exceeds
`--max-batch-tokens=384`. Either increase `--max-batch-tokens` to at
least `32768`, or set `--auto-truncate true` so that regardless the
maximum input length, those are truncated to `384` tokens.
```

Checked whether that requirement is inflated by provisioning for
concurrent batching that this workload (959 embeddings / 49h measured, a
~0.11% duty cycle - concurrency is not the constraint) never uses: TEI
exposes `--max-batch-requests` and `--max-client-batch-size` to narrow
batch width. Live-tested at `--max-batch-tokens 16384` (the value that
already fails at TEI's un-overridden default): the warmup-buffer
allocation failure is byte-for-byte identical (`memory allocation of
17179869184 bytes failed`, 16 GiB) with `--max-batch-requests 1` or
`--max-client-batch-size 1` added, same as with no batch-width constraint
at all. The warmup buffer - `min(model max_input_length,
--max-batch-tokens)^2 * 16 attention heads * 4 bytes (fp32)` - is sized for
one worst-case single sequence occupying the entire token budget alone;
batch width never factors in, because a lone request can always be as long
as `max_input_length` regardless of how many concurrent requests are
allowed. TEI exposes no per-sequence max-length knob independent of
`--max-batch-tokens` (confirmed against its full `--help` output).

At 32768 that formula is 64 GiB for the buffer alone - 4x the 16 GiB that
already fails on this cluster's own node budget, and not affordable on a
Talos node with ~91GiB allocatable shared by every other workload. **This
is a real, evidenced, non-negotiable limit of TEI v1.9.3 for this model on
this hardware profile, not an artifact of over-provisioning for
concurrency.**

### 3. Warmup cost is real, measured, and rises steeply with the ceiling

TEI's mandatory startup warmup runs one forward pass shaped by
`--max-batch-tokens` before the server reports `Ready`. That is a real,
recurring cost on every restart of a service `mcp-gateway-internal` (vmcp)
actually depends on today.

Measured:

| ceiling | environment | time to Ready |
|---|---|---|
| 384 | live Talos pod | 54s |
| 512 | local podman (same pinned image) | ~11 min |
| 2048 | local podman (same pinned image) | >60 min (torn down after confirming Ready) |

**The podman numbers are VM-confounded and must not be read as
cluster-equivalent times** - podman on this host runs the pinned amd64
image under QEMU emulation on an 8GiB macOS VM, a completely different
performance profile from a native Talos node. The 512 and 2048 figures are
only valid as a same-environment curve *shape* (warmup cost rises steeply,
worse than linearly, with the ceiling), not as a prediction of what a
cluster restart would actually cost. No live-cluster warmup measurement
above 384 was taken - that would mean restarting the production pod, which
needs explicit authorization (the live cluster is read-only to this task)
and was not sought, since the podman curve combined with (1) and (2) below
already settled the question without needing it.

## Why the ceiling stays at 384

Putting the three findings together: raising `--max-batch-tokens` does not
make truncation loud for `/v1/embeddings` (finding 1), disabling
`auto_truncate` server-wide is not affordable at any ceiling below 32768
regardless of concurrency tuning (finding 2), and raising the ceiling has a
real, steeply-scaling warm-up cost paid on every restart of a service vmcp
depends on today (finding 3). So raising the ceiling would not fix the
defect - it would only narrow how often it bites - and it would cost
availability on the one consumer that is actually live.

Weighed against that: neither real consumer needs more than 384 tokens
today. ToolHive's tool-selection index embeds short tool name/description
strings, comfortably inside 384. The `embedding-external` virtual key has
**no live external consumer yet** (`kubernetes/apps/base/ai/litellm/app/
virtualkeys/embedding-external.yaml`'s own comment: "Tune with real traffic
once one exists"). There is no concrete capacity need being unmet today,
only a hypothetical future one.

**384 stays.** `auto_truncate` stays at its TEI default (`true`) for the
same reason as before - it is not reachable at any affordable ceiling. If a
real document-retrieval consumer with a genuine need for longer input shows
up, raise `--max-batch-tokens` deliberately then and pay the warmup cost
for a real, not speculative, reason.

The model swap itself (384-dim/256-token/English-only MiniLM ->
1024-dim/32768-capable/multilingual Qwen3) stands from #1681 - that is a
real improvement independent of the ceiling question, and unaffected by
anything in this document.

## Still outstanding

The `mcp-gateway-internal` vmcp pod needs a rollout restart - the operator
does not cascade one when the embedding model changes, and the server
already returns 1024-dim vectors where it used to return 384-dim, so
anything the vmcp pod is still holding from before the #1681 merge compares
incompatible vector spaces right now. This is an operational action outside
git; it is not performed by this change and remains a follow-up for
firstmate/the captain, same as #1681's own commit message and the previous
version of this document already flagged.
