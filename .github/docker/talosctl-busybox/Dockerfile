# syntax=docker/dockerfile:1
ARG TALOS_VERSION=v1.12.2
FROM ghcr.io/siderolabs/talosctl:${TALOS_VERSION} AS talosctl

FROM busybox:1.37

ARG TALOS_VERSION
LABEL org.opencontainers.image.source="https://github.com/aviator-coding/home-ops"
LABEL org.opencontainers.image.description="Busybox with talosctl ${TALOS_VERSION}"

COPY --from=talosctl /talosctl /usr/local/bin/talosctl
