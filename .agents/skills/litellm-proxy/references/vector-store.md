# Vector store (pgvector)

LiteLLM's Vector Stores API with the `pg_vector` provider, served by
`ai/litellm-pgvector` (BerriAI/litellm-pgvector, OpenAI-compatible) on the
`litellm_pgvector` database of shared `postgres-17`. Store id `default`.

**Non-sensitive content only** (docs, references, repo knowledge; never
personal documents or credentials). Captain decision "A with guardrail": the
registry entry has no `team_id`, so every virtual key can search it and pull
hits into chat via `file_search`, including the demo keys and `ai-pr-review`
(which handles untrusted PR content). Team scoping is not declaratively
enforceable on this LiteLLM/operator version; revisit at LiteLLM v1.104.0.

## Pieces

| Piece | Where |
|---|---|
| Server | `kubernetes/apps/base/ai/litellm-pgvector/app/helmrelease.yaml`, ClusterIP `litellm-pgvector.ai.svc.cluster.local:8000`, no HTTPRoute |
| Image | built from `.github/docker/litellm-pgvector/` (pinned upstream commit + local patches) by `.github/workflows/build-litellm-pgvector.yaml` into `ghcr.io/aviator-coding/litellm-pgvector` |
| Role, DB, `vector` extension, tables, seeded `default` store | `litellm-pgvector/app/dbinit.yaml` + `resources/schema.sql` |
| Registration | `litellmproxy.yaml` `extraConfig.vector_store_registry` |
| Embedding model | `models/qwen3-embedding-8b.yaml` (`qwen/qwen3-embedding-8b`, OpenRouter, metered) |
| Embedding key | `virtualkeys/litellm-pgvector.yaml` (that one model only) |
| Invariants | `scripts/ci/litellm-pgvector-test.py` |

## Three silent failure modes

1. **Registry location.** Use `spec.extraConfig.vector_store_registry`, never
   the CRD's `vectorStoreRegistry`: that field is validated as an object while
   LiteLLM iterates a list, so it is rejected or renders one entry per key and
   breaks startup. `extraConfig` merges into `config.yaml` verbatim; the typed
   field overrides it only when set, so leave it unset.
2. **Key name.** The registry's key is `os.environ/LITELLM_PGVECTOR_API_KEY`.
   Never export `PG_VECTOR_API_KEY` to the proxy: it is `PGVectorStoreConfig`'s
   fallback, so exporting it opens every store id on the server to every
   virtual key (`custom_llm_provider: pg_vector` in the body). Unset, an
   unregistered id fails closed. A missing `LITELLM_PGVECTOR_API_KEY` at
   startup is not fatal (only this store's searches fail).
3. **One model, one width.** Every vector must come from
   `qwen/qwen3-embedding-8b` at `dimensions: 2000`. `schema.sql`'s
   `vector(2000)`, the server's `EMBEDDING__DIMENSIONS`, `EMBEDDING__MODEL`
   and the `litellm-pgvector` key's one-model allow-list move together (CI
   enforces agreement and the 2000-dim index ceiling). A same-width vector
   from another model is accepted and ranks silently wrong; that is also why
   this key deliberately lacks `embedding-local`.

Why 2000: the model's native 4096 cannot be indexed (pgvector HNSW/IVFFlat cap
`vector` at 2000, `halfvec` at 4000). The model is Matryoshka-trained and
OpenRouter honours `dimensions`, returning the renormalised prefix. Changing
the model or width means re-embedding every row (truncate `embeddings` before
altering the column).

## Search (any virtual key)

```bash
curl -s https://litellm.${SECRET_DOMAIN}/v1/vector_stores/default/search \
  -H "Authorization: Bearer $LITELLM_KEY" -H 'Content-Type: application/json' \
  -d '{"query": "how long does pizza dough ferment?", "max_num_results": 5}'
```

RAG in chat: add `{"type": "file_search", "vector_store_ids": ["default"],
"max_num_results": 3}` to `tools`; LiteLLM prepends hits as a `Context:`
message. Keep `max_num_results` small; hits are injected verbatim. Filters:
`eq` on a metadata key, optionally inside `and`; anything else is a 400.
Search spend lands on the `litellm-pgvector` key (the server embeds queries).

## Ingest (not through LiteLLM's vector-store API)

On the pinned LiteLLM, `pg_vector` supports only store create and search:
`/v1/vector_stores/{id}/files` is OpenAI-only and `/v1/rag/ingest` has no
`pg_vector` class. The server has no chunking endpoint. Ingest is two calls,
both with admin credentials:

```bash
kubectl -n ai port-forward svc/litellm-pgvector 18000:8000 &
EMB=$(curl -s https://litellm.${SECRET_DOMAIN}/v1/embeddings \
  -H "Authorization: Bearer $PGVECTOR_EMBED_KEY" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg t "$TEXT" '{model: "qwen/qwen3-embedding-8b", input: $t, dimensions: 2000}')" |
  jq -c '.data[0].embedding')
jq -n --arg t "$TEXT" --argjson e "$EMB" '{content: $t, embedding: $e, metadata: {source: "notes"}}' |
  curl -s localhost:18000/v1/vector_stores/default/embeddings \
    -H "Authorization: Bearer $PGVECTOR_SERVER_KEY" -H 'Content-Type: application/json' -d @-
```

`PGVECTOR_EMBED_KEY` is 1Password `litellm-consumer-litellm-pgvector`;
`PGVECTOR_SERVER_KEY` is 1Password `litellm-pgvector`, field
`LITELLM_PGVECTOR_SERVER_API_KEY`. Omitting `dimensions: 2000` gets 4096 and
the insert is refused. Bulk: `/embeddings/batch` with `{"embeddings": [...]}`.
The key's $5/30d and 1M tpm bound a runaway load.

Another store: create it on the server (`POST /v1/vector_stores` with the
bearer key) and add a second registry entry, or seed it in `schema.sql`.
Creating through LiteLLM would need `PG_VECTOR_API_KEY` (never set). All
stores share one table, so one model and width.

## Data, credentials, health

- Every ingested chunk and search query is sent to OpenRouter (Nebius or
  DeepInfra) to be embedded; rows ride `postgres-17` Barman to the LAN NAS.
- Credentials: `litellm/app/pushsecret-pgvector.yaml` carries the one-time
  seed command (`kubectl create`, never `apply`) and rotation. It lives in the
  litellm app so the 1Password item exists before `litellm-pgvector` (which
  `dependsOn` litellm) reads it. `deletionPolicy: None` so a prune never
  deletes the only durable copy.
- Never put libpq `PG*` variables (especially `PGDATABASE`) in the db-init
  Secret: `postgres-init` loads it too and would point its checks at the not
  yet created database, leaving the role with a NULL password while the Job
  reports Completed. The schema container sets its own `PG*` in `env`.
- The server image tag is content-addressed: after any edit under
  `.github/docker/litellm-pgvector/`, set the HelmRelease tag to
  `python3 scripts/ci/litellm-pgvector-test.py --print-tag`. Never edit files
  there casually (a comment changes the hash).
- The server's `/health` is static (process up, not DB up). A DB problem shows
  as 500s on search, logged by the proxy as failed `avector_store_search`.
