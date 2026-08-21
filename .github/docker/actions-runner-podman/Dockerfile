# syntax=docker/dockerfile:1
FROM ghcr.io/home-operations/actions-runner:2.336.0@sha256:281a9a090522fafbf4967f158b8c97d03552b1978b688893c5f7cb1944bc5fe5

LABEL org.opencontainers.image.source="https://github.com/aviator-coding/home-ops"
LABEL org.opencontainers.image.description="ARC actions-runner with podman pre-installed for ephemeral local-runner jobs"

# Same package set software-factory's runner-tests.yml installs at runtime today, baked in at
# build time instead so ephemeral runner pods start warm. buildx ships with the base image
# already (docker CLI plugin) and needs no extra install here.
USER root
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      podman \
      uidmap \
      netavark \
      aardvark-dns && \
    rm -rf /var/lib/apt/lists/*
USER runner
