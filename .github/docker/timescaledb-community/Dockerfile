# syntax=docker/dockerfile:1
# TimescaleDB Community Edition (Timescale License: compression, continuous
# aggregates, policy jobs) packaged as a CloudNativePG ImageVolume extension
# image. The official ghcr.io/cloudnative-pg/timescaledb-oss image is the
# Apache-2 edition only. Consumer: postgres-18 in
# kubernetes/apps/base/database/cloudnative-pg/cluster-18/.
#
# Layout mirrors cloudnative-pg/postgres-extensions-containers
# (timescaledb-oss/Dockerfile): /lib and /share/extension, nothing else.
#
# The published tag is content-addressed (see
# .github/workflows/build-timescaledb-community.yaml): it changes whenever this
# directory changes, and scripts/ci/timescaledb-community-test.py fails if
# cluster-18 does not reference the tag this directory builds to.
ARG BASE=ghcr.io/cloudnative-pg/postgresql:18.6-minimal-trixie@sha256:64abc98a4387361afd6821a3a99f975763bbd87bbdde9449e75ad6002528a318
FROM ${BASE} AS builder

ARG PG_MAJOR=18
# Exact package version from packagecloud.io/timescale/timescaledb (debian trixie).
# The part before `~` is the TimescaleDB version the image tag starts with.
ARG TIMESCALEDB_VERSION=2.31.0~debian13-1806
# Primary key of https://packagecloud.io/timescale/timescaledb/gpgkey
ARG TIMESCALE_KEY_FPR=1005FB68604CE9B8F6879CF759F18EDF47F24417

USER 0

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey -o /tmp/timescale.key; \
    gpg --show-keys --with-colons /tmp/timescale.key | grep -q "^fpr:::::::::${TIMESCALE_KEY_FPR}:"; \
    gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg /tmp/timescale.key; \
    echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/debian/ trixie main" \
        > /etc/apt/sources.list.d/timescaledb.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends "timescaledb-2-postgresql-${PG_MAJOR}=${TIMESCALEDB_VERSION}"

FROM scratch
ARG PG_MAJOR=18

# Licenses (TSL is not Apache-2; keep the notices with the binaries)
COPY --from=builder /usr/share/doc/timescaledb-2-postgresql-${PG_MAJOR}/ /licenses/timescaledb-2-postgresql-${PG_MAJOR}/

# Libraries: the loader (timescaledb.so) plus the versioned apache and tsl modules
COPY --from=builder /usr/lib/postgresql/${PG_MAJOR}/lib/timescaledb* /lib/

# Share: control file and versioned SQL scripts
COPY --from=builder /usr/share/postgresql/${PG_MAJOR}/extension/timescaledb* /share/extension/

USER 65532:65532
