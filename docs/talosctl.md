# talosctl

Operator recipes are in [`talos/AGENTS.md`](../talos/AGENTS.md), invoked as `just talos <recipe>`.

Node addresses (not the VIP):

| Node | Address |
|---|---|
| talos-1 | `10.10.10.11` |
| talos-2 | `10.10.10.12` |
| talos-3 | `10.10.10.13` |
| control-plane VIP | `10.10.10.10` (do not pass as `-n` for node apply/upgrade) |

```bash
just talos render-config talos-1
just talos apply-node talos-1 --dry-run
just talos apply-node talos-1
talosctl -n 10.10.10.11 get rd
```

`talosconfig` is `talos/talosconfig` (gitignored).
