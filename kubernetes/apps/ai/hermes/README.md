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
2. **Confirm the model id**: list what xAI serves and set `OPENAI_MODEL`:
   ```bash
   kubectl -n ai run -it --rm curl --image=curlimages/curl --restart=Never -- \
     -s http://internal-noauth.ai.svc.cluster.local/xai/v1/models
   ```

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
