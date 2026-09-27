-- Schema for BerriAI/litellm-pgvector, applied by ../dbinit.yaml on every run.
-- Idempotent: every statement is IF NOT EXISTS / ON CONFLICT DO NOTHING.
--
-- Why this file exists at all: the server image never creates its own tables
-- (upstream's Dockerfile only runs `prisma generate`; `prisma db push` is a
-- manual step in its README), and upstream's prisma/schema.prisma hardcodes
-- `vector(1536)`. This store's width is chosen, not inherited - see below. The
-- server only ever talks to
-- these tables through raw SQL (`db.query_raw`), so declaring them here with
-- the right width is sufficient - the generated Prisma models are unused.
--
-- WIDTH 2000, deliberately. The embedding model is OpenRouter's
-- qwen/qwen3-embedding-8b (captain decision 2026-09-27), native width 4096.
-- pgvector's HNSW and IVFFlat indexes cap `vector` at 2000 dims and `halfvec`
-- at 4000, so the native width cannot be indexed at all; the model is
-- Matryoshka-trained and honours `dimensions`, so every vector is requested at
-- 2000 - the widest a plain `vector` column still indexes (halfvec would also
-- need every `::vector` cast in upstream's SQL patched, for no gain below
-- 2000). Measured 2026-09-27: the 2000-d answer is the renormalised prefix of
-- the 4096-d one (cosine 0.99995).
--
-- The width MUST match EMBEDDING__DIMENSIONS in ../helmrelease.yaml and stay
-- <= 2000 while the HNSW index exists (scripts/ci/litellm-pgvector-test.py).
-- Changing the embedding model or the width means re-embedding every row:
-- ALTER the column only after truncating `embeddings`, because vectors from
-- two models (or two widths) are not comparable.
--
-- Column names and types mirror upstream's Prisma schema (Json -> jsonb,
-- DateTime -> timestamp(3)) so upstream's queries run unchanged.
--
-- Runs as the superuser (only a superuser may CREATE EXTENSION vector - it is
-- not a trusted extension), then drops to the app role so the app owns every
-- object it reads and writes. psql variable `owner` is set by the Job.

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS vector;

SET ROLE :"owner";

CREATE TABLE IF NOT EXISTS vector_stores (
    id             text         PRIMARY KEY,
    name           text         NOT NULL,
    file_counts    jsonb,
    status         text         NOT NULL DEFAULT 'completed',
    usage_bytes    integer      DEFAULT 0,
    created_at     timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_after  jsonb,
    expires_at     timestamp(3),
    last_active_at timestamp(3),
    metadata       jsonb
);

CREATE TABLE IF NOT EXISTS embeddings (
    id              text         PRIMARY KEY,
    vector_store_id text         NOT NULL REFERENCES vector_stores (id) ON DELETE CASCADE ON UPDATE CASCADE,
    content         text         NOT NULL,
    embedding       vector(2000) NOT NULL,
    metadata        jsonb,
    created_at      timestamp(3) NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS embeddings_vector_store_id_idx ON embeddings (vector_store_id);

-- The server's search orders by `embedding <=> query` (cosine distance), so
-- index the cosine operator class. HNSW needs no training data, unlike IVFFlat,
-- so it is valid on an empty table.
CREATE INDEX IF NOT EXISTS embeddings_embedding_hnsw_idx ON embeddings USING hnsw (embedding vector_cosine_ops);

-- The store registered in ../../../litellm/app/litellmproxy.yaml. Seeded here
-- so its id is fixed in Git: a store created through the API gets a random
-- UUID, which LiteLLM's config-file registry would then have to hardcode.
INSERT INTO vector_stores (id, name, file_counts, status, usage_bytes, metadata)
VALUES ('default', 'default', '{"in_progress": 0, "completed": 0, "failed": 0, "cancelled": 0, "total": 0}', 'completed', 0, '{}')
ON CONFLICT (id) DO NOTHING;
