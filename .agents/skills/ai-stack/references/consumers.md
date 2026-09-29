# OpenCode and repo-wiki

Both are internal-only app-template workloads. Both call LiteLLM at
`http://litellm.ai.svc.cluster.local:4000/v1` with a
`LiteLLMVirtualKey`. Never a provider API key, and never agentgateway's
`/v1` for governance. Skill `litellm-proxy` owns the key, the allow-list,
and the budget. Changing the model on one side only fails every call:
the proxy checks the model the caller asks for.

## OpenCode

- Default model `litellm/auto`. This cluster has one local chat model.
  Do not copy a reference layout of per-GPU subagent roles onto it.
- UI route is Authentik ext-auth on `envoy-internal` only
  (`app/securitypolicy.yaml`). No second basic-auth API route.
- `GITHUB_TOKEN` is the existing `hermes` 1Password item field
  `HOMELAB_GH_TOKEN` (`public_repo`, read and write). No new item.
- ToolHive MCP goes through the LAN route `mcp.${SECRET_DOMAIN}/mcp` with
  `Authorization: Bearer {env:MCP_GATEWAY_TOKEN}`. URL and token come from
  the `opencode` Secret (1Password `mcp-gateway`). The vmcp Service is closed
  to it by NetworkPolicy. [toolhive.md](toolhive.md) "Gateway auth".
- The memory plugin and Context7 were not carried over. `agentmemory` is
  retired, so a future memory backend is new work.
  [retirement.md](retirement.md)
- Backup is kopiur on the `home` claim. The overlay owns the include.
  Skill `kopiur-backups`. Do not add VolSync.

## repo-wiki

- Generator model is the local alias `chat-local`. It must stay on the
  `repo-wiki` virtual key's allow-list. Spend on this path is the local
  model. The bounds that matter are `rpmLimit` 12, `tpmLimit` 200000,
  and `MAX_REPOS_PER_RUN=1`.
- `GITHUB_TOKEN` is **not** `HOMELAB_GH_TOKEN`. That token can push.
  repo-wiki needs its own read-only credential. The human README holds
  the 1Password prerequisite.
- Repo list is `app/resources/repos.txt` only. Do not restate it.
- Content is regenerable and still backed up, so a wipe does not force
  a full regeneration before pages are served. kopiur is the only
  engine. Skill `kopiur-backups`.
- `configMapGenerator` builds the ConfigMap from `app/resources/`. Those
  files are the generator input. A byte change rolls the pod.

## SearxNG

Hermes' web search. The URL pin and the "do not point web search at a
paid provider" rule live in skill `hermes-agent`. This app is the
deployment those settings name.
