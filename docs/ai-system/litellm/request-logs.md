# LiteLLM request logs: reading a prompt, response and cost

The proxy stores full request and response bodies in its Postgres spend log
(`store_prompts_in_spend_logs` on
`kubernetes/apps/base/ai/litellm/app/litellmproxy.yaml`), with
`maximum_spend_logs_retention_period: "30d"` to match the `postgres-17` Barman
window on the LAN NAS (`nas.${SECRET_DOMAIN}`). Mechanism, confidentiality and
retention reasoning: skill `litellm-proxy`
([`references/spend-logs.md`](../../../.agents/skills/litellm-proxy/references/spend-logs.md)).

Three things that trip people up:

1. **The prompt is not in the `messages` column** (that stays `"{}"`). Read
   `proxy_server_request` (the request body) and `response` (the completion).
2. **`request_id` is the upstream provider's id** (`gen-...` for OpenRouter,
   `chatcmpl-...` for OpenAI-compatible backends), not the
   `x-litellm-call-id` header. Searching by call id finds nothing.
3. **Never call `GET /spend/logs` bare.** Without `request_id`, an `api_key`
   or a narrow date range it walks the whole `LiteLLM_SpendLogs` table
   in-process and has OOM-killed the proxy. Without `request_id` it also
   returns a per-day aggregate, not a request list.

`store_model_in_db` is deliberately false and is not needed for any of this.

## Admin UI

`https://litellm.${SECRET_DOMAIN}/ui/` -> **Logs** -> click a row. The list
omits bodies; opening a row calls `GET /spend/logs/ui/{request_id}`, which
returns `messages`, `response` and `proxy_server_request`.

## API (no DB credentials needed)

```bash
kubectl -n ai port-forward svc/litellm 4000:4000 &
MK="$(kubectl -n ai get secret litellm-secret -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)"

# 1. Find a request_id (the list the UI renders; no bodies).
#    Dates are "YYYY-MM-DD HH:MM:SS", URL-encoded.
curl -sG -H "Authorization: Bearer $MK" "http://127.0.0.1:4000/spend/logs/ui" \
  --data-urlencode "start_date=2026-09-27 00:00:00" \
  --data-urlencode "end_date=2026-09-28 00:00:00" \
  --data-urlencode "page_size=10" \
  | jq '.data[] | {request_id, model_group, spend, startTime, status}'

# 2. One request, with the full prompt and response.
curl -s -H "Authorization: Bearer $MK" \
  "http://127.0.0.1:4000/spend/logs?request_id=<request_id>" | jq '.[0] | {
     prompt:          .proxy_server_request.messages,
     completion:      .response.choices[0].message.content,
     spend:           .spend,
     cost_breakdown:  .metadata.cost_breakdown
   }'

# Equivalent, and the exact call the UI makes:
curl -s -H "Authorization: Bearer $MK" "http://127.0.0.1:4000/spend/logs/ui/<request_id>" | jq
```

## SQL (to grep across many requests)

```bash
DBURL="$(kubectl -n ai get secret litellm-secret -o jsonpath='{.data.DATABASE_URL}' | base64 -d)"
PG="$(kubectl -n database get cluster postgres-17 -o jsonpath='{.status.currentPrimary}')"

kubectl -n database exec -i "$PG" -c postgres -- psql "$DBURL" -P pager=off -f - <<'SQL'
SELECT request_id, model_group, model, spend, prompt_tokens, completion_tokens, "startTime",
       metadata -> 'user_api_key_alias'        AS consumer,
       metadata -> 'cost_breakdown'            AS cost_breakdown,
       proxy_server_request -> 'messages'      AS prompt,
       response -> 'choices' -> 0 -> 'message' AS completion
FROM "LiteLLM_SpendLogs"
ORDER BY "startTime" DESC
LIMIT 5;
SQL
```

Useful filters: `WHERE metadata ->> 'user_api_key_alias' = 'repo-wiki'`,
`WHERE model_group = 'auto'`, or
`WHERE proxy_server_request::text ILIKE '%some phrase%'`.

| Column | Holds |
|---|---|
| `proxy_server_request` | the request body (`messages`, `model`, parameters, a `metadata` sub-object) |
| `response` | the full provider response (choices, usage, provider `cost`) |
| `messages` | `"{}"` except for realtime-API calls; ignore it |
| `spend` | USD LiteLLM charged the virtual key |
| `metadata.cost_breakdown` | input / output / cache-read / cache-creation split when LiteLLM priced the call; `null` when the provider reported cost (OpenRouter: `response.usage.cost`) |
| `metadata.user_api_key_alias` | which consumer |

## Reading the numbers

- Local-model spend is not money: `qwen3.6-35b-a3b` carries synthetic
  governance prices for the `demo` budget test and dominates the headline
  total. For real money filter
  `custom_llm_provider IN ('anthropic','openrouter','xai','zai')`.
- Rows keep the prices they were written with; repricing a model never
  rewrites history.
- A `litellm_truncated` marker in a stored body means one string exceeded
  `MAX_STRING_LENGTH_PROMPT_IN_DB` (set to 1,000,000 chars).
- Per-day, per-model and per-consumer history beyond 30 days lives in the
  Admin UI **Usage** page (`/user/daily/activity`), which the pruner does not
  touch.
