# Provider pass-through lockdown (`/openrouter`, `/anthropic`)

## The hole

The image registers built-in `/openrouter/{endpoint:path}` and
`/anthropic/{endpoint:path}` pass-through routes unconditionally
(`llm_passthrough_endpoints.py`; no config or CRD toggle). They forward to
`https://openrouter.ai/api/...` and `https://api.anthropic.com/...` on the
**shared** credentials (`OPENROUTER_API_KEY`, and the household-metered
`ANTHROPIC_API_KEY`) for **any** valid LiteLLM key. A key's `models`
allow-list only fires when the body happens to carry a `model` field, so it is
not a guard: an OpenRouter `models` array, or any GET, got through with
`demo`. Nobody legitimately used `/anthropic` (no spend-log rows, no route
series); every consumer, Claude Code included, uses `/v1/*`.

**Invariant: no virtual key may reach a household-metered credential through
a pass-through route.**

## The close: `generalSettings.pass_through_endpoints`

Registering a path with `auth: true` makes
`RouteChecks.non_proxy_admin_allowed_routes_check` require an
`allowed_passthrough_routes` match from every **non-admin** key before any
other route rule. The master key and SSO proxy-admins skip that check. A new
key is denied by default, without anyone remembering to scope it.

The native handler still serves the request but takes its **target and
headers from the first matching registry entry** and drops the subpath, so
**entry order is load-bearing**:

1. `/openrouter` catch-all (`include_subpath: true`) -> dead loopback
   `http://127.0.0.1:9/...`, no headers. No exact `/openrouter` entry exists:
   Jev decisions are served natively on `/v1/decisions` (below).
2. `POST /anthropic/v1/messages` and `POST /anthropic/v1/messages/count_tokens`
   -> real targets with `x-api-key: os.environ/ANTHROPIC_API_KEY` and
   `anthropic-version`.
3. `/anthropic` catch-all -> dead loopback, no headers.

Every other path fails closed (500, connection refused, nothing sent), even
for the master key. Exact entries must stay **above** their catch-all, or
the admin Anthropic paths break for everyone.

- **Never set `forward_headers`** on an entry. The native handler forwards
  every client header upstream, the caller's own LiteLLM key included; an
  entry replaces that with exactly its declared headers. Clients may still
  send `x-pass-anthropic-beta` (LiteLLM strips the prefix and refuses
  credential headers).
- The `envoy-internal` `HTTPRouteFilter` on `httproute-internal.yaml` 404s
  `/anthropic` on the hostname path as an outer layer. It never sees the
  Service DNS path; the config layer covers both.
- CI: `scripts/ci/litellm-passthrough-lockdown-test.py` drives LiteLLM's own
  `RouteChecks` against these entries (mutation-proven), including the order;
  `scripts/ci/litellm-anthropic-passthrough-test.py` pins the gateway layer.

Re-opening: granting a virtual key needs a list-valued
`allowed_passthrough_routes` (see below) and a captain decision. Giving the
master key another path is a new exact entry above the catch-all. Removing
the gateway rule alone reopens nothing; removing the config entries reopens
the Service DNS path to every key. Either way, first re-establish the
invariant above.

## Why no key can be granted declaratively

LiteLLM wants a **list** in the key's (or team's)
`metadata.allowed_passthrough_routes`. The operator's `metadata` is
`map[string]string`, and a string value is iterated as characters and fails
closed. The top-level `/key/generate` `allowed_passthrough_routes` field is
Enterprise-gated on this OSS image. The same list inside `metadata` is not
gated, but only a proxy admin can set it. A granted key also needs the target
model on its `models` list, because the model check still applies.

## Jev decisions are not a pass-through

`typesafe/jev-*` is a decisions model that OpenRouter refuses on chat
completions. It is the `jev` `LiteLLMModel` (upstream
`openrouter/typesafe/jev-1.14`) served by LiteLLM's native
`POST /v1/decisions` and `/v1/systemone` (1.104.2+). A key reaches it through
a normal `models: [jev]` allow-list, so nothing is hand-applied in LiteLLM's
DB and recreating a key loses nothing. The former `/openrouter/alpha/decisions`
exact entry and the `jev-door-grant` are gone; CI refuses a reopened exact
`/openrouter` entry, any declared `allowed_passthrough_routes`, and raw
`typesafe/` ids in `models`. Contract: [model-catalog.md](model-catalog.md).
Call it with the `jev-decisions` key (1Password `litellm-consumer-jev-decisions`,
field `key`) at `https://litellm.${SECRET_DOMAIN}/v1/decisions` or the Service
DNS equivalent.
