set quiet
set script-interpreter := ['bash', '-euo', 'pipefail']
set shell := ['bash', '-euo', 'pipefail', '-c']

[private]
default:
    just -l talos

[confirm('Apply Talos config to this node? [y|N]')]
[doc('Apply Talos config to a node (node = talos-1|talos-2|talos-3). Pass --dry-run / --insecure as extra args.')]
[script]
apply-node node *args:
    ip="$(just talos _ip {{ node }})"
    talosctl -n "$ip" apply-config -f <(just talos render-config {{ node }}) {{ args }}

[doc('Render Talos machine config for a node to stdout (node = talos-1|talos-2|talos-3)')]
[script]
render-config node:
    export CONTROLPLANE="$(just talos _machine-controller {{ node }})"
    export SCHEMATIC="$(just talos _schematic-id)"
    talosctl machineconfig patch \
        <(just template "{{ source_directory() }}/machineconfig.yaml.j2") \
        -p @<(just template "{{ source_directory() }}/nodes/{{ node }}.yaml.j2")

[confirm('Upgrade Talos on this node? [y|N]')]
[doc('Upgrade Talos version on a node (install image derived from the rendered config)')]
[script]
upgrade-node node *args:
    ip="$(just talos _ip {{ node }})"
    just talos render-config {{ node }} > /dev/null # validate render before touching the node
    talosctl -n "$ip" upgrade -i "$(just talos _machine-image {{ node }})" -m powercycle --timeout=10m {{ args }}

[confirm('Upgrade Kubernetes to this version? [y|N]')]
[doc('Upgrade the Kubernetes version on the cluster')]
upgrade-k8s version:
    talosctl -n "$(talosctl config info -o yaml | yq -e '.endpoints[0]')" upgrade-k8s --to {{ version }}

[confirm('Reboot this node? [y|N]')]
[doc('Reboot a node')]
[script]
reboot-node node:
    talosctl -n "$(just talos _ip {{ node }})" reboot -m powercycle

[confirm('Shutdown this node? [y|N]')]
[doc('Shutdown a node')]
[script]
shutdown-node node *args:
    talosctl -n "$(just talos _ip {{ node }})" shutdown --force {{ args }}

[confirm('Reset this node to maintenance mode (wipes STATE + EPHEMERAL)? [y|N]')]
[doc('Reset a node back to maintenance mode')]
[script]
reset-node node *args:
    talosctl -n "$(just talos _ip {{ node }})" reset \
        --system-labels-to-wipe STATE --system-labels-to-wipe EPHEMERAL \
        --graceful=false {{ args }}

[doc('Download a Talos metal ISO for the current schematic (version = vX.Y.Z)')]
[script]
download-image version:
    export SCHEMATIC="$(just talos _schematic-id)"
    curl -fL --remove-on-error --retry 5 --retry-delay 5 --retry-all-errors --progress-bar \
        -o "{{ source_directory() }}/talos-{{ version }}-${SCHEMATIC:0:8}.iso" \
        "https://factory.talos.dev/image/${SCHEMATIC}/{{ version }}/metal-amd64.iso"
    just log info "Downloaded Talos" version "{{ version }}" schematic "${SCHEMATIC}"

# --- private helpers ---

# Map a node name to its talosctl target IP
[private]
[script]
_ip node:
    case "{{ node }}" in
        talos-1) echo "10.10.10.11" ;;
        talos-2) echo "10.10.10.12" ;;
        talos-3) echo "10.10.10.13" ;;
        *) echo "unknown node: {{ node }} (expected talos-1|talos-2|talos-3)" >&2; exit 1 ;;
    esac

# Compute the factory schematic id from schematic.yaml.j2
[private]
_schematic-id:
    just template "{{ source_directory() }}/schematic.yaml.j2" \
        | curl -sX POST --data-binary @- "https://factory.talos.dev/schematics" \
        | jq -r '.id'

# Returns "true" when the node template declares machine.type == controlplane
[private]
_machine-controller node:
    just template "{{ source_directory() }}/nodes/{{ node }}.yaml.j2" | yq -e 'select(.machine) | (.machine.type == "controlplane") // ""'

# Extract machine.install.image from the rendered machine config for a node
[private]
[script]
_machine-image node:
    export CONTROLPLANE="$(just talos _machine-controller {{ node }})"
    export SCHEMATIC="$(just talos _schematic-id)"
    just template "{{ source_directory() }}/machineconfig.yaml.j2" \
        | yq -e 'select(.machine) | .machine.install.image'
