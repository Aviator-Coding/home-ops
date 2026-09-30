# Hermes MCP gateway env hydration

`mcp_servers.toolhive` in `config.yaml` uses `${MCP_GATEWAY_URL}` and
`${MCP_GATEWAY_TOKEN}`. Both live in the `hermes` Secret and reach the
container env via `envFrom`, but that is not enough.

## Why the env is not enough

The gateway runs with `gateway.multiplex_profiles: true` (set on the PVC
copy of the config, not in Git). Under multiplexing a `${VAR}` resolves only
from the profile secret scope: `/opt/data/.env` plus `secrets:` sources.
A miss stays a literal, and Hermes logs
`Invalid MCP URL for 'toolhive': scheme must be http or https, got ''`
every few minutes. A fresh shell in the pod expands the vars fine, which
makes the manifest and Secret look correct.

## What fixes it

- `secrets.command` in `config.yaml` prints the two vars as `KEY=VALUE`.
- The helper also runs with the profile view of the env, not the container
  env, so it cannot `printenv`. It reads the two keys from files instead: the
  `mcp-gateway` persistence entry in `helmrelease.yaml` mounts them at
  `/secrets/mcp/`.
- There is no `op` binary in the image, so the `1password` source is not an
  option without changing the image.

Keep the mounts and the helper in step: renaming a path in one breaks the
other silently (the vars stay literal and only the log shows it).

## Verify

- Log: no `Invalid MCP URL for 'toolhive'` after the pod restarts.
- Tools: the toolhive tools list from Hermes once the vmcp reply path is fixed.
- Scratch proof (no PVC, same image): set `multiplex_profiles`, call
  `hydrate_profile_secret_sources`, `build_profile_secret_scope`,
  `set_secret_scope`, then `load_config()`. Without the `secrets:` block the
  scope is empty and the refs stay literal.
