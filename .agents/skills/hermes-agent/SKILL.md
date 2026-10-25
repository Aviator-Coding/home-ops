---
name: hermes-agent
description: "Read before editing kubernetes/apps/base/ai/hermes/** (config.yaml, HelmRelease, runtime skills), restarting or debugging Hermes, working on its state.db or PVC capacity, its MCP/LLM connectivity, or its samba shared storage. Covers any config byte restarting the pod uncleanly, green status hiding a stuck agent, and never setting vacuum_after_prune: true."
---

# Hermes agent: config, restarts, state.db and routing

Hermes is the homelab operator in namespace `ai`. It is a single writer on an
RWO `ceph-block` claim. Config, the runtime skill, and Samba share contents
are payloads the pod reads: a comment edit there still restarts the pod
uncleanly, so batch them and never pair one with an unrelated rollout.

## Tripwires

1. **Any byte of `resources/config.yaml` or a runtime skill restarts the pod.**
   Both are `configMapGenerator` sources. The restart is unclean and the next
   boot integrity-checks all of `state.db`. [restarts.md](references/restarts.md)
2. **Pod status and Gatus stay green while the gateway is stuck.** Probes are
   TCP on the dashboard port `9119`, which is a different s6 service from
   `gateway-default`. Read `/opt/data/logs/gateway-startup-watchdog.log`.
   Do not add a gateway liveness probe.
3. **Never set `sessions.vacuum_after_prune: true`.** `last_vacuum` is absent
   from `state_meta`, so the interval throttle never engages and a full
   rewrite through the WAL is retried on every prune that deletes rows.
   Growing the claim to 40Gi did not retire this. `retention_days` (narrower
   than upstream's 90) is the dial. [state-db.md](references/state-db.md)
4. **`sessions.auto_prune` must be explicit `true`.** Both call sites use
   `.get("auto_prune", False)`, so an upstream default flip silently stops
   pruning. Gate: `scripts/ci/hermes-state-db-retention-test.py`.
5. **Never set `OPENAI_BASE_URL`.** It is a global OpenAI-SDK override. The
   one provider, `custom:litellm`, carries everything (default model,
   fallbacks, auxiliary) with Hermes' own virtual key, and sends
   `x-opencode-session` for the `go-*` models. A dummy `OPENAI_API_KEY` is the
   only env to re-add if boot demands an OpenAI key. [llm-routing.md](references/llm-routing.md)
6. **Keep the image on the `-desktop` tag.** The plain tag has no Chromium
   or VNC stack, and the process cannot `apt install` after it drops to uid
   10000. Renovate is pinned to the suffix so it cannot drift back.
7. **Never set container `args` back to `gateway run`.** The image already
   starts `gateway-default` via s6. A second gateway CrashLoops. The command
   stays `sleep infinity`.
8. **Keep the `kopiur/pvc` include** on the overlay, `strategy: Recreate`,
   and `replicas: 1`. The claim is single-writer. `KOPIUR_CAPACITY` in git
   does not resize a live claim. [shared-storage.md](references/shared-storage.md)
9. **Env-injected secrets do not refresh in a running pod** without
   `reloader.stakater.com/auto: "true"` on the controller. The annotation is
   already set; removing it leaves rotated tokens stale until some other
   rollout.
10. **Do not name the custom provider after a built-in id** (`openrouter`,
    `openai`, ...). Hermes then treats it as the native integration and
    skips that entry's `base_url`. `litellm` is the safe name.
11. **Secrets are resolved from the profile secret scope, not the container
    env.** A provider `key_env`, an MCP `${VAR}` and a skill's required env
    all come from `secrets.command` (files mounted at `/secrets/mcp`). Env
    alone gave 401s and four days of blocked commit-watcher cron runs.
    [llm-routing.md](references/llm-routing.md). Gate:
    `scripts/ci/hermes-secret-scope-test.py`.
12. **Profiles are not inherited and not in git.** `tia-portal` (dormant, the
    PLC archive) has its own `config.yaml` on the volume. Providers in the
    default config do not reach it, so removing a provider silently breaks any
    profile that names it. Grep `/opt/data/profiles/*/config.yaml` first.
13. **The terminal guard hook is fail-closed.** `hooks/guard-terminal.py`
    blocks the catastrophic and escalates "ask first" commands to the approval
    gate (cron and gateway deny them). A legitimate command it refuses means
    editing the script and adding the case to `scripts/ci/hermes-hooks-test.py`,
    never turning the hook off. It guards `terminal` only; `execute_code` can
    go around it. The image has no `jq`.
14. **Do not set `agent.max_turns`.** Upstream defaults it to unlimited on
    purpose (caps caused silent mid-task truncation), and unattended runs
    already hard-stop on loops (`non_interactive_hard_stop_enabled`).
15. **`SUMMARY_LLM_MODEL` must not be a thinking model.** The commit-watcher
    asks for `400 x repos + 400` tokens and drops an empty `content`;
    `go-kimi-k2.6` reasons past that budget and every digest came back
    "unavailable". `go-glm-5.1` answers in seconds. Set it in both
    `helmrelease.yaml` and `secrets.command`; the secret-scope test keeps them equal.

## Where things live

| What | Path |
|---|---|
| Config payload (any byte, comments included, restarts the pod) | `kubernetes/apps/base/ai/hermes/app/resources/config.yaml` |
| Runtime skill payload | `kubernetes/apps/base/ai/hermes/app/skills/homelab-commit-watcher/` |
| Terminal guard hook (mounted at `/etc/hermes-hooks`) | `kubernetes/apps/base/ai/hermes/app/hooks/guard-terminal.py` |
| HelmRelease | `kubernetes/apps/base/ai/hermes/app/helmrelease.yaml` |
| Alerts (live PVC capacity, not a hardcoded size) | `kubernetes/apps/base/ai/hermes/app/prometheusrule.yaml` |
| Flux overlay (backup comments are the backups domain) | `kubernetes/apps/main/ai/hermes.yaml` |
| Human setup (1Password fields, Git PAT) | `kubernetes/apps/base/ai/hermes/README.md` |
| Samba + shared claims | `kubernetes/apps/base/ai/samba/`, `kubernetes/apps/base/ai/pvc/` |

## Procedures

- Why vacuum stays off, and how retention is shaped: [state-db.md](references/state-db.md).
- What a restart actually does, and how to tell the gateway is wedged: [restarts.md](references/restarts.md).
- Model, fallback, and auxiliary routing: [llm-routing.md](references/llm-routing.md).
- MCP gateway `${VAR}` refs under multiplexing: [mcp-gateway.md](references/mcp-gateway.md).
- Samba identity, setgid, and the unbacked `shared-files` claim: [shared-storage.md](references/shared-storage.md).
- First-sync secrets and the single-repo PAT: the README, not this skill.

## Verify

- `python3 scripts/ci/hermes-state-db-retention-test.py`
- `python3 scripts/ci/hermes-secret-scope-test.py`
- `python3 scripts/ci/hermes-hooks-test.py`; live: `hermes hooks doctor` and
  `hermes hooks test pre_tool_call --for-tool terminal` in the pod.
- After a config change, expect a pod restart. Do not declare the agent
  healthy from `kubectl get pods` or Gatus. Read the watchdog log.
- Live (read-only): `kubectl -n ai logs deploy/hermes -c app` is the wrong
  stream for the gateway. The files under `/opt/data/logs/` are.
