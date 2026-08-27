# OpenCode

[sst/opencode](https://opencode.ai) interactive coding agent workspace,
adapted 2026-08-26 from `joryirving/home-ops`
`kubernetes/apps/base/llm/opencode/` (commit `3d3b700`) per captain request.
Internal-only: gated behind Authentik and reachable solely through the
internal gateway, never the public one.

## What changed vs the reference

- **Namespace/path**: `kubernetes/apps/base/ai/opencode/` in this repo's
  existing `ai` namespace, not the reference's own `llm` namespace/path.
- **Chart source**: uses this repo's shared `app-template` `OCIRepository`
  via `chartRef`, not a per-app one.
- **LLM access**: `opencode.json`'s `provider.litellm` points at
  `http://litellm.ai.svc.cluster.local:4000/v1` (our in-cluster governance
  proxy, captain decisions B4/D4) using a budgeted `LiteLLMVirtualKey`
  consumer key - never a direct provider API key. The default model is
  `litellm/auto` (our complexity-tier router), matching this cluster's D3
  design intent for agent-facing consumers.
- **Model/provider list drastically simplified**: the reference's
  `opencode.json` references a dozen models across its own multi-GPU
  homelab (separate local models per physical GPU for concurrent
  subagent lanes, plus several ChatGPT variants) and a matching
  coordinator/explorer/fixer/oracle/reviewer subagent-role hierarchy built
  around that hardware diversity. This cluster has one local model
  (`qwen3.6-35b-a3b`, one GPU) and two cloud tiers (`claude-sonnet-5`,
  `claude-opus-5`) behind the same `auto` router already used elsewhere in
  this repo - replicating the reference's role hierarchy would either
  reference nonexistent models or serialize every "concurrent" subagent
  onto the one local model, defeating its purpose. Simplified to a single
  default model; nothing prevents adding custom agent roles later against
  this cluster's actual model set.
- **Memory plugin dropped**: the reference's `@eleboucher/opencode-memini`
  plugin integration was not carried over - out of scope for this task
  (LLM-access wiring only) and would need its own compatibility check
  against this cluster's `ai/agentmemory` app.
- **Context7 MCP dropped**: needs a new external API key/service with no
  existing prerequisite in this repo; out of scope here.
- **ToolHive MCP kept**: `mcp.toolhive` points at this cluster's own
  `ai/toolhive` `VirtualMCPServer` (`http://vmcp-mcp-gateway-internal.ai.svc.cluster.local:4483/mcp`,
  `incomingAuth: anonymous` - confirmed in-cluster, no new secret needed),
  the direct analog of the reference's own toolhive router.
- **Auth**: reference gates its UI route with a native Envoy Gateway `oidc:`
  SecurityPolicy pointed straight at Authentik's issuer, plus a *second*
  HTTPRoute/hostname for headless API access gated by HTTP Basic Auth. This
  repo's house pattern (`home-assistant`, `kromgo`, `echo`) is Envoy
  `extAuth` forward-auth against the Authentik embedded outpost instead -
  used here unchanged since the mechanism is gateway/route-agnostic. The
  basic-auth API split was collapsed into the single Authentik-gated route
  (see `app/securitypolicy.yaml`) since nothing here needs unauthenticated
  programmatic access; add it back following the reference's shape if that
  need appears later.
- **Route**: internal only - `envoy-internal` is the sole `parentRef` and
  `external-dns.alpha.kubernetes.io/target` publishes only the internal
  record. The reference's `opencode.jory.dev` is also internal-only, so no
  change in intent, only in namespace/hostname.
- **GITHUB_TOKEN**: reuses the *existing* `hermes` 1Password item's
  `HOMELAB_GH_TOKEN` field (`public_repo` scope: read+write to public
  repos) - no new 1Password item. This is deliberately different from
  `ai/repo-wiki`, which needs a *new*, strictly read-only credential:
  opencode is an interactive workspace that needs to actually commit and
  push, and `HOMELAB_GH_TOKEN` is already the credential this cluster
  trusts for automated public-repo writes.
- **Default workspace**: entrypoint clones `Aviator-Coding/home-ops` (this
  repo) instead of the reference's own repo.
- **Persistence**: reference uses its own "kopiur" backup tool for the home
  PVC. This repo's convention backs stateful apps with VolSync instead -
  used here for the `home` PVC.
- **Git identity**: set to the captain's own name/email (this is a
  single-operator internal tool), not the reference's bot identity.

## Prerequisites (before first sync)

**Authentik**: this route's `SecurityPolicy` forward-auths to the Authentik
embedded outpost the same way `home-assistant`, `kromgo`, and `echo` already
do. Authentik's own Proxy Provider + Application binding for
`opencode.${SECRET_DOMAIN}` is configured in the Authentik UI, out of band
from GitOps (this repo has no committed Authentik blueprint/provider
resources for any of the existing gated apps either) - the captain needs to
create that binding before the outpost will authorize this host. Until then,
requests to `opencode.${SECRET_DOMAIN}` will fail auth even though every
Kubernetes object is healthy.

No 1Password item is needed: `GITHUB_TOKEN` reuses the existing `hermes`
item's `HOMELAB_GH_TOKEN` field, and `litellm-consumer-opencode` is
auto-created by the `PushSecret` paired with this app's `LiteLLMVirtualKey`
CR (`kubernetes/apps/base/ai/litellm/app/virtualkeys/opencode.yaml`).
