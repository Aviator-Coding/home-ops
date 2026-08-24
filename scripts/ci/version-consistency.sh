#!/usr/bin/env bash
# Assert the Talos and Kubernetes versions pinned in talos/machineconfig.yaml.j2
# match the tuppr CRs that actually drive cluster upgrades.
#
# WHY THIS EXISTS
# The template holds a second, Renovate-managed copy of versions that tuppr
# already owns. Flux never applies the template - `just talos apply-node` does,
# by hand - so the two copies drift silently in both directions: a Renovate PR
# bumping the template does not upgrade the cluster, and a tuppr-driven upgrade
# does not update the template. Applying a stale template then downgrades a
# running node. See AGENTS.md, "talos/machineconfig.yaml.j2's 6 version pins".
#
# This compares declared version against declared version. It says nothing about
# what the nodes are actually running - check that with `kubectl get nodes -o wide`
# before any apply-node.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v yq >/dev/null 2>&1 || {
    printf 'missing required tool: yq\n' >&2
    exit 1
}

machineconfig=talos/machineconfig.yaml.j2
kubernetes_cr=kubernetes/apps/base/system-upgrade/tuppr/upgrades/kubernetesupgrade.yaml
talos_cr=kubernetes/apps/base/system-upgrade/tuppr/upgrades/talosupgrade.yaml

for file in "${machineconfig}" "${kubernetes_cr}" "${talos_cr}"; do
    [[ -f "${file}" ]] || {
        printf 'missing expected file: %s\n' "${file}" >&2
        exit 1
    }
done

kubernetes_want="$(yq -e '.spec.kubernetes.version' "${kubernetes_cr}")"
talos_want="$(yq -e '.spec.talos.version' "${talos_cr}")"

fail=0
checked=0

# machine.install.image - the Talos installer, matched against the TalosUpgrade CR.
while read -r image; do
    checked=$((checked + 1))
    version="${image##*:}"
    [[ "${version}" == "${talos_want}" ]] || {
        printf 'MISMATCH  %s  %s  != TalosUpgrade %s\n' \
            "${machineconfig}" "${image}" "${talos_want}"
        fail=1
    }
done < <(grep -oE 'factory\.talos\.dev/installer/[^:]+:v[0-9.]+' "${machineconfig}")

# kubelet + control-plane images, matched against the KubernetesUpgrade CR.
while read -r image; do
    checked=$((checked + 1))
    version="${image##*:}"
    [[ "${version}" == "${kubernetes_want}" ]] || {
        printf 'MISMATCH  %s  %s  != KubernetesUpgrade %s\n' \
            "${machineconfig}" "${image}" "${kubernetes_want}"
        fail=1
    }
done < <(grep -oE '(ghcr\.io/siderolabs/kubelet|registry\.k8s\.io/kube-[a-z-]+):v[0-9.]+' "${machineconfig}")

# A refactor that renames or drops the pins must fail loudly, not pass silently.
expected=6
[[ "${checked}" -eq "${expected}" ]] || {
    printf 'found %d version pin(s) in %s, expected %d - has the template been restructured?\n' \
        "${checked}" "${machineconfig}" "${expected}" >&2
    fail=1
}

if [[ "${fail}" -ne 0 ]]; then
    exit 1
fi

printf 'OK: %d machineconfig version pin(s) match the tuppr CRs (Talos %s, Kubernetes %s)\n' \
    "${checked}" "${talos_want}" "${kubernetes_want}"
