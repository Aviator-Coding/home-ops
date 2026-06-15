# Hermes Agent

[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — a
self-improving conversational AI agent (learning loop, persistent memory, skills),
deployed here as a homelab operator. Self-contained Python image; no upstream Helm
chart, so this is a hand-authored `app-template` deploy.

The pod runs three containers (one controller, one PVC):

| Container | Purpose | URL |
| --------- | ------- | --- |
| `app` | Hermes gateway + built-in dashboard (basic auth) | `https://hermes.${SECRET_DOMAIN}` |
| `webui` | Richer standalone chat UI ([nesquena/hermes-webui](https://github.com/nesquena/hermes-webui)) | `https://hermes-webui.${SECRET_DOMAIN}` (LAN) |
| `codeserver` | Browser VS Code over `/opt/data` (skills/config/sessions) | `https://hermes-code.${SECRET_DOMAIN}` (LAN) |

Plus an OpenAI-compatible API on `:8642` and a companion **agentmemory** service
(`../agentmemory/`) for long-term cross-session memory.

> The `app` container exposes a terminal + cluster RBAC + git, so the dashboard is
> gated by basic auth. `webui` and `codeserver` have **no auth of their own** — they
> are only on the **internal** gateway (`envoy-internal`, LAN). Front them with
> Authentik if you ever need more than network isolation.

## Configuration is GitOps (read this first)

Hermes reads its model/provider config from **`/opt/data/config.yaml`** (on the PVC).
That file is **owned by Git**: the `copy-config` initContainer copies
[`app/resources/config.yaml`](app/resources/config.yaml) into the PVC **on every pod
start**, and reloader restarts the pod when the ConfigMap changes.

**Change models/providers/skills config by editing `app/resources/config.yaml` and
committing** — *not* with in-pod `hermes model` / `hermes config edit`, whose writes
are overwritten on the next restart.

### LLM backend

| Provider | Routing | Auth |
| -------- | ------- | ---- |
| `custom:local` (**default** `qwen3.6-35b-a3b`) | agentgateway `internal-noauth` → `/vllm/v1` (llama.cpp on the B70) | keyless (LAN-only gateway) |
| `custom:gateway` (`kimi-k2.6`) | agentgateway `internal-noauth` → `/opencodego/v1` | keyless (gateway injects the key) |
| `xai-oauth` (native, `grok-4.3`) | direct to xAI, bypassing the gateway | OAuth (Grok subscription, see below) |

`config.yaml` also sets:
- **Fallback chain** — `custom:local/qwen3.6-35b-a3b` → `custom:gateway/kimi-k2.6` →
  `xai-oauth/grok-4.3`, so a local outage (e.g. ComfyUI scaling `vllm` to 0 under the
  Dedicated-VRAM runbook) doesn't kill the session. The local default is free and
  rate-limit-proof, so long tasks don't hit the xAI throttle or OpenCode-Go limits.
- **Auxiliary routing** — compression / web_extract / session_search / title run on kimi
  via the gateway, **not** the local model: `web_extract` fires N parallel LLM calls (one
  per page) that the single-slot local server can't serve concurrently (they time out).
  Aux volume is low/bursty so it won't hit the cloud limits the main loop did. Aux fallback
  uses a **per-task `fallback_chain`** (the global `fallback_providers` is main-loop only),
  so the heavy chores pin a Grok fallback. `vision` is pinned to Grok (`xai-oauth`) since
  the local 35B is text-only.
- **Web search** — `web.search_backend: searxng`, wired to the in-cluster SearXNG
  via `SEARXNG_URL`.

> **No `OPENAI_BASE_URL`.** It's a *global* OpenAI-SDK override that pins the endpoint
> for **every** provider. Per-provider `base_url` in `config.yaml` is the right layer.
> If Hermes ever won't boot without an OpenAI key, re-add a dummy `OPENAI_API_KEY`
> **only** (never `OPENAI_BASE_URL`).

### xAI Grok subscription login (manual, one-time)

The Grok **subscription** is used via Hermes' native `xai-oauth` provider (OAuth),
which can't go through the gateway. The OAuth loopback can't reach a pod from your
laptop, so use `--manual-paste`:

```bash
kubectl -n ai exec -it deploy/hermes -c app -- hermes auth add xai-oauth --no-browser --manual-paste
```

Open the printed URL, approve, then paste the **full** failed-callback URL (or a
`?code=...&state=...` fragment, or a fresh bare code). Codes expire in ~minutes; on a
stale/reused login, `hermes auth remove xai-oauth` and retry for a fresh `state`.
Credentials persist in `/opt/data/auth.json` (PVC, Volsync-backed; the refresh token
renews automatically), so this is one-time per fresh PVC.

## Cluster RBAC (operator access)

Hermes runs under its own `hermes` ServiceAccount (`automountServiceAccountToken: true`
— app-template v5 defaults it to false) with:

- **`hermes-read-all`** (ClusterRole) — get/list/watch across core, apps, batch,
  networking, gateway, storage, metrics, Flux (helm/kustomize/source), External
  Secrets, Volsync and CNPG. Hermes can fully inspect/diagnose the homelab.
- **`hermes-pod-delete`** (ClusterRole) — delete a wedged pod so a controller
  reschedules it.
- **`hermes-exec-deploy`** (Role, ns `ai`) — `pods/exec` (diagnostics) and
  `deployments` patch/update (rollout-restart) in this namespace only.

This is read-everywhere + a narrow self-heal write surface. Widen the
`hermes-exec-deploy` Role (or add namespaced bindings) if you want it to operate
beyond `ai`.

## Long-term memory (agentmemory)

The `../agentmemory/` app runs the memory service; this app mounts the Hermes
[memory plugin](app/agentmemory-plugin.yaml) at `/opt/hermes/plugins/agentmemory`
and enables it via `plugins.enabled: [agentmemory]`. Hermes gets `memory_recall` /
`memory_save` tools and auto-saves turns; recall survives restarts and new sessions.

**agentmemory needs an embeddings endpoint** — it points at the agentgateway's
no-auth internal listener (`OPENAI_BASE_URL=http://internal-noauth.ai.svc.cluster.local`),
which routes root `/v1/embeddings` to **qwen3-vl-embedding-2b** (vllm-embed, 2048-dim)
and `/v1/chat/completions` to the local **qwen3.6-35b-a3b** (llama.cpp on the B70).
Both models live in the `vllm` app and persist on PVCs — nothing to pull manually.

## Skills (GitOps-managed)

Agent skills are version-controlled in [`app/skills/`](app/skills/) and copied onto
the PVC at `/opt/data/skills` by the `seed-skills` init on every start. Add a skill
by dropping a `skills/<name>/` dir and a matching `configMapGenerator` entry +
persistence mount.

Shipped: **`homelab-commit-watcher`** — ranks interesting commits across `k8s-at-home`
peers and posts a digest to a **Discord webhook** (`DISCORD_WEBHOOK`). It uses
`HOMELAB_GH_TOKEN` (a `public_repo` PAT — deliberately *not* `GH_TOKEN`, which Hermes
scrubs) and the gateway for per-repo summaries. Register its daily run in-agent once
the pod is up:

```bash
# in the dashboard/webui terminal, or: kubectl -n ai exec -it deploy/hermes -c app -- hermes ...
# create the `homelab-peers-commit-watcher` cron (see the skill's SKILL.md).
```

## Telegram

DM the bot from an allowed user. The gateway-default profile starts the Telegram
platform when `TELEGRAM_BOT_TOKEN` is present; `TELEGRAM_ALLOWED_USERS` gates access.
Both come from the `hermes` secret.

## Prerequisites (before first sync)

1. **1Password item `hermes`** (the `onepassword` ClusterSecretStore vault):
   - `HERMES_DASHBOARD_USER` / `HERMES_DASHBOARD_PASSWORD`
   - `HERMES_DASHBOARD_SECRET` — `openssl rand -hex 32`
   - `GITHUB_LLM_WIKI_TOKEN` — fine-grained, single-repo PAT (see **Git access**)
   - `API_SERVER_KEY` — `openssl rand -hex 32` (OpenAI-compatible API on `:8642`)
   - `HOMELAB_GH_TOKEN` — classic GitHub PAT, **`public_repo` only** (commit-watcher)
   - `DISCORD_WEBHOOK` — channel → Integrations → Webhooks → New
   - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USERS` — BotFather token + numeric ids
2. **1Password item `agentmemory`** with `AGENTMEMORY_SECRET` — `openssl rand -hex 32`
   (shared between the agentmemory service and the Hermes plugin).
3. **Authenticate the Grok subscription** once the pod is up (xAI Grok login above).

## Git access (single private repo)

Hermes gets access to **exactly one** private repo without exposing any other:

1. **Fine-grained PAT** (GitHub → Settings → Developer settings → Fine-grained
   tokens): *Repository access* → *Only select repositories* → the one repo;
   *Permissions* → *Contents* → **Read and write**. Set an expiry.
2. Store it in the `hermes` 1Password item as `GITHUB_LLM_WIKI_TOKEN`.

The `hermes-git` ExternalSecret renders it into a `.git-credentials` file mounted
read-only at `/secrets/git/.git-credentials`, consumed by git's `store` helper
(wired via `GIT_CONFIG_*` env, no writable `$HOME`). Because the PAT is repo-scoped
server-side, GitHub rejects it for any other repo — that's the boundary. (A *classic*
PAT or account SSH key would expose every repo; don't use those.)

> This `GITHUB_LLM_WIKI_TOKEN` (write, one repo) is separate from the commit-watcher's
> `HOMELAB_GH_TOKEN` (read-only, public repos). Keep them distinct.

## Notes

- **Single-writer state.** `/opt/data` (sessions/memories/skills, incl. SQLite) is not
  concurrency-safe → `replicas: 1` + `strategy: Recreate` + RWO `ceph-block` PVC. The
  `codeserver` and `webui` sidecars share the PVC **in the same pod** (no
  multi-attach). Do not scale up.
- **Gateway runs via the profile service, not the CMD.** The image auto-starts a
  `gateway-default` s6 service (the gateway: cron + messaging). The container CMD is
  idled (`args: ["sleep","infinity"]`) — passing `gateway run` started a *second*
  gateway that collided and CrashLooped. **Don't set `args` back to `gateway run`.**
- **webui is the fiddly one.** It imports a staged copy of the image's `/opt/hermes`
  (the `copy-agent-source` init → `agent-source` emptyDir) and reads agent state from
  the PVC at `/home/hermeswebui/.hermes`. The `app` container's `/opt/hermes` is left
  pristine on purpose (don't disturb the gateway). If webui misbehaves, it can be
  removed without touching the rest — see the disable note in `helmrelease.yaml`.
- **Runs as root then drops** to UID 10000 (s6-overlay), so no `runAsNonRoot` on the
  `app` container — `fsGroup: 10000` makes the PVC writable for the dropped user.
- **Cost tracking:** the default `custom:local` and the `custom:gateway` paths go
  through the agentgateway, so they're in gateway cost/Tempo tracking. The `xai-oauth`
  Grok path (now fallback + `vision`) talks to xAI **directly**, bypassing the gateway,
  so it isn't tracked (flat subscription anyway).
- **Ports:** `9119` dashboard, `8642` OpenAI-compatible API, `8787` webui, `12321`
  code-server.
