# State holds every adopted client_secret in plaintext. Bucket stays private. Do not re-add an endpoints block: AWS_ENDPOINT_URL_S3 must win.
# Never point OpenTofu at https://s3.sklab.dev (SignatureDoesNotMatch through Envoy). Skill `authentik-terraform` references/state-and-credentials.md.
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
