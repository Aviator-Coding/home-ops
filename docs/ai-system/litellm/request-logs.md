# Request logs: reading a full prompt, response and cost

How to pull up exactly what was sent to a model and what came back, plus what it
cost. Enabled 2026-08-27 by `store_prompts_in_spend_logs` on
`kubernetes/apps/base/ai/litellm/app/litellmproxy.yaml`.

Everything below was verified against the pinned image
`ghcr.io/berriai/litellm-non_root:v1.98.0` (file:line citations are paths inside
`/app/.venv/lib/python3.13/site-packages/` in that image) and against the live
proxy, not against the LiteLLM docs site, which is thin and partly wrong on this
feature.

---

## 1. TL;DR - the three things that will trip you up

1. **The prompt is NOT in the `messages` column.** That column stays `"{}"` for
   ordinary `/chat/completions` traffic no matter what you enable. Read
   `proxy_server_request` (the request body) and `response` (the completion).
2. **`request_id` is the UPSTREAM PROVIDER's response id**, not the
   `x-litellm-call-id` header the proxy hands back. For OpenRouter it looks like
   `gen-1787882691-eK0dnnHsVU5MzNL03Rqx`; for an OpenAI-compatible backend,
   `chatcmpl-...`. Searching by call-id finds nothing.
3. **Nothing leaves the cluster** and **no caller credential is stored** - see
   section 5. But full prompt bodies are now readable by anything holding the
   master key, and Prometheus holds the master key.

---

## 1b. `store_prompts_in_spend_logs` is sufficient ALONE - do not also set `store_model_in_db`

The Admin UI's own hint for this setting bundles it with `store_model_in_db`.
Do not follow that. This repo keeps `store_model_in_db: false` deliberately so
the Admin UI can never override GitOps-declared models and keys
(`app/litellmproxy.yaml`, and `applyMode: file` depends on the same decision).

Tested, not assumed. With `store_model_in_db: false` throughout:

- content was **written**: the probe row below stored a 4,683-char prompt and
  its completion;
- content was **read back**: `GET /spend/logs/ui/{request_id}` returned both;
- the Logs **list** the UI page renders, `GET /spend/logs/ui`, returned rows
  normally.

And in source, `grep -r store_model_in_db litellm/proxy/spend_tracking/` matches
**nothing** - spend logging never consults it in either direction.

Why the UI bundles them anyway: the UI's Settings page changes
`general_settings` by writing it to the database, and that write path,
`ProxyConfig.save_config` (`proxy/proxy_server.py:4188`) and its
`environment_variables` sibling (`:4235`), returns early unless
`store_model_in_db` is true. So the UI needs it to **set** the flag from the UI.
It is not needed to **honor** the flag or to **display** logs. We declare the
flag in `config.yaml` (rendered by litellm-operator from the `LiteLLMProxy` CR),
so no DB write is involved at all.

---

## 2. Pull up one request - Admin UI

`https://litellm.${SECRET_DOMAIN}/ui/` -> **Logs** -> click the row.

The list view deliberately does not carry the bodies (they are excluded from the
list query "for performance",
`proxy/spend_tracking/spend_management_endpoints.py:2790`). Opening a row calls
`GET /spend/logs/ui/{request_id}` (:2734), which is the endpoint that returns
`messages`, `response` and `proxy_server_request`.

## 3. Pull up one request - API (no DB credentials needed)

This is the same endpoint the UI row-expand calls, so it is the authoritative
check that content capture is working.

```bash
kubectl -n ai port-forward svc/litellm 4000:4000 &
MK="$(kubectl -n ai get secret litellm-secret -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)"

# 1. Find a request_id. This is the list the UI Logs page renders; it carries
#    no bodies. Dates here are datetimes - "YYYY-MM-DD HH:MM:SS", URL-encoded.
curl -sG -H "Authorization: Bearer $MK" "http://127.0.0.1:4000/spend/logs/ui" \
  --data-urlencode "start_date=2026-08-27 00:00:00" \
  --data-urlencode "end_date=2026-08-29 00:00:00" \
  --data-urlencode "page_size=10" \
  | jq '.data[] | {request_id, model_group, spend, startTime, status}'

# 2. One request, WITH the full prompt and response. Shortest path:
curl -s -H "Authorization: Bearer $MK" \
  "http://127.0.0.1:4000/spend/logs?request_id=<request_id>" | jq '.[0] | {
     prompt:     .proxy_server_request.messages,
     completion: .response.choices[0].message.content,
     cost:       .response.usage.cost,
     spend:      .spend
   }'

# Equivalent, and the exact call the UI row-expand makes:
curl -s -H "Authorization: Bearer $MK" \
  "http://127.0.0.1:4000/spend/logs/ui/<request_id>" | jq
```

Trap: `GET /spend/logs` **without** `request_id` does not return a request list
at all - it returns a per-day, per-key spend aggregate. Use `/spend/logs/ui` for
the list, `/spend/logs?request_id=` for one row.

## 4. Pull up one request - SQL

Use this when you want to grep across many requests, or when you want the
`spend` figure LiteLLM actually billed the virtual key (the `response.usage.cost`
above is what the *provider* reported; `LiteLLM_SpendLogs.spend` is what
LiteLLM charged, and for local models those differ deliberately - section 6).

```bash
DBURL="$(kubectl -n ai get secret litellm-secret -o jsonpath='{.data.DATABASE_URL}' | base64 -d)"
PG="$(kubectl -n database get cluster postgres-17 -o jsonpath='{.status.currentPrimary}')"

kubectl -n database exec -i "$PG" -c postgres -- psql "$DBURL" -P pager=off -f - <<'SQL'
SELECT request_id,
       model_group,
       model,
       spend,
       prompt_tokens,
       completion_tokens,
       "startTime",
       metadata -> 'user_api_key_alias'                AS consumer,
       metadata -> 'cost_breakdown'                    AS cost_breakdown,
       proxy_server_request -> 'messages'              AS prompt,
       response -> 'choices' -> 0 -> 'message'         AS completion
FROM "LiteLLM_SpendLogs"
ORDER BY "startTime" DESC
LIMIT 5;
SQL
```

Useful filters: `WHERE metadata ->> 'user_api_key_alias' = 'repo-wiki'`,
`WHERE model_group = 'auto'`, or
`WHERE proxy_server_request::text ILIKE '%some phrase%'` to find a request by
what was in it.

Columns that matter, from `proxy/schema.prisma` (`model LiteLLM_SpendLogs`):

| Column | Holds |
|---|---|
| `proxy_server_request` | the **request body** - `messages`, `model`, `temperature`, plus a `metadata` sub-object |
| `response` | the **full provider response object** - choices, usage, provider `cost` |
| `messages` | `"{}"` except for realtime-API calls. Ignore it. |
| `spend` | USD LiteLLM charged the virtual key |
| `metadata.cost_breakdown` | input / output / cache-read / cache-creation split |
| `metadata.user_api_key_alias` | which consumer (`repo-wiki`, `opencode`, ...) |

---

## 5. Confidentiality: what this does and does not persist

**Stays in the cluster.** The only store is the `litellm` database on the shared
in-cluster `postgres-17` CNPG cluster. There is no external copy:

- LiteLLM's cold-storage feature (S3/GCS offload of prompt bodies) is opt-in via
  `litellm.cold_storage_custom_logger`, which defaults to `None`
  (`litellm/__init__.py:166`) and is not set here.
  `_generate_cold_storage_object_key` returns `None` the moment that is unset
  (`litellm_core_utils/litellm_logging.py:5089`), so no object key is minted and
  nothing is uploaded. Live rows confirm it: `metadata.cold_storage_object_key`
  is `null`.
- The only logging callback configured is `prometheus`
  (`litellmSettings.callbacks`), which emits labelled counters, never content.
  There is no S3/GCS/Langfuse/OTel-collector callback on this proxy.

**Caller credentials are not persisted.** Verified live, not just read: a probe
request carrying `Authorization: Bearer sk-ant-oat01-<fake>` and a fake
`x-api-key` was stored with `metadata.headers.authorization` =
`"***REDACTED***"` and **zero** occurrences of the token material anywhere in the
row. The mechanism is `redact_credential_headers`
(`proxy/litellm_pre_call_utils.py:835`, applied at `:1614`), whose docstring says
these values "must never reach a logging callback or a spend log"; it masks
`authorization`, `x-api-key`, `x-litellm-api-key`, `api-key`,
`x-goog-api-key`, `ocp-apim-subscription-key`, `cookie` and
`proxy-authorization`. Separately, the stored body snapshot excludes
`secret_fields`, the key that carries raw headers (`:1792`,
`spend_tracking_utils.py:654`).

That matters here specifically because this proxy deliberately passes a Claude
Code Max/Pro OAuth token through on `claude-code-subscription`
(`claude-code-subscription.md`). That token is **not** written to the spend log.

**What did widen.** Prompt and completion bodies are now readable by anything
that can present the master key. That includes Prometheus, which holds
`LITELLM_MASTER_KEY` to scrape `/metrics` (a deliberate tradeoff recorded in
`kubernetes/apps/base/ai/litellm/app/servicemonitor.yaml`). Before this change
that credential exposed usage counters; now it exposes request content. The
route itself is unchanged - still internal-only, never the public listener.

**Sensitive traffic to be aware of.** `repo-wiki` sends third-party repository
source through the local model, and `opencode` is a coding-agent workspace that
may carry the captain's own work. Both are now persisted verbatim in Postgres.
That is cluster-internal persistence with no export path, but it is persistence:
the retention question in section 7 is how long it lasts.

---

## 6. Cost breakdown: use what already exists

Nothing new was built for cost. Three surfaces already cover it, and they answer
different questions:

| Question | Where | Backing table |
|---|---|---|
| What did THIS request cost, and why | `metadata.cost_breakdown` on the row (section 4), or `.response.usage` via the API | `LiteLLM_SpendLogs` |
| Spend per model / per consumer / per day, with cache and router savings | Admin UI **Usage**, i.e. `GET /user/daily/activity?start_date=..&end_date=..` | `LiteLLM_Daily*Spend` rollups |
| Top models / keys, last 30 days | `GET /global/spend/models`, `/global/spend/keys`, `/global/spend/provider` | 30-day SQL **views over `LiteLLM_SpendLogs`** |

`metadata.cost_breakdown` is already granular - a real row from this proxy:

```json
{"input_cost": 0.027669, "output_cost": 0.0, "cache_read_cost": 0.026509,
 "cache_creation_cost": 0.00116, "total_cost": 0.027669, "discount_amount": 0.0}
```

Prometheus adds `litellm_spend_metric`, `litellm_input_tokens_metric`,
`litellm_output_tokens_metric` and `litellm_total_tokens_metric` via the
`prometheus` callback (`integrations/prometheus.py:209-231`); they are scraped by
`app/servicemonitor.yaml`. Note these series only appear after the proxy has
served a request since its last restart, so a freshly rolled pod shows none of
them - that is expected, not a broken scrape.

### Reading the numbers: local-model spend is a governance price, not money

`qwen3.6-35b-a3b` and `qwen3.6-35b-a3b-classifier` run on the in-cluster B70 and
cost nothing, but they carry explicit `input_cost_per_token: 0.00005` /
`output_cost_per_token: 0.0001` in their `LiteLLMModel` CRs. That is deliberate
(the rationale is in `app/models/qwen3.6-35b-a3b.yaml`): a $0 model accrues no
spend, so D4 virtual-key budgets could never constrain local usage. The
consequence when reading any cost surface: the local model dominates every
total. As of 2026-08-28 it was $7.95 of $8.73 recorded spend - about 91% of the
headline figure is accounting units, not dollars. Filter on
`custom_llm_provider IN ('anthropic','openrouter','xai','zai')` for real money.

---

## 7. Retention: there IS a built-in pruner, and it is OFF

`general_settings.maximum_spend_logs_retention_period` (e.g. `"90d"`) is the
gate. It is unset here, and while it is unset **no cleanup job is ever
scheduled** - `proxy_server.py:8937` only registers `spend_log_cleanup_job` when
that key (or `maximum_autorouter_session_retention_period`) is non-null. So spend
logs, now including full prompts and responses, grow without bound.

It was left off deliberately: turning it on deletes whole `LiteLLM_SpendLogs`
rows, which deletes the per-request **cost history** along with the content.
There is no built-in option to strip content while keeping the cost row. That is
a captain call, not an implementation default.

**Recommendation: `maximum_spend_logs_retention_period: "90d"`,** added next to
`store_prompts_in_spend_logs` in `litellmproxy.yaml`. Reasoning:

- Growth is modest but real. At ~100 requests/day (measured 2026-08-26..28), a
  metadata-only row was ~4-5 kB; with content a small request measured 13.5 kB,
  and a 132,929-prompt-token Claude Code call is roughly 0.5-1 MB. Worst case is
  therefore tens of MB/day on a shared 100Gi `ceph-block` PVC - not urgent, and
  not something to leave running for years either.
- **Do not go below 30 days.** `/global/spend/models`, `/global/spend/keys` and
  `MonthlyGlobalSpend*` are SQL views defined directly over
  `LiteLLM_SpendLogs` with a `startTime >= CURRENT_DATE - 30 days` filter, so a
  shorter window silently truncates those pages.
- Long-term cost history survives regardless: `/user/daily/activity` (the Admin
  UI Usage page) reads the `LiteLLM_Daily*Spend` rollup tables, and the pruner
  only touches `LiteLLM_SpendLogs` plus its tool-index table
  (`db/db_transaction_queue/spend_log_cleanup.py:480`).

Related knobs, all off/default and all optional: `maximum_spend_logs_cleanup_cron`
(cron instead of the default `1d` interval), `use_spend_logs_partitioning`
(drop partitions instead of deleting rows - requires converting the table with
upstream's `db_scripts/partition_spend_logs.sql` first), and
`maximum_autorouter_session_retention_period`.

---

## 8. Truncation - why the env var exists

`store_prompts_in_spend_logs` alone does not store the whole prompt. Both the
request body and the response go through
`_sanitize_request_body_for_spend_logs_payload`
(`proxy/spend_tracking/spend_tracking_utils.py:657`), which truncates every
string longer than `MAX_STRING_LENGTH_PROMPT_IN_DB` - **default 2048 characters**
(`litellm/constants.py:375`) - keeping 35% of the head and 65% of the tail with a
`litellm_truncated` marker between them.

The cap is per **string**, not per row, so a long many-turn conversation of short
messages is captured whole even at the default; it is single long strings (a
pasted file, a wiki page, repo source) that lose their middle. `litellmproxy.yaml`
raises it to `1000000`, which covers the largest call measured on this proxy with
headroom while still capping a runaway base64 image data URI.

If you see `litellm_truncated` in a stored body, the string exceeded even that.

---

## 9. Turning it back off

Remove `store_prompts_in_spend_logs` from `generalSettings` (or set it to
`false`) and let Flux reconcile. New rows go back to `"{}"` immediately.

Rows already written keep their content - retrieval decides per row, on actual
row content rather than on the current config
(`_resolve_request_response_payload`, `spend_management_endpoints.py:2683`).
Verified: the probe request below still returned its full prompt through
`/spend/logs/ui/{request_id}` after the flag was reverted. To actually erase
history you must delete the rows (or set a retention period and wait).

---

## 10. Verification evidence (2026-08-27)

Pre-change baseline, live DB: 134 of 134 rows had `messages`, `response` and
`proxy_server_request` all equal to `"{}"`. Only usage, cost, latency and
attribution were stored.

After enabling, one real request through `gemini-3.5-flash-lite` with a
4,683-character prompt (deliberately over the 2048-char default cap):

| Field | Value |
|---|---|
| `request_id` | `gen-1787882691-eK0dnnHsVU5MzNL03Rqx` |
| `model` / `model_group` | `openrouter/google/gemini-3.5-flash-lite` / `gemini-3.5-flash-lite` |
| `spend` | `0.0008539` USD |
| tokens | 2813 prompt / 4 completion |
| `length(proxy_server_request)` | 12,324 (was 2, i.e. `"{}"`, on every prior row) |
| `length(response)` | 1,224 (was 2) |
| prompt retrieved | 4,683 chars, all 140 filler repetitions present, **no** `litellm_truncated` marker |
| completion retrieved | `full content capture works` |

Retrieved identically through SQL and through `GET /spend/logs/ui/{request_id}`,
the endpoint the Admin UI row-expand calls.
