-- Schema for BerriAI/litellm-pgvector, applied by ../dbinit.yaml on every run.
-- Idempotent: every statement is IF NOT EXISTS / ON CONFLICT DO NOTHING.
--
-- Why this file exists at all: the server image never creates its own tables
-- (upstream only runs `prisma generate`; `prisma db push` is a manual step), and
-- upstream's prisma/schema.prisma hardcodes `vector(1536)`. The server only ever
-- talks to these tables through raw SQL (`db.query_raw`), so declaring them here
-- with the right width is sufficient - the generated Prisma models are unused.
--
-- WIDTH 2000, deliberately: pgvector's HNSW index caps `vector` at 2000 dims and
-- the embedding model's native 4096 cannot be indexed. Why 2000 (and not
-- halfvec): skill `litellm-proxy`, references/vector-store.md.
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
