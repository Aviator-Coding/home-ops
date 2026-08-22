#!/usr/bin/env bash
# Render every Talos template and run `talosctl validate` over the result.
#
# Runs with no secrets and no cluster access: the `ref+op://` vals references are
# stubbed with a dummy value and the factory schematic id is faked, because
# neither takes part in schema validation.
#
# WHAT THIS CATCHES
#   - template render breakage: undefined variables, malformed Jinja, output
#     that is not parseable YAML
#   - machine-config keys Talos does not know (unknown/misspelled fields)
#   - patch failures between machineconfig.yaml.j2 and a node overlay
#
# WHAT THIS DOES NOT CATCH - tested directly, do not oversell this check
#   - invalid enum values (e.g. `machine.type: notarealtype`)
#   - malformed or simply wrong CIDRs
#   - a missing or wrong `machine.install.disk`
# `talosctl validate` is a schema check, not a semantic one. Green here does not
# mean the config is safe to apply to a node; the dry-run, one-node-at-a-time
# discipline in talos/AGENTS.md is what covers that.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

for tool in minijinja-cli talosctl yq; do
    command -v "${tool}" >/dev/null 2>&1 || {
        printf 'missing required tool: %s\n' "${tool}" >&2
        exit 1
    }
done

# Any 64-char hex id renders; the real one is computed from a live call to
# factory.talos.dev, which CI deliberately does not make.
export SCHEMATIC=0000000000000000000000000000000000000000000000000000000000000000

tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT

# vals resolves these at apply time. Substitute a base64 value so fields typed as
# base64 (certs, keys) still parse.
stub() { sed -E 's#ref\+op://[^[:space:]]+#ZHVtbXk=#g'; }

nodes=()
for template in talos/nodes/*.yaml.j2; do
    nodes+=("$(basename "${template}" .yaml.j2)")
done
[[ ${#nodes[@]} -gt 0 ]] || {
    printf 'no node templates found under talos/nodes/\n' >&2
    exit 1
}

for node in "${nodes[@]}"; do
    minijinja-cli "talos/nodes/${node}.yaml.j2" | stub >"${tmp}/${node}.overlay.yaml"

    # Mirrors `just talos render-config`: machine.type lives in the node overlay
    # and gates the control-plane-only blocks of machineconfig.yaml.j2.
    CONTROLPLANE="$(yq 'select(.machine) | (.machine.type == "controlplane") // ""' "${tmp}/${node}.overlay.yaml")"
    export CONTROLPLANE

    minijinja-cli talos/machineconfig.yaml.j2 | stub >"${tmp}/${node}.base.yaml"
    talosctl machineconfig patch "${tmp}/${node}.base.yaml" \
        -p "@${tmp}/${node}.overlay.yaml" >"${tmp}/${node}.yaml"
    talosctl validate -m metal -c "${tmp}/${node}.yaml"
done

minijinja-cli talos/schematic.yaml.j2 >"${tmp}/schematic.yaml"
yq -e '.customization' "${tmp}/schematic.yaml" >/dev/null

printf 'OK: %d node config(s) render and validate; schematic renders\n' "${#nodes[@]}"
