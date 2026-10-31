#!/usr/bin/env bash
# Mint read-only credentials for agent sessions (goal §3, task 0.4). OWNER runs this
# with admin credentials; agents never do:
#
#   HOME_OPS_KUBECONFIG=~/.kube/home-ops-admin HOME_OPS_TALOSCONFIG=~/.talos/home-ops-admin \
#     mise exec -- scripts/agent-credentials.sh
#
# (.mise.toml maps HOME_OPS_* onto KUBECONFIG/TALOSCONFIG; a plain KUBECONFIG= prefix is overridden.)
#
# Writes (both gitignored, both picked up by .mise.toml):
#   kubeconfig-agent          ServiceAccount kube-system/agent-readonly token (view + agent-readonly-extra)
#   talos/talosconfig-agent   talosctl client certificate with role os:reader
#
# Re-run before the printed expiry. Needs kubectl, talosctl, python3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_KUBECONFIG="${ROOT}/kubeconfig-agent"
AGENT_TALOSCONFIG="${ROOT}/talos/talosconfig-agent"
DURATION="${AGENT_TOKEN_DURATION:-720h}"

die() { echo "agent-credentials: $*" >&2; exit 1; }

# Minting needs admin rights, which the agent files do not have by design.
for pair in "KUBECONFIG:${AGENT_KUBECONFIG}" "TALOSCONFIG:${AGENT_TALOSCONFIG}"; do
  var="${pair%%:*}"
  agent_file="${pair#*:}"
  current="${!var:-}"
  [[ -n "${current}" ]] || die "${var} is not set; point it at the admin file (see header)"
  [[ "$(cd "$(dirname "${current}")" 2>/dev/null && pwd)/$(basename "${current}")" != "${agent_file}" ]] \
    || die "${var} points at the agent file; use the admin credentials to mint new ones"
done

kubectl -n kube-system get serviceaccount agent-readonly >/dev/null \
  || die "ServiceAccount kube-system/agent-readonly not found; is the agent-readonly Kustomization reconciled?"

server="$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.server}')"
ca_data="$(kubectl config view --minify --raw -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
token="$(kubectl -n kube-system create token agent-readonly --duration="${DURATION}")"

umask 077
cat > "${AGENT_KUBECONFIG}" <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: home-ops
    cluster:
      server: ${server}
      certificate-authority-data: ${ca_data}
users:
  - name: agent-readonly
    user:
      token: ${token}
contexts:
  - name: agent-readonly@home-ops
    context:
      cluster: home-ops
      user: agent-readonly
current-context: agent-readonly@home-ops
EOF

# The API server can cap token lifetime below the request; report the real expiry.
expiry="$(python3 - "${token}" <<'PY'
import base64, datetime, json, sys
payload = sys.argv[1].split(".")[1]
claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
print(datetime.datetime.fromtimestamp(claims["exp"], datetime.timezone.utc).isoformat())
PY
)"

rm -f "${AGENT_TALOSCONFIG}"
talosctl config new "${AGENT_TALOSCONFIG}" --roles os:reader --crt-ttl "${DURATION}"

echo "wrote ${AGENT_KUBECONFIG} (token expires ${expiry})"
echo "wrote ${AGENT_TALOSCONFIG} (os:reader, ttl ${DURATION})"
echo "check: KUBECONFIG=${AGENT_KUBECONFIG} kubectl auth can-i create pods -A   # expect: no"
