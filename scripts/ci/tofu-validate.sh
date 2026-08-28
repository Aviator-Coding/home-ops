#!/usr/bin/env bash
# Format-check and schema-validate every OpenTofu stack under terraform/.
#
# This script (and validate.yaml's terraform job that calls it) stays
# credential-less: `tofu init -backend=false`, so the S3 backend in
# terraform/authentik/backend.tofu is never contacted and no state lock is
# taken. Real read-only plans against the live backend/API live in
# terraform-diff.yaml (see docs/authentik/terraform.md section 9).
# `tofu apply` remains fully behind the explicit approval gate in that doc's
# section 7 - nothing here mints, reads, or references apply credentials.
#
# WHAT THIS CATCHES
#   - formatting drift (`tofu fmt`)
#   - unknown or misspelled resource, data source and attribute names, checked
#     against the real provider schema downloaded from the registry
#   - type errors, unresolved references, undeclared variables
#   - provider versions that do not satisfy the lock file, or a lock file missing
#     hashes for this runner's platform
#   - state or rendered tfvars accidentally committed
#
# WHAT THIS DOES NOT CATCH - do not read a green run as apply-safety
#   - anything that requires talking to Authentik or the state backend.
#     `tofu validate` never calls the API, so it cannot tell you whether an
#     import id resolves, whether a resource matches the live object, or
#     whether a plan is free of destructive changes. For that, see the
#     read-only plan path in terraform-diff.yaml / section 9; apply stays
#     section 7-gated.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v tofu >/dev/null 2>&1 || {
    printf 'missing required tool: tofu\n' >&2
    exit 1
}

# State holds every adopted OAuth2 client secret in plaintext, and a rendered
# tfvars holds whatever was fed in. terraform/.gitignore covers both; this is the
# check that the ignore rule was not bypassed with `git add -f`.
leaked="$(git ls-files 'terraform/**' \
    | grep -E '(^|/)tfplan$|\.(tfstate|tfstate\.backup|tfvars|tfvars\.json|tfplan|planfile)$' || true)"
if [[ -n "${leaked}" ]]; then
    printf 'refusing to validate: state or variable files are committed:\n%s\n' "${leaked}" >&2
    exit 1
fi

tofu fmt -check -recursive -diff terraform

stacks=()
for dir in terraform/*/; do
    compgen -G "${dir}*.tofu" >/dev/null && stacks+=("${dir%/}")
done
[[ ${#stacks[@]} -gt 0 ]] || {
    printf 'no OpenTofu stacks found under terraform/\n' >&2
    exit 1
}

for stack in "${stacks[@]}"; do
    printf '==> %s\n' "${stack}"
    tofu -chdir="${stack}" init -backend=false -input=false -no-color
    tofu -chdir="${stack}" validate -no-color
done

printf 'OK: %d OpenTofu stack(s) formatted and valid\n' "${#stacks[@]}"
