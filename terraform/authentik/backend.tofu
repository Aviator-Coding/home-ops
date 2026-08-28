# Remote state on the cluster's own Ceph RGW object store.
#
# Why RGW and not the VolSync MinIO target: MinIO here is external to the cluster
# (nas.${cluster_domain}:9000, see kubernetes/components/volsync/minio/), while RGW
# is the in-cluster, Flux-managed S3 service already used for app buckets. `s3.sklab.dev`
# is published on the `envoy-internal` gateway only (kubernetes/apps/base/rook-ceph/),
# so state is never reachable from the public gateway.
#
# THE STATE FILE CONTAINS SECRETS IN PLAINTEXT - every OAuth2 client_secret this
# stack adopts, plus the Authentik API token if it is ever surfaced in an output.
# The `terraform-state` bucket MUST stay private: no bucket policy, no public ACL,
# and its RGW user's keys are 1Password-only. Bootstrap steps and the exact
# `radosgw-admin` commands are in ../../docs/authentik/terraform.md.
#
# Credentials come from the environment (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY),
# rendered from 1Password by `vals`; there is deliberately no backend.tfvars file.
#
# S3 endpoint is ALSO environment-supplied (AWS_ENDPOINT_URL_S3), not hardcoded
# here. That is load-bearing for two consumers that cannot share one URL:
#
#   - Local operators open a port-forward to the RGW Service and point
#     secrets.vals.yaml / secrets-apply.vals.yaml at http://127.0.0.1:18081
#     (see docs/authentik/terraform.md section 3).
#   - terraform-diff.yaml runs on the in-cluster ARC runner, which has no
#     ServiceAccount token and therefore cannot kubectl port-forward, but CAN
#     reach ClusterIP Services directly. secrets-ci.vals.yaml therefore points
#     at http://rook-ceph-rgw-ceph-objectstore.rook-ceph.svc.cluster.local.
#
# NEVER put https://s3.sklab.dev here (or in AWS_ENDPOINT_URL_S3). Measured
# 2026-08-27: every request from OpenTofu's S3 client through the
# `envoy-internal` gateway fails with SignatureDoesNotMatch, while the
# identical credentials against the RGW Service directly succeed.
#
# The cause is which headers get signed. aws-sdk-go-v2 puts `accept-encoding`,
# `amz-sdk-invocation-id` and `amz-sdk-request` in SignedHeaders; boto3 and
# minio-go sign none of them. Envoy rewrites one of those in transit, so RGW
# recomputes a different signature and rejects the request. That is also why
# nothing noticed before: VolSync (restic/minio-go) and every other S3 client
# in this cluster keep working through the same route, and a boto3
# ListObjectsV2 against this exact bucket returns 200. State traffic must
# never touch the gateway at all, which for a bucket holding every adopted
# client secret is the better posture anyway.
#
# OpenTofu's S3 backend honours AWS_ENDPOINT_URL_S3 when `endpoints` is omitted
# from this block (verified against OpenTofu 1.12.6). Do not re-add an
# `endpoints` block here - a hardcoded value would win over the env var and
# break whichever consumer it does not match.
terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "authentik/authentik.tfstate"
    region = "us-east-1"

    # S3-native state locking. RGW supports the conditional-write semantics
    # OpenTofu >= 1.11 uses for this, so no DynamoDB equivalent is needed.
    use_lockfile = true

    # RGW is not AWS: it has no STS, no IMDS, and no account-id endpoint, and it
    # serves buckets path-style rather than as virtual hosts.
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    use_path_style              = true
  }
}
