# Spend logs: content capture, retention, confidentiality, cost reading

Retrieval runbook (UI, API, SQL):
[`docs/ai-system/litellm/request-logs.md`](../../../../docs/ai-system/litellm/request-logs.md).

## Content capture

- `generalSettings.store_prompts_in_spend_logs: true` is the **only** gate
  (`should_store_prompts_and_responses_in_spend_logs` in
  `proxy/spend_tracking/spend_tracking_utils.py`; it also reads the
  `STORE_PROMPTS_IN_SPEND_LOGS` env var). Off, `messages`, `response` and
  `proxy_server_request` are all the literal `"{}"`.
- **The prompt is in `proxy_server_request`**, the completion in `response`.
  The `messages` column stays `"{}"` except for the realtime API.
- **`request_id` is the upstream provider's id** (`gen-...`, `chatcmpl-...`),
  not the `x-litellm-call-id` the proxy returns.
- **Truncation**: every string over `MAX_STRING_LENGTH_PROMPT_IN_DB`
  (default 2048 chars) is head/tail stitched with a `litellm_truncated`
  marker. The cap is per string, not per row. `litellmproxy.yaml` sets the env
  var to `1000000`, which covers the largest measured call with headroom and
  still caps a runaway base64 data URI. It is read at request time.
- **Do not add `store_model_in_db: true`**, even though the Admin UI hint
  bundles the two. Spend tracking never reads it; the UI only needs it to
  *write* settings to the DB. We declare the flag in config, so no DB write
  is involved, and `store_model_in_db: false` keeps the UI from overriding
  GitOps models and keys.
- Turning it off: remove the flag; new rows go back to `"{}"`. Existing rows
  keep their content until the pruner removes them.

## Retention: `maximum_spend_logs_retention_period: "30d"`

- That key is the gate for the built-in pruner: while null, no
  `spend_log_cleanup_job` is scheduled at all. Set, it runs daily.
- `"30d"` (exactly 2,592,000 s), not `"1mo"` (a calendar month, 28-31 days).
  It matches the `postgres-17` Barman `retentionPolicy: 30d`, so logged content
  does not outlive the backup window (captain decision
  `litellm-log-backup-export`, option C). CI pins both.
- Tradeoff: the pruner deletes whole `LiteLLM_SpendLogs` rows, so per-request
  cost history goes with the content. The durable `LiteLLM_Daily*Spend`
  rollups (Admin UI Usage, `/user/daily/activity`) are untouched. The
  last-30d SQL views can lose their oldest day at the boundary; accepted.
- Optional knobs, all off: `maximum_spend_logs_cleanup_cron`,
  `use_spend_logs_partitioning` (needs the table converted first).

## Confidentiality

- Primary store: the `litellm` DB on shared `postgres-17`. It leaves the
  cluster only through Barman to the LAN TrueNAS MinIO
  (`https://nas.${SECRET_DOMAIN}:9000`, 30d), which is on-premises and not
  replicated to R2. Captain-accepted as a sink for full-content logs.
- LiteLLM's own export paths stay closed: `cold_storage_custom_logger` is
  unset (no S3/GCS object key is minted), and `litellmSettings.callbacks` is
  `["prometheus"]` only (labels, never content). CI asserts prometheus-only.
- Caller credentials are not stored: `redact_credential_headers` masks
  `authorization`, `x-api-key`, `x-litellm-api-key`, `cookie` and similar to
  `***REDACTED***`, and the body snapshot excludes `secret_fields`. So the
  pass-through OAuth tokens are never persisted.
- Widened blast radius: anything holding the master key can read full
  prompts, and Prometheus holds it for the `/metrics` scrape.
- Sensitive traffic: `repo-wiki` (third-party repo source) and `opencode`
  (captain's workspace) are persisted verbatim for the retention window.

## Reading cost

- `spend` on a row is what LiteLLM charged the key. `metadata.cost_breakdown`
  splits input/output/cache when LiteLLM priced the call itself (every
  Anthropic row); it is `null` when the provider reported cost (OpenRouter:
  see `response.usage.cost`).
- Per model/consumer/day: Admin UI Usage (`/user/daily/activity`, rollups).
  Top models/keys: `/global/spend/models`, `/keys`, `/provider` (30-day views).
- Prometheus: `litellm_spend_metric_total`, `litellm_{input,output,total}_tokens_metric`;
  these series appear only after the first request since the last restart.
- Local spend is not money: only `qwen3.6-35b-a3b` is priced (the demo
  fixture), and its rows dominate the headline total. Old rows keep the
  prices they were written with. For real money filter
  `custom_llm_provider IN ('anthropic','openrouter','xai','zai')`.

## Trap: never call `GET /spend/logs` bare

Without `request_id`, an `api_key` or a narrow date range it walks the whole
table in-process and OOM-killed the proxy (twice, at the old 2Gi limit).
`GET /spend/logs` without `request_id` returns a per-day aggregate, not a
list; use `GET /spend/logs/ui` for the list and
`GET /spend/logs?request_id=<id>` or `/spend/logs/ui/<id>` for one row.
