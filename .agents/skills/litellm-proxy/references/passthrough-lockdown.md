# Provider pass-through lockdown (`/openrouter`, `/anthropic`) and the Jev door

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

1. `POST /openrouter/alpha/decisions` -> real target, `Authorization: Bearer
   os.environ/OPENROUTER_API_KEY`.
2. `/openrouter` catch-all (`include_subpath: true`) -> dead loopback
   `http://127.0.0.1:9/...`, no headers.
3. `POST /anthropic/v1/messages` and `POST /anthropic/v1/messages/count_tokens`
   -> real targets with `x-api-key: os.environ/ANTHROPIC_API_KEY` and
   `anthropic-version`.
4. `/anthropic` catch-all -> dead loopback, no headers.

Every other path fails closed (500, connection refused, nothing sent), even
for the master key. Exact entries must stay **above** their catch-all, or
Jev's door and the admin Anthropic paths break for everyone.

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

Re-opening: granting a virtual key needs a declarative list-valued
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

## The one exception: `jev-decisions` (captain decision `jev-door-grant`)

`typesafe/jev-1.13` is a decisions model: OpenRouter refuses it on chat
completions, so it has no `LiteLLMModel` and must not get one. It is reached
only at `POST /openrouter/alpha/decisions`, priced natively by
`TypeSafePassthroughLoggingHandler` from the cost map (spend is recorded).

`virtualkeys/jev-decisions.yaml`: `models: [typesafe/jev-1.13]`, `$1`/30d,
rpm 300, tpm 1000000, **no `spec.metadata`**. The grant is one out-of-Git admin
`/key/update` stored only in LiteLLM's DB:

- It survives every operator reconcile only while the CR declares no
  `spec.metadata` (the operator then sends no metadata and LiteLLM keeps it).
  Declaring `spec.metadata` makes LiteLLM replace the whole object and delete
  the grant. CI refuses `spec.metadata` on this CR.
- Recreating the key (deleting the CR, its `litellm-key-jev-decisions` Secret,
  or the key in LiteLLM) mints a key **without** the grant: `403 Key/team not
  allowed to access passthrough route` until the grant is re-applied. That is
  fail-closed; the decision skill treats any non-200 as inconclusive.
- The grant is prefix-matched and method-blind, so the key is route-allowed on
  subpaths and other methods too, but those land on the dead catch-all. A body
  naming any other model gets the model 403 (OpenRouter requires a string
  `model` on decisions, so that check always fires).

Budget caveat: the `$1`/30d cap is enforced from recorded spend, so rows
logged at zero cost (no price on the cost map, or a failed pricing hook) add
nothing and the cap can under-count. Treat it as a soft guard, not a hard
ceiling. The grant also lives only in LiteLLM's DB, never in Git, so a DB
restore or key recreation loses it and the grant runbook must be re-run.

Apply the grant once after first mint and after any recreation, with the
script in `kubernetes/apps/base/ai/litellm/README.md` ("Granting the
jev-decisions key"). It must print
`{'allowed_passthrough_routes': ['/openrouter/alpha/decisions']}`. Grant that
one path and nothing wider.

Decision endpoint contract for callers: `POST
https://litellm.${SECRET_DOMAIN}/openrouter/alpha/decisions` (LAN) or the
Service DNS equivalent, `Authorization: Bearer <jev-decisions key>`
(1Password `litellm-consumer-jev-decisions`, field `key`), body
`{"model": "typesafe/jev-1.13", "state": "...", "questions": {...}}`.
Results: 200 decision; 403 "passthrough route" = grant missing; 403 "not
allowed to access model" = wrong model; 400 = malformed body; 429/budget =
the key's limits. `typesafe/jev-router` is an unrelated chat model; reaching
it would need its own `LiteLLMModel`.
