# Hermes Agent

[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — a
self-improving conversational AI agent (learning loop, persistent memory,
skills). Self-contained Python image; no upstream Helm chart, so this is a
hand-authored `app-template` deploy.

Dashboard (chat UI) at `https://hermes.${SECRET_DOMAIN}` (basic auth).

## LLM backend

Hermes picks its model/provider from **`/opt/data/config.yaml`** (on the PVC), not
from env. That file is seeded by the `seed-config` initContainer from the
`hermes-config-seed` ConfigMap (`configmap.yaml`) **only if absent** — Hermes owns
it afterwards (`hermes model` / `hermes config edit` rewrite it). Two providers are
configured:

| Provider | Routing | Auth |
| -------- | ------- | ---- |
| `custom:gateway` | agentgateway `internal-noauth` → `/opencodego/v1` (kimi etc.) | keyless (gateway injects the opencodego key) |
| `xai-oauth` (native) | **direct to xAI**, bypassing agentgateway | OAuth (Grok subscription, see below) |

The default is `grok-4.3` via `xai-oauth`. Hermes needs a **≥64k context**,
tool-calling model — Grok and kimi both qualify.

> **No `OPENAI_BASE_URL`.** It used to point Hermes at the gateway, but it's a
> *global* OpenAI-SDK override that pins the endpoint for **every** provider — it
> was sending `grok-4.3` to `/opencodego`. Per-provider `base_url` in
> `config.yaml` is the correct layer, so the `OPENAI_*` env vars were removed. If
> Hermes ever won't boot without an OpenAI key, re-add a dummy `OPENAI_API_KEY`
> **only** (never `OPENAI_BASE_URL`).

**Switching models:** edit the ConfigMap + re-seed (below), or at runtime
`kubectl -n ai exec -it deploy/hermes -c app -- hermes model` (writes config.yaml
on the PVC). Add a 429 fail-over chain with `hermes fallback add` (e.g. primary
`xai-oauth/grok-4.3`, fallback `custom:gateway/kimi-k2.6`).

**Re-seed config.yaml** (after editing the ConfigMap, or to reset):
```bash
kubectl -n ai exec deploy/hermes -c app -- rm -f /opt/data/config.yaml
kubectl -n ai rollout restart deploy/hermes
```

### xAI Grok subscription login (manual, one-time)

The Grok **subscription** is used via Hermes' native `xai-oauth` provider (OAuth),
which can't be done through the gateway (that path needs a console.x.ai API key, a
separate product). The OAuth loopback callback can't reach a pod from your laptop,
so use `--manual-paste`:

```bash
kubectl -n ai exec -it deploy/hermes -c app -- hermes auth add xai-oauth --no-browser --manual-paste
```

Open the printed URL, approve, then paste the **full** failed-callback URL (or a
`?code=...&state=...` fragment, or a fresh bare code) at the prompt. Codes expire in
~minutes; if it tracebacks on a stale/reused login, `hermes auth remove xai-oauth`
and retry for a fresh `state`. Credentials persist in `/opt/data/auth.json` (PVC →
Volsync-backed; `offline_access` refresh token renews automatically), so this is a
one-time step per fresh PVC.

**Local models later:** when the local vLLM backend exists, add a backend +
HTTPRoute under `../agentgateway/app/backends/` and a `custom_providers` entry
pointing at `.../vllm/v1`. Nothing else in this deploy changes.

## Prerequisites (manual, before first sync)

1. **1Password item `hermes`** (vault used by the `onepassword`
   ClusterSecretStore) with fields:
   - `HERMES_DASHBOARD_USER`
   - `HERMES_DASHBOARD_PASSWORD`
   - `HERMES_DASHBOARD_SECRET` — `openssl rand -hex 32`
   - `GITHUB_LLM_WIKI_TOKEN` — fine-grained GitHub PAT (see **Git access** below)
2. **Authenticate the Grok subscription** once the pod is up — run the
   `hermes auth add xai-oauth --manual-paste` flow in **LLM backend → xAI Grok
   subscription login** above. (Until then Hermes has no working provider unless
   you switch the seed to `custom:gateway`.)

## Git access (single private repo)

Hermes is given access to **exactly one** private repo, without exposing any
other repo on the account:

1. **Create a fine-grained PAT** (GitHub → Settings → Developer settings →
   Fine-grained tokens):
   - **Repository access:** *Only select repositories* → pick the one repo.
   - **Permissions:** *Contents* → **Read and write** (write enabled here).
   - Set an expiry; rotate by updating `GITHUB_LLM_WIKI_TOKEN` in 1Password
     (reloader restarts the pod automatically).
2. Put the token in the `hermes` 1Password item as `GITHUB_LLM_WIKI_TOKEN`.

The `hermes-git` ExternalSecret renders it into a `.git-credentials` file
(`https://x-access-token:<token>@github.com`); the file is mounted read-only at
`/secrets/git/.git-credentials` and consumed by git's `store` credential helper,
which is wired up purely through `GIT_CONFIG_*` env (no writable `$HOME`). A
commit identity (`user.name` / `user.email`) is set for pushes — change the
`GIT_CONFIG_VALUE_1/2` env if you want a different author.

Because the PAT is **repo-scoped server-side**, this is the security boundary:
even though git may present it for any `github.com` URL, GitHub rejects it for
any repo outside the selected one. (A *classic* PAT or an account SSH key would
expose every repo — don't use those.)

> **Other hosts / SSH:** for GitLab use a *project* access/deploy token the same
> way. For an even tighter, key-based boundary, a per-repo **deploy key**
> (SSH, write-enabled) also works, but needs careful key-file permissions when
> mounted from a Secret — the HTTPS PAT above avoids that.

## Notes

- **Single-writer state.** `/opt/data` (sessions/memories/skills, incl. SQLite)
  is not concurrency-safe → `replicas: 1` + `strategy: Recreate` + RWO
  `ceph-block` PVC. Do not scale up.
- **Gateway runs via the profile service, not the CMD.** The image auto-starts a
  `gateway-default` s6 service (the `default` profile gateway: cron + messaging).
  The container CMD is therefore idled (`args: ["sleep","infinity"]`) — passing
  `gateway run` started a *second* gateway that collided at startup and
  CrashLooped the pod. The `dashboard` s6 service serves chat independently.
  Don't set `args` back to `gateway run`.
- **Runs as root then drops** to UID 10000 (s6-overlay), so no `runAsNonRoot`
  here — `fsGroup: 10000` makes the PVC writable for the dropped user.
- **Cost tracking:** the default `xai-oauth` Grok path talks to xAI **directly**,
  bypassing agentgateway, so it does **not** appear in gateway cost/Tempo tracking
  at all (a flat subscription, so there's no per-token spend anyway). Only the
  `custom:gateway` path is metered by the gateway.
- **Ports:** `9119` dashboard (exposed), `8642` OpenAI-compatible API
  (not enabled here — set `API_SERVER_ENABLED=true` + `API_SERVER_KEY` to use it).
