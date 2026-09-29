# Hermes

Homelab operator. Image tag stays on the `-desktop` suffix (Chromium and
the VNC stack are baked in; the process cannot `apt install` after it
drops to uid 10000). Skill `hermes-agent` owns restarts, `state.db`,
routing, and Samba. This page is the human setup.

| Container | URL |
| --- | --- |
| `app` (dashboard, basic auth) | `https://hermes.${SECRET_DOMAIN}` |
| `codeserver` (no auth of its own, internal gateway only) | `https://hermes-code.${SECRET_DOMAIN}` |

Ports: `9119` dashboard, `8642` API, `12321` code-server. Mounts:
`/opt/data` (RWO, single writer), `/opt/xml`, `/opt/files`.

## Config is Git

`copy-config` copies `app/resources/config.yaml` onto the PVC on every
start. Edit that file and commit. `hermes config edit` in the pod is
overwritten on the next restart. Any byte of that file or a runtime
skill restarts the pod. Do not edit them to tidy comments.

## Browser and Bot Desktop

`AGENT_BROWSER_ARGS` is set because the image's sandbox auto-detection
never fires (uid 10000, no AppArmor on Talos). Without
`--no-sandbox,--disable-dev-shm-usage,--disable-gpu`, Chromium hangs.

Bot Desktop is opt-in (`bot_desktop.auto_start` stays false). VNC listens
on a mode-0600 unix socket only. Open it from the dashboard Screen pane,
or `hermes computer-use screen status|start|stop`. The viewer is the
existing dashboard WebSocket. `min_free_memory_mb` defaults to 1536,
inside the 6Gi app limit.

## When a restart looks healthy and is not

Probes are TCP on `9119`. The pod stays Running and Gatus stays green
while the gateway is stuck. Read
`/opt/data/logs/gateway-startup-watchdog.log`. Do not add a gateway
liveness probe. Skill `hermes-agent` references/restarts.md.

`sessions.vacuum_after_prune` stays false. Narrowing `retention_days`
is the dial. Reclaiming disk is an offline
`hermes sessions optimize-storage` after there is free space. Skill
`hermes-agent` references/state-db.md.

## Cluster RBAC

ServiceAccount `hermes`. `hermes-read-all` is get/list/watch across the
homelab. `hermes-pod-delete` can delete a wedged pod.
`hermes-exec-deploy` is `pods/exec` and deployment rollout-restart in
namespace `ai` only. Widen that Role on purpose. It is not read-only.

## Memory and skills

Long-term memory is the bundled `holographic` provider
(`memory.provider: holographic`), a SQLite store on this pod. It is not
shared with other agents. `agentmemory` is retired. Skill `ai-stack`.

Runtime skills live in `app/skills/` and are copied to `/opt/data/skills`
on start. Shipped: `homelab-commit-watcher` (Discord digest). Register
its cron once in the dashboard after the pod is up. The skill file is a
payload: editing it restarts Hermes.

## Telegram

DM the bot from an allowed user. `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_ALLOWED_USERS` come from the `hermes` 1Password item.

## Prerequisites

1Password item `hermes`:

| Field | What it is |
| --- | --- |
| `HERMES_DASHBOARD_USER`, `HERMES_DASHBOARD_PASSWORD` | Dashboard basic auth |
| `HERMES_DASHBOARD_SECRET` | `openssl rand -hex 32` |
| `GITHUB_LLM_WIKI_TOKEN` | Fine-grained PAT, one repo, Contents read and write. Not a classic PAT |
| `API_SERVER_KEY` | `openssl rand -hex 32` for the `:8642` API |
| `HOMELAB_GH_TOKEN` | Classic PAT, `public_repo` only. Commit-watcher. Distinct from the wiki token. OpenCode reuses this field |
| `DISCORD_WEBHOOK` | Channel webhook for the commit digest |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS` | BotFather token and numeric user ids |

`MCP_GATEWAY_TOKEN` (the ToolHive MCP gateway bearer token) is not a
prerequisite. It comes from 1Password item `mcp-gateway`, which
`ai/toolhive`'s PushSecret writes.

Git access is that one repo-scoped fine-grained PAT, rendered to
`/secrets/git/.git-credentials`. GitHub rejects it for every other repo.
Do not substitute a classic PAT or an account SSH key.
