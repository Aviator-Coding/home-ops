# talos-3 B70 reboot

The Arc Pro B70 sits on an OCuLink dock with its own PSU. The root port reports `SLTCAP PowerController=0` and `HotPlugCapable=0`, so a host powercycle does not cycle the dock and cannot rediscover a card that trained late.

`pcie_port_pm=off` in `talos/schematic.yaml.j2` stops the root port (`00:01.0`) from runtime-suspending to D3hot while it waits. `pcie_aspm=off` does not do that: ASPM is link power, and port runtime D-state is a separate layer. The arg is not retroactive. It also does not replace dock-PSU-first power-on once a card has already been lost.

The schematic is cluster-wide, so all three nodes get the arg. Only talos-3 has the card.

Kernel args and extensions boot only through `just talos upgrade-node` (`talosctl upgrade -i <image> -m powercycle`). `just talos apply-node` stages machine config and does not boot a new schematic. Skill `talos-nodes`.

Do not re-add `siderolabs/thunderbolt`. Keep `pci=assign-busses`, `xe.force_probe=a7a0` and `i915.force_probe=!a7a0` (the i915 extension still ships `i915.ko`). Recompute the factory schematic id after any edit:

```sh
just template talos/schematic.yaml.j2 \
  | curl -sX POST --data-binary @- https://factory.talos.dev/schematics | jq -r .id
```

## Before the reboot

talos-3 hosts OSDs 2 and 4. `ceph -s` must be `HEALTH_OK` and `task rook:check-osd-device-paths` clean. One node at a time. Skill `rook-ceph`.

Capture the baseline while the card is up (`TALOSCONFIG` must point at a real talosconfig; the file is gitignored):

```sh
N=10.10.10.13
talosctl -n $N read /proc/cmdline | tr ' ' '\n' | grep pcie
talosctl -n $N read /sys/bus/pci/devices/0000:03:00.0/device    # 0xe223
talosctl -n $N read /sys/bus/pci/devices/0000:03:00.0/uevent    # DRIVER=xe
talosctl -n $N read /sys/bus/pci/devices/0000:00:01.0/power_state   # D0
kubectl get nodes -o custom-columns='NAME:.metadata.name,B70:.status.allocatable.devic\.es/b70'
```

If `pcie_port_pm=off` is absent from cmdline, this node is still on an older schematic. `upgrade-node` is what boots the current one.

## If the card is missing after boot

1. Power talos-3 fully off.
2. Dock PSU on first.
3. Wait 5-10 seconds. Fans spin, card LED lit.
4. Only then power the host.

Never power the dock and the host together, and never host-first.

## After

```sh
talosctl -n $N read /proc/cmdline | tr ' ' '\n' | grep pcie_port_pm   # pcie_port_pm=off
talosctl -n $N read /sys/bus/pci/devices/0000:03:00.0/device          # 0xe223
talosctl -n $N read /sys/bus/pci/devices/0000:03:00.0/uevent          # DRIVER=xe
talosctl -n $N read /sys/bus/pci/devices/0000:00:01.0/power_state     # D0
talosctl -n $N get extensions                                         # no thunderbolt
```

`devic.es/b70` allocatable is 99 on talos-3. The workloads that need the card are `ai/vllm`, `ai/embedding-gpu` and the tdarr node. A VA-API consumer must use `devic.es/b70-vaapi` (skill `intel-gpu`). Allocatable capacity is not proof that transcoding works.
