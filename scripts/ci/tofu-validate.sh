#!/usr/bin/env bash
# Credential-less tofu fmt and tofu validate -backend=false for every stack under terraform/.
# A green run is not apply-safety: this never calls Authentik or the state backend. Live read-only plans are terraform-diff.yaml. Apply needs an explicit go-ahead.
# Skill `authentik-terraform` references/ci-plan.md and references/apply-runbook.md.
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

# Throwaway data dir: an operator's real `tofu init` leaves .terraform/ with the
# S3 backend recorded, which this credential-less check would otherwise pick up.
data_dir="$(mktemp -d)"
trap 'rm -rf "${data_dir}"' EXIT

for stack in "${stacks[@]}"; do
    printf '==> %s\n' "${stack}"
    export TF_DATA_DIR="${data_dir}/${stack##*/}"
    tofu -chdir="${stack}" init -backend=false -input=false -no-color
    tofu -chdir="${stack}" validate -no-color
done

printf 'OK: %d OpenTofu stack(s) formatted and valid\n' "${#stacks[@]}"
