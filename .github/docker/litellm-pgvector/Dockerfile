# syntax=docker/dockerfile:1
# BerriAI/litellm-pgvector - the OpenAI-compatible vector-store server that
# LiteLLM's `pg_vector` vector-store provider talks to. Upstream publishes no
# image, so this builds one from a pinned upstream commit. Consumer:
# kubernetes/apps/base/ai/litellm-pgvector/.
#
# The published tag is content-addressed (see
# .github/workflows/build-litellm-pgvector.yaml): it changes whenever this
# directory changes, and scripts/ci/litellm-pgvector-test.py fails if the
# HelmRelease does not reference the tag this directory builds to.
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b AS build

# Upstream main as of 2025-12-22 (the merge of BerriAI/litellm-pgvector#2,
# which also carries the #7 file_counts fix). A git ADD pinned to a full commit
# SHA is content-addressed, so a moved branch cannot change what this builds.
ARG LITELLM_PGVECTOR_COMMIT=b553f84a32f580b4303297df5567f25912b59d93

# prisma-client-py needs node only to run `prisma generate`. Left alone it
# downloads the LATEST node release through nodeenv (unpinned, and 26.x will not
# even start on slim without libatomic), so give it Debian's instead - it prefers
# a node already on PATH (PRISMA_USE_GLOBAL_NODE defaults to true). Node stays
# in this stage; the runtime image carries no node at all. `patch` applies the
# local fixes below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm patch \
    && rm -rf /var/lib/apt/lists/*

ENV PRISMA_HOME_DIR=/opt/prisma \
    PRISMA_BINARY_CACHE_DIR=/opt/prisma/binaries

ADD --keep-git-dir=false https://github.com/BerriAI/litellm-pgvector.git#${LITELLM_PGVECTOR_COMMIT} /app
COPY constraints.txt /tmp/constraints.txt
# Local fixes to upstream, one file each, applied in name order; the build fails
# if any no longer applies cleanly (e.g. after bumping the commit above).
#   0001: list/create 500'd once a store had been written to - raw-query
#         timestamps arrive as strings, not datetimes.
COPY *.patch /tmp/patches/
WORKDIR /app
RUN for p in /tmp/patches/*.patch; do patch -p1 --forward --no-backup-if-mismatch < "$p"; done

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt -c /tmp/constraints.txt \
    && PATH=/opt/venv/bin:$PATH prisma generate \
    && set -- /opt/prisma/binaries/node_modules/prisma/query-engine-* \
    && test "$#" -eq 1 && test -f "$1" \
    && install -D -m 0755 "$1" /opt/prisma/query-engine

# Same base as the build stage (the venv is not relocatable across Pythons).
FROM python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b

LABEL org.opencontainers.image.source="https://github.com/aviator-coding/home-ops"
LABEL org.opencontainers.image.description="BerriAI/litellm-pgvector (OpenAI-compatible vector store API on PGVector)"

COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/prisma/query-engine /opt/prisma/query-engine
COPY --from=build /app /app

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    # The generated client otherwise hunts for the engine under $HOME and the
    # build stage's cache paths; point it at the one binary this image ships.
    PRISMA_QUERY_ENGINE_BINARY=/opt/prisma/query-engine

WORKDIR /app
USER 65534:65534
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
