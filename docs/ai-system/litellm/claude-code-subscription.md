# Claude Code through LiteLLM (subscription pass-through): client setup

Point a `claude` CLI that is logged in to a personal Claude Max/Pro
subscription at this cluster's LiteLLM proxy. The CLI keeps sending its own
OAuth token, so tokens stay billed to that person's flat-rate plan; the
cluster gets per-request tokens, latency and per-key attribution. The proxy
holds no credential for the four pass-through models (`claude-sonnet-5`,
`claude-opus-5`, `claude-haiku-4-5-20251001`, `claude-fable-5-1`).

Mechanism, money-safety invariants and how to add a model family: skill
`litellm-proxy`
([`references/claude-code-subscription.md`](../../../.agents/skills/litellm-proxy/references/claude-code-subscription.md)).

## 1. One time: log the CLI in to your subscription

Interactive and per-person, on the workstation that will use it:

```bash
claude          # then: /login  ->  "Claude account with subscription"
```

This browser OAuth flow stores the `sk-ant-oat...` token in the CLI's own
credential store. Headless verification of this step is out of scope, like
every manual credential step in this repo: nothing in Git or CI can perform or
check it, and the token never leaves the workstation.

## 2. One time: get a LiteLLM virtual key

The key is `kubernetes/apps/base/ai/litellm/app/virtualkeys/claude-code-subscription.yaml`,
allow-listed to the four pass-through models plus `embedding-local`, never a
metered model. It is minted into a Secret and mirrored to 1Password item
`litellm-consumer-claude-code-subscription`:

```bash
kubectl -n ai get secret litellm-key-claude-code-subscription -o jsonpath='{.data.key}' | base64 -d
```

Give each additional person their own key rather than sharing this one, or
per-person attribution collapses into one bucket.

## 3. Point the CLI at LiteLLM

```bash
export ANTHROPIC_BASE_URL="https://litellm.${SECRET_DOMAIN}"   # internal gateway
export ANTHROPIC_MODEL="claude-sonnet-5[1m]"
export ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-5[1m]"
export ANTHROPIC_DEFAULT_OPUS_MODEL="claude-opus-5[1m]"
export ANTHROPIC_DEFAULT_FABLE_MODEL="claude-fable-5-1[1m]"
export ENABLE_TOOL_SEARCH=true
export ANTHROPIC_CUSTOM_HEADERS="x-litellm-api-key: Bearer sk-...your-virtual-key..."
claude
```

- **The virtual key goes in `ANTHROPIC_CUSTOM_HEADERS` as `x-litellm-api-key`,
  never as `Authorization`.** `Authorization` is reserved for the CLI's own
  subscription token; a virtual key there is treated as proxy auth and the
  pass-through silently stops working.
- **`[1m]` is required and client-only.** Claude Code trusts the native 1M
  window only when the base URL is `api.anthropic.com`; through this proxy it
  assumes 200k, compacts at ~167k and turns tool search off. The suffix
  restores the 1M belief, and Claude Code strips it before sending, so the
  wire model is the plain name. `ANTHROPIC_BETAS`,
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW` and `CLAUDE_CODE_MAX_CONTEXT_TOKENS` do
  not raise the window; do not use `_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL`
  (undocumented, treats the proxy as first-party).
- The family variables matter because subagents and `/model` resolve by
  family, not by `ANTHROPIC_MODEL`.
- `ANTHROPIC_DEFAULT_HAIKU_MODEL` stays unset on purpose: Claude Code already
  resolves Haiku to `claude-haiku-4-5-20251001`, and Haiku 4.5's window is
  200k, so `[1m]` does not apply.
- `ENABLE_TOOL_SEARCH=true` is safe through this proxy.
- `litellm.${SECRET_DOMAIN}` resolves on the private VLAN only; in-cluster
  callers can use `http://litellm.ai.svc.cluster.local:4000`.

Self-check (must print `1000000`):

```bash
claude -p 'Reply OK' --output-format json | jq '.modelUsage[].contextWindow'
```

## 4. Confirm it landed

```bash
kubectl -n ai logs deploy/litellm -c litellm --since=5m | grep claude-code-subscription
```

Tokens, duration and the owning key are the signal. Spend reads `$0` by
design. If new rows start carrying cost, a price field was dropped from a
model CR (`LiteLLMClaudeCodeSubscriptionSpendRegression` also fires).

## Troubleshooting

- `401 "OAuth access token is invalid."`: the request reached Anthropic
  without a valid subscription token (not logged in, or the virtual key was
  put in `Authorization`). It never bills the household account.
- `403 ... can only access models=[...]`: the CLI asked for a model outside
  the key's allow-list, usually a missing family variable.
- To use the household-metered account on purpose, ask for
  `claude-sonnet-5-metered` / `claude-opus-5-metered` with a key entitled to
  them; the subscription key deliberately is not.
