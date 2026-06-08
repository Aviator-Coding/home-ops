# Hermes Agent

[NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — a
self-improving conversational AI agent (learning loop, persistent memory,
skills). Self-contained Python image; no upstream Helm chart, so this is a
hand-authored `app-template` deploy.

Dashboard (chat UI) at `https://hermes.${SECRET_DOMAIN}` (basic auth).

## LLM backend

All LLM traffic goes through **agentgateway** (the single model gateway), like
every other AI app here. Hermes points at the keyless `internal-noauth`
gateway, which injects the real provider key:

| Setting | Value |
| ------- | ----- |
| `OPENAI_BASE_URL` | `http://internal-noauth.ai.svc.cluster.local/xai/v1` |
| `OPENAI_API_KEY`  | placeholder (gateway injects the real xAI key) |
| `OPENAI_MODEL`    | a model your xAI subscription serves (**set this**) |

Hermes requires a **≥64k token context window** and a tool-calling-capable
model — Grok models satisfy both.

**Local models later:** when the local vLLM backend exists, add a backend +
HTTPRoute under `../agentgateway/app/backends/` and change only `OPENAI_BASE_URL`
(e.g. `.../vllm/v1`). Nothing else in this deploy changes.

## Prerequisites (manual, before first sync)

1. **1Password item `hermes`** (vault used by the `onepassword`
   ClusterSecretStore) with fields:
   - `HERMES_DASHBOARD_USER`
   - `HERMES_DASHBOARD_PASSWORD`
   - `HERMES_DASHBOARD_SECRET` — `openssl rand -hex 32`
   - `GITHUB_LLM_WIKI_TOKEN` — fine-grained GitHub PAT (see **Git access** below)
2. **Confirm the model id**: list what xAI serves and set `OPENAI_MODEL`:
   ```bash
   kubectl -n ai run -it --rm curl --image=curlimages/curl --restart=Never -- \
     -s http://internal-noauth.ai.svc.cluster.local/xai/v1/models
   ```

## Git access (single private repo)

Hermes is given access to **exactly one** private repo, without exposing any
other repo on the account:

1. **Create a fine-grained PAT** (GitHub → Settings → Developer settings →
   Fine-grained tokens):
   - **Repository access:** *Only select repositories* → pick the one repo.
   - **Permissions:** *Contents* → **Read and write** (write enabled here).
   - Set an expiry; rotate by updating `GIT_TOKEN` in 1Password (reloader
     restarts the pod automatically).
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
- **Runs as root then drops** to UID 10000 (s6-overlay), so no `runAsNonRoot`
  here — `fsGroup: 10000` makes the PVC writable for the dropped user.
- **Cost tracking:** Grok models have no price row in
  `../agentgateway/app/rules/cost.yaml`, so spend shows under "unpriced models"
  until you add `grok-*` input/output rows. Token counts are still tracked.
- **Ports:** `9119` dashboard (exposed), `8642` OpenAI-compatible API
  (not enabled here — set `API_SERVER_ENABLED=true` + `API_SERVER_KEY` to use it).
