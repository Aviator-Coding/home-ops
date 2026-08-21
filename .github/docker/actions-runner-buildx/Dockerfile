# syntax=docker/dockerfile:1
FROM ghcr.io/home-operations/actions-runner:2.336.0@sha256:281a9a090522fafbf4967f158b8c97d03552b1978b688893c5f7cb1944bc5fe5

LABEL org.opencontainers.image.source="https://github.com/aviator-coding/home-ops"
LABEL org.opencontainers.image.description="ARC actions-runner with buildx verified pre-baked"

# podman/uidmap/netavark/aardvark-dns (the package set software-factory's runner-tests.yml
# installs at runtime) were deliberately left out: this cluster's nodes have unprivileged user
# namespaces disabled (/proc/sys/user/max_user_namespaces=0), which blocks rootless podman even
# under a fully privileged pod, and enabling it cluster-wide or running the runner as root was
# judged not worth the security tradeoff. Revisit if that decision changes.
#
# buildx ships with the base image already as a docker CLI plugin; this just asserts it at
# build time so a future base-image bump that silently drops it fails the build here instead of
# surfacing later in a job.
RUN docker buildx version
