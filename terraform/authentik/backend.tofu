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
terraform {
  backend "s3" {
    bucket = "terraform-state"
    key    = "authentik/authentik.tfstate"
    region = "us-east-1"

    endpoints = {
      s3 = "https://s3.sklab.dev"
    }

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
