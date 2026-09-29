# talosctl

Recipes, apply versus upgrade, and the node roll: skill `talos-nodes`. Offline pointer: [`talos/AGENTS.md`](../talos/AGENTS.md).

| Node | Address |
|---|---|
| talos-1 | `10.10.10.11` |
| talos-2 | `10.10.10.12` |
| talos-3 | `10.10.10.13` |
| control-plane VIP | `10.10.10.10` (not a `talosctl -n` target) |

`talosconfig` is `talos/talosconfig` (gitignored). Resource get names used from other notes (`link`, `links`, `linkstatus`, `ethtool`, `address`) are `talosctl get` verbs, not a second config file.
