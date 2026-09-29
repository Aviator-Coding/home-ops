# Virtual keys: minting, limits, consumers, rotation

## How a key is minted

One `LiteLLMVirtualKey` + one `PushSecret` per consumer in
`kubernetes/apps/base/ai/litellm/app/virtualkeys/`, listed in that directory's
`kustomization.yaml`. Spec fields: `models` (allow-list), `maxBudget` (USD as a
**decimal string**), `budgetDuration`, `rpmLimit`, `tpmLimit`, plus `keyAlias`,
`secretName`, `secretKey`, and optional `maxParallelRequests`, `duration`,
`aliases`, `userID`/`teamID`, `metadata`.

The operator reconciles against the proxy admin API with the master key
(`LiteLLMProxy.spec.apiAccess.masterKeyRef`). It branches on **its own output
Secret**, never on an alias lookup:

- No output Secret: `POST /key/generate`, then writes the Secret named by
  `spec.secretName` (owned by the CR).
- Output Secret exists: `GET` the live key, compare governed fields,
  `POST /key/update` only on a difference. The credential is preserved across
  a budget or allow-list edit.
- CR deleted: a finalizer deletes the remote key.

The `PushSecret` mirrors the key to 1Password item
`litellm-consumer-<name>` (property `key`), refresh 5m. These items are
written, never read by the proxy, so they need not pre-exist.

Watch a change land: `kubectl -n ai get litellmvirtualkey -o wide`, and
`describe` for the failing condition (`AdminClientFailed`, `GenerateFailed`,
`UpdateFailed`). A long-failing CR can show a stale `Ready=False` while the
controller backs off; restarting `deploy/litellm-operator` forces a reconcile
(it is stateless).

## Trap: aliases are global and there is no adopt-by-alias

`POST /key/generate` returns `400 Key with alias '<name>' already exists` when
any proxy-DB key holds the alias, and with no output Secret the operator can
only retry `generate`. The CR sits `Ready=False`/`GenerateFailed` forever
while the proxy looks healthy. Deleting the colliding key
(`POST /key/delete`) is a required step, and it is a **real rotation**: the
old value starts returning 401 and its spend history goes with it. Check who
holds the key first. This is how `agent-swarm-captain`, `agent-swarm-paid`
and `agent-swarm-network-broker` were brought under Git (forced rotation,
captain-approved; entitlements carried over exactly as measured live).

Rotating `agent-swarm-paid` also resets its spend counter; a $0 after a
rotation is not evidence of low use.

## Trap: removing a field never clears it on the live key

`rpmLimit`, `tpmLimit`, `maxBudget` (and likely `maxParallelRequests`) are Go
pointers with `omitempty`. A removed field is **omitted** from `/key/update`,
and LiteLLM's `exclude_unset=True` leaves an omitted column alone; only an
explicit JSON `null` clears it. The operator keeps detecting drift and
re-sending the same no-op update forever.

- Raising or lowering a limit works through Git alone.
- Going from limited to unlimited needs a one-time admin call after the Git
  change, confirmed with `GET /key/info`:

  ```bash
  # in the proxy pod, master key from its env
  POST /key/update {"key": "<key>", "rpm_limit": null, "tpm_limit": null}
  ```

- A key created without the field, or regenerated after the spec dropped it,
  never hits this.

This is also why the "inert" `maxBudget` on `repo-wiki`, `ai-pr-review` and
`embedding-external` stays: deleting it would not remove the live cap, and it
is the guard that matters the moment someone widens `models` to a priced alias.

## Trap: never `spec.metadata` on `jev-decisions`

That key's pass-through grant lives only in LiteLLM's DB
([passthrough-lockdown.md](passthrough-lockdown.md)). The operator re-sends
the spec on every reconcile; with no `spec.metadata` it sends no metadata and
LiteLLM keeps the grant. Declaring `spec.metadata` makes LiteLLM replace the
whole object and silently deletes the grant.
`scripts/ci/litellm-passthrough-lockdown-test.py` refuses it.

## Trap: per-key `aliases` grant nothing

The allow-list (`can_key_call_model`, in the `user_api_key_auth` dependency)
runs against the **raw** requested name, before the handler applies key
aliases (`_update_model_if_key_alias_exists`). There is no key-alias check,
only a deprecated team-alias one. Measured: an aliased key got a
byte-identical 403. Making an alias fire would mean allow-listing the
target's original name, which on a metered model is exactly the leak the
allow-list prevents. Solve name collisions with a distinct model name (or own
the natural name outright), never with aliases.

## Allow-list semantics

- `models: []` means **all models** (CRD field doc). Only
  `agent-swarm-paid` uses it (unrestricted paid access under a hard $25
  cap); do not narrow it without a captain decision. Such a key also reaches
  every local alias, including the reviewer-only ones.
- The allow-list is checked against what the **caller asked for**, never
  what the router or a fallback resolved to. So `auto` is a real boundary
  (a key holding only `auto` cannot call `claude-opus-5-metered` directly),
  and so is `chat-ha` (holding it is the cloud entitlement, see
  [fallbacks.md](fallbacks.md)).
- A caller-supplied `fallbacks` array in the request body is checked (403).

## Budgets need prices

Spend accrues from the model's price (built-in cost map for cloud models,
`info.extra` for declared ones). On a zero-priced model spend stays $0 and a
`maxBudget` can never trip. Consequences:

- `demo` is the budget-path fixture: `qwen3.6-35b-a3b` carries synthetic
  prices (`5e-05`/`1e-04` per token) so the `$0.05` cap is exhaustible by one
  smoke test. `demo` must remain that alias's only consumer.
- On `auto` keys (`router-demo`, `opencode`) the budget measures only real USD
  on the COMPLEX/REASONING cloud tiers, since the local tiers and the
  classifier sub-call (billed to the caller) are zero-priced. `rpmLimit` /
  `tpmLimit` are the only volume bound on local routed traffic.
- On `chat-ha` keys (`ha-demo`) the budget measures only cloud fallback spend.
- A budget on a key whose models are all zero-priced is inert; it is kept
  only as a guard (see above).

## Budgetless keys (CI-asserted exact set)

`scripts/ci/litellm-claude-code-subscription-test.py` asserts that exactly
these keys omit `maxBudget`; a new budgetless key must be added there with a
reason:

- `claude-code-subscription`: its models are pass-through CRs priced $0 on
  every field, so a budget could never trip. Also no `rpmLimit`/`tpmLimit`
  (captain decision; measured headroom was 43x/267x). The real ceiling is
  Anthropic's subscription rate limiting.
- `agent-swarm-captain`: budgetless by captain decision, unrelated to price.
- `mac-gnhf`: `chat-local` + `embedding-local` only, so spend is $0 by
  construction; its rate limits (300 rpm / 200M tpm, captain's live values)
  only stop spurious throttling. The client (`gnhf`) is sequential, so it
  holds at most one of the backend's four slots; if routed latency regresses,
  cap client concurrency, not rpm.

## Consumer inventory (read the CR for current numbers)

| Key | Models | Consumer / note |
|---|---|---|
| `demo` | `qwen3.6-35b-a3b` | budget-path smoke test, rpm 2 / tpm 2000 |
| `ha-demo` | `chat-ha` | cloud-entitled failover demo, real-USD cap |
| `router-demo` | `auto` | routed-consumer smoke test |
| `opencode` | `auto` | ai/opencode workspace |
| `repo-wiki` | `chat-local` | ai/repo-wiki cron; `WIKI_MODEL` in its HelmRelease must match this allow-list |
| `ai-pr-review` | `pr-review-local` | GitHub Actions reviewer ([pr-reviewer.md](pr-reviewer.md)); never `auto` |
| `claude-code-subscription` | the four pass-through CRs | [claude-code-subscription.md](claude-code-subscription.md) |
| `agent-swarm-captain`, `agent-swarm-network-broker` | `chat-local` | off-cluster agent-swarm runs |
| `agent-swarm-paid` | `[]` (all) | off-cluster, $25 hard cap |
| `mac-gnhf` | `chat-local` | captain's Mac loop, off-cluster |
| `embedding-external` | `embedding-local` | external embedding callers; rpm also protects chat on the shared B70 |
| `litellm-pgvector` | `qwen/qwen3-embedding-8b` only | vector store ([vector-store.md](vector-store.md)); deliberately no `embedding-local` |
| `jev-decisions` | `typesafe/jev-1.13` | decision door, $1/30d ([passthrough-lockdown.md](passthrough-lockdown.md)); no `embedding-local` |

Every other key also allow-lists `embedding-local` (captain intent: every key
may use the local embedder). It is credential-less and zero-priced, so it
never changes a key's money reach.

Off-cluster consumers (`agent-swarm-*`, `mac-gnhf`) re-copy the value from
1Password after any rotation. `ai-pr-review` also lives in a GitHub secret,
so its rotation has two steps ([pr-reviewer.md](pr-reviewer.md)).

## Adding or changing a consumer

1. Copy a CR in `virtualkeys/`, change `metadata.name`, `keyAlias`,
   `secretName`, the PushSecret's names and `remoteKey`, and the limits.
   Keep `maxBudget` a quoted decimal string.
2. Allow-list only what the consumer needs. Never give an unattended job
   `auto` or a fallback-carrying alias unless it is meant to spend cloud money.
3. Add the file to `virtualkeys/kustomization.yaml`.
4. Make sure the alias is free in the proxy DB (see the alias trap), then
   merge. The PushSecret picks the Secret up within 5m.
5. Git is the source of truth for limits: a UI edit is reverted on the next
   reconcile unless the CR changes first.

## Verifying governance end to end

```bash
kubectl -n ai get deploy litellm-operator litellm
kubectl -n ai get litellmproxy,litellmmodel,litellmvirtualkey   # all Ready=True
DEMO_KEY=$(kubectl -n ai get secret litellm-key-demo -o jsonpath='{.data.key}' | base64 -d)
kubectl -n ai port-forward svc/litellm 4000:4000 &
# allowed model -> completion; another model -> 403; repeat until $0.05 -> 429
curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer $DEMO_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"say ok"}]}'
```

`litellm_remaining_api_key_budget_metric` shows the balance and
`LiteLLMConsumerBudgetExhausted` fires at zero. A 429 on a zero-priced key is
always a rate limit, and the message names which one.
